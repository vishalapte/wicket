"""Ingest local ``.eml`` (a flat folder, or one file) into the manifest, additively.

The offline sibling of ``catalog`` + ``fetch``: it observes AND settles in one
pass from bodies already on disk (an Outlook / Apple Mail drag-export of RFC822
``.eml``), for a mailbox wicket cannot reach over IMAP.

**Additive, never destructive.** The source folder is a volatile inbox; the
archive is the durable record. A message already in the manifest (by the
provider-neutral ``message_key``) keeps its ``.eml`` and its row byte-for-byte;
only messages whose key is absent are filed. Nothing is ever deleted, so removing
a file from the source folder can never remove it from the archive. Re-running
with nothing new is a no-op.

Threads are reconstructed deterministically from headers (no subject-matching):
connected components over ``Message-ID`` <-> ``References``/``In-Reply-To``,
augmented by the Outlook ``Thread-Index`` conversation id. The filing domain is
the shared counterparty rule (`reconcile.compute_domain`) applied to a thread's
earliest message. When a thread already has an archived member, the new members
adopt that member's ``thread_id`` and ``domain`` so a reply never splits from its
thread or lands in a different folder.

No argparse, no CLI, no network: the CLI layer (`wicket.ingest.cli`) parses
argv and the api resolves paths; this worker computes and writes.
"""

from __future__ import annotations

import base64
import binascii
import email
import email.policy
import email.utils
import hashlib
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from wicket.domains import DOMAIN_RE, canonical_domain
from wicket.env import Identities
from wicket.manifest import Row, read_shard, shard_path, write_shard
from wicket.message import message_key, normalize_message_id
from wicket.reconcile import compute_domain

# The export origin, tagged into the fallback key when a message carries no
# ``Message-ID``. Kept as "outlook" for continuity with the existing store
# (whose fallback keys were minted that way); every real header still keys on
# its portable ``Message-ID`` and is unaffected.
_PROVIDER_TAG = "outlook"
UNFILED = "_unfiled"

# Resolves a recipient address to the account that owns it, or None when nothing
# does. Supplied by the api (`env.account_of` bound to the alias map and the
# stores that exist), so the worker stays free of both config files and the disk.
AccountFor = Callable[[str], str | None]

# Headers that say who a message was delivered to. `To` alone misses a message
# that reached you by Cc or through a forwarder, which is exactly the mail whose
# owning account is easiest to get wrong.
RECIPIENT_HEADERS = ("To", "Cc", "Delivered-To", "X-Original-To")


class UnionFind:
    """Minimal union-find over opaque string nodes for thread grouping."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, node: str) -> str:
        """Return the component root of ``node`` (path-halving as it walks)."""
        self.parent.setdefault(node, node)
        root = node
        while self.parent[root] != root:
            self.parent[root] = self.parent[self.parent[root]]
            root = self.parent[root]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _local_msgid(key: str) -> str:
    """Compact 16-hex archive filename for a portable key (Gmail X-GM-MSGID parity).

    Deterministic (same key -> same name, so re-runs are idempotent) and
    collision-safe at this scale; the full key stays the manifest ``id``.
    """
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _referenced_ids(msg: email.message.Message) -> list[str]:
    """Normalized message-ids this message replies to (References + In-Reply-To)."""
    out: list[str] = []
    for header in ("References", "In-Reply-To"):
        raw = str(msg.get(header, "") or "")
        for tok in raw.replace("<", " <").split():
            normalized = normalize_message_id(tok)
            if normalized:
                out.append(normalized)
    return out


def _conversation_key(msg: email.message.Message) -> str | None:
    """Outlook Thread-Index conversation id: hex of the shared leading 22 bytes."""
    raw = str(msg.get("Thread-Index", "") or "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw)
    except (ValueError, binascii.Error):
        return None
    return decoded[:22].hex() if len(decoded) >= 22 else None


def _domain_of(address: str) -> str | None:
    address = address.strip().lower()
    if "@" not in address:
        return None
    domain = address.split("@", 1)[1]
    return domain if domain and DOMAIN_RE.match(domain) else None


def _unfiled_fallback(
    to_addrs: list[str],
    account: str,
    identities: Identities,
    aliases: dict[str, str],
) -> str | None:
    """Owner rule: strip yourself from To; if one external domain remains, use it.

    "Yourself" is your own domain plus every address in ``identities`` — without
    the latter, a message to a burner address of yours leaves that burner's
    domain standing as the lone "external" one and files under it. The survivor is
    folded to its primary (``t.delta.com`` -> ``delta.com``), same as the main rule.
    """
    own = account.split("@", 1)[1] if "@" in account else ""
    external = {
        canonical_domain(d, aliases)
        for a in to_addrs
        if (d := _domain_of(a)) and d != own and a not in identities
    }
    return next(iter(external)) if len(external) == 1 else None


def _parse_eml(path: Path) -> dict[str, object]:
    """Parse one ``.eml`` into the fields threading, filing, and the row need."""
    raw = path.read_bytes()
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    message_id = str(msg.get("Message-ID", "") or "")
    native = hashlib.sha1(raw).hexdigest()
    key = message_key(message_id=message_id, provider=_PROVIDER_TAG, native_id=native)

    parsed: datetime | None = None
    date_header = msg.get("Date")
    if date_header:
        try:
            parsed = email.utils.parsedate_to_datetime(str(date_header))
        except (TypeError, ValueError):
            parsed = None
    if parsed is None:
        parsed = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    dt = parsed.astimezone(timezone.utc)

    from_header = str(msg.get("From", "") or "")
    _, from_addr = email.utils.parseaddr(from_header)
    to_addrs = sorted(
        {
            addr.strip().lower()
            for _, addr in email.utils.getaddresses([str(msg.get("To", "") or "")])
            if addr
        }
    )
    delivered = sorted(
        {
            addr.strip().lower()
            for header in RECIPIENT_HEADERS
            for _, addr in email.utils.getaddresses(msg.get_all(header, []))
            if addr
        }
    )
    return {
        "id": key,
        "raw": raw,
        "date": dt,
        "from_header": from_header,
        "from": from_addr.strip().lower(),
        "to": to_addrs,
        "rcpt": delivered,
        "subject": str(msg.get("Subject", "") or ""),
        "size": len(raw),
        "refs": _referenced_ids(msg),
        "conv": _conversation_key(msg),
    }


def _participants(
    messages: list[dict[str, object]], account_for: AccountFor
) -> Counter[str]:
    """Per account, how many of these messages it took part in — sent or received.

    ``account_for`` resolves an address to the account that owns it
    (`env.account_of`): the alias map first, then the clear line where an
    address names its own store. An address nothing claims counts for nothing, so
    this is a comparison between destinations that actually exist.

    **The sender counts, not only the recipients.** Mail *you sent* is addressed
    entirely to counterparties, so a recipients-only tally leaves every outbound
    thread ownerless — which is most of a correspondence you started.
    """
    tally: Counter[str] = Counter()
    for m in messages:
        rcpt: list[str] = m["rcpt"]  # type: ignore[assignment]
        addresses = [*rcpt, str(m["from"])]
        owners = {a for addr in addresses if (a := account_for(addr)) is not None}
        tally.update(owners)
    return tally


def check_account_matches(
    messages: list[dict[str, object]], account: str, account_for: AccountFor
) -> str | None:
    """Warn when ``account`` is not the account this folder was addressed to.

    ``--account`` is a *destination*, not a filter: it says which store to write,
    and nothing about the folder. Naming the wrong one files the whole export
    into the wrong mailbox, silently and additively. So compare the folder's
    recipients against the target and refuse when another known account plainly
    owns this mail. Returns the complaint, or None when the target is defensible
    (it leads, it ties, or the alias map is too thin to tell).
    """
    tally = _participants(messages, account_for)
    if not tally:
        return None
    leader, top = tally.most_common(1)[0]
    mine = tally.get(account, 0)
    if mine >= top:
        return None
    counts = ", ".join(f"{acct} ({n})" for acct, n in tally.most_common())
    return (
        f"account mismatch: {mine} of {len(messages)} message(s) are addressed to "
        f"{account}, but {top} are addressed to {leader}. Recipients here: {counts}. "
        f"--account names the store to write into, it does not filter the folder, "
        f"so this would file mail belonging to {leader} into {account}. Re-run with "
        f"--account {leader}, or pass --force if you meant it."
    )


def _assign_threads(messages: list[dict[str, object]]) -> dict[str, str]:
    """Return {id -> thread_id} via References/In-Reply-To + Thread-Index.

    ``thread_id`` is the earliest member's id in its connected component.
    """
    uf = UnionFind()
    for m in messages:
        mid = str(m["id"])
        uf.find(mid)
        for ref in m["refs"]:  # type: ignore[attr-defined]
            uf.union(mid, ref)
        if m["conv"]:
            uf.union(mid, f"tidx:{m['conv']}")

    components: dict[str, list[dict[str, object]]] = {}
    for m in messages:
        components.setdefault(uf.find(str(m["id"])), []).append(m)

    thread_of: dict[str, str] = {}
    for members in components.values():
        root = min(members, key=lambda m: (m["date"], str(m["id"])))
        thread_id = str(root["id"])
        for m in members:
            thread_of[str(m["id"])] = thread_id
    return thread_of


def _computed(members: list[dict[str, object]], target: Target) -> str | None:
    """Filing domain from the thread's earliest message that resolves to one.

    ``target.domain_aliases`` folds a marketing subdomain onto its primary
    (``t.delta.com`` -> ``delta.com``), so a bucket does not fragment into one
    folder per campaign host. `fetch` has always done this; ingest passing ``{}``
    was the divergence.
    """
    account, identities, aliases = (
        target.account,
        target.identities,
        target.domain_aliases,
    )
    ordered = sorted(members, key=lambda m: (m["date"], str(m["id"])))
    for m in ordered:
        domain = compute_domain(
            str(m["from_header"]),
            ", ".join(m["to"]),  # type: ignore[arg-type]
            account,
            aliases,
            identities,
        )
        if domain is None:
            to: list[str] = m["to"]  # type: ignore[assignment]
            domain = _unfiled_fallback(to, account, identities, aliases)
        if domain is not None:
            return domain
    return None


def route_threads(
    members_by_thread: dict[str, list[dict[str, object]]],
    account_for: AccountFor,
    route_for: AccountFor = lambda _: None,
) -> tuple[dict[str, list[str]], list[str]]:
    """Assign each thread to the store it belongs in.

    **The thread is the routing unit, not the message.** A conversation where you
    were addressed on some replies and merely cc'd on others would otherwise
    split across two stores, breaking the one invariant the archive relies on:
    a thread has one ``thread_id`` and one folder. So every member votes, and the
    whole thread files where the vote lands.

    Two rules, and **the mailbox wins**:

    1. ``account_for`` — the account that took part in the thread, by identity.
    2. ``route_for`` — the counterparty map (`domains.load_domain_routes`), used
       only when no mailbox claims the thread: an order to a burner, a catch-all
       subdomain, a drag-export of a mailbox wicket cannot reach.

    The order matters and it is not a preference. A manifest is the observation
    record *of a mailbox*: `catalog` sweeps Gmail and writes what it sees into
    Gmail's manifest. If a counterparty rule could pull a Gmail-owned message into
    ``shopping``, the next catalog would re-observe it in the mailbox and write it
    back into Gmail's manifest, and the same message would live in two stores
    permanently. So a bucket is the *physical* home only for mail no mailbox owns;
    topical grouping of mailbox-owned mail is a view over it, never a relocation.

    Either resolver only ever names a destination that has a store, so a bucket
    you have not created yet is not a destination and its mail stays unrouted
    rather than minting a directory. Ties break toward the destination named on
    the thread's earliest message, then alphabetically, so a rerun always lands
    the same way.

    Returns ``({destination: [thread_id, ...]}, [unroutable thread_id, ...])``.
    """
    routed: dict[str, list[str]] = {}
    unrouted: list[str] = []
    for tid, members in members_by_thread.items():
        earliest = min(members, key=lambda m: (m["date"], str(m["id"])))
        for resolve in (account_for, route_for):
            scored = _participants(members, resolve)
            if scored:
                first_choice = set(_participants([earliest], resolve))
                routed.setdefault(_winner(scored, first_choice), []).append(tid)
                break
        else:
            unrouted.append(tid)
    return routed, unrouted


def _winner(scored: Counter[str], first_choice: set[str]) -> str:
    """Most members addressed, then the earliest message's account, then the name."""
    return min(scored, key=lambda a: (-scored[a], a not in first_choice, a))


def _dedup_folder(
    files: list[Path],
) -> tuple[list[dict[str, object]], int, dict[str, list[Path]]]:
    """Parse every file, keeping the first of each portable id; count the rest.

    Also returns every source file per id, including the duplicates dropped here:
    two files holding the same message are both redundant once it is archived, so
    both are eligible for the trash.
    """
    seen: dict[str, dict[str, object]] = {}
    files_by_id: dict[str, list[Path]] = {}
    dup = 0
    for path, parsed in ((p, _parse_eml(p)) for p in files):
        mid = str(parsed["id"])
        files_by_id.setdefault(mid, []).append(path)
        if mid in seen:
            dup += 1
        else:
            seen[mid] = parsed
    return list(seen.values()), dup, files_by_id


def trash(paths: list[Path], trash_dir: Path | None = None) -> int:
    """Move source files to the macOS Trash, never delete them. Returns the count.

    Only ever called for a message that is *durably in a store* (filed by this
    run, or already archived): the archive is the record, the source folder is a
    volatile inbox, and the Trash is recoverable if that judgment is ever wrong.
    An unroutable message keeps its file, so nothing leaves the folder without a
    home to go to. Name collisions in the Trash get a numeric suffix rather than
    clobbering whatever is already there.
    """
    dest = trash_dir or (Path.home() / ".Trash")
    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in paths:
        target = dest / path.name
        n = 2
        while target.exists():
            target = dest / f"{path.stem} {n}{path.suffix}"
            n += 1
        shutil.move(str(path), str(target))
        moved += 1
    return moved


def _load_existing(store_dir: Path) -> dict[str, Row]:
    """Every row already in the store, id -> row, across all year shards."""
    existing: dict[str, Row] = {}
    for shard in sorted(store_dir.glob("*.jsonl")) if store_dir.exists() else []:
        existing.update(read_shard(shard))
    return existing


def _file_plan(
    members_by_thread: dict[str, list[dict[str, object]]],
    existing: dict[str, Row],
    target: Target,
) -> dict[str, tuple[str, str]]:
    """Per folder-thread, the (thread_id, domain) new members file under.

    Reuse an archived member's thread_id and domain so a new reply stays joined
    to its thread and lands in the same folder; else compute both.

    ``target.filing_domain`` (the ``--domain`` override) stands in for the
    computed counterparty, so a folder of mail with no obvious counterparty gets
    one home rather than scattering into ``_unfiled``. It ranks *below* an
    archived anchor on purpose: forcing a domain must never tear a reply out of
    the folder its thread already lives in.
    """
    plan: dict[str, tuple[str, str]] = {}
    for tid, members in members_by_thread.items():
        archived = [existing[str(m["id"])] for m in members if str(m["id"]) in existing]
        forced = target.filing_domain
        if archived:
            anchor = min(archived, key=lambda r: str(r.get("date", "")))
            final_tid = str(anchor.get("thread_id", tid))
            domain = str(
                anchor.get("domain") or forced or _computed(members, target) or UNFILED
            )
        else:
            final_tid = tid
            domain = forced or _computed(members, target) or UNFILED
        plan[tid] = (final_tid, domain)
    return plan


def _row_for(
    m: dict[str, object],
    final_tid: str,
    domain: str,
    labels: list[str] | None = None,
) -> tuple[Row, str]:
    """Build the manifest row and archive rel-path for one new message.

    ``labels`` is the ``--tags`` payload: a flat ``.eml`` export carries no
    provider folders, so ``labels`` is ``None`` unless the owner names tags.
    """
    mid = str(m["id"])
    mgid = _local_msgid(mid)
    dt: datetime = m["date"]  # type: ignore[assignment]
    rel = f"{domain}/{dt.strftime('%Y-%m')}/{mgid}.eml"
    row: Row = {
        "id": mid,
        "msgid": mgid,
        "thread_id": final_tid,
        "date": dt.isoformat(),
        "from": m["from"],
        "to": m["to"],
        "subject": m["subject"],
        "size": m["size"],
        "labels": labels,  # --tags, or None for a bare .eml export
        "deleted": False,
        "downloaded": True,
        "domain": domain,
        "path": rel,
    }
    return row, rel


def _commit(
    new_by_year: dict[int, dict[str, Row]],
    to_write: dict[str, tuple[str, bytes]],
    store_dir: Path,
    archive_dir: Path,
) -> None:
    """Copy new bodies, then union new rows into each year shard (additive only).

    A rewritten shard's pre-existing rows serialize byte-identically (same
    deterministic write_shard), so nothing already archived is disturbed.
    """
    for rel, raw in to_write.values():
        target = archive_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(raw)
    for year, rows in new_by_year.items():
        path = shard_path(store_dir, year)
        merged = read_shard(path)
        for mid, row in rows.items():
            if mid not in merged:  # never overwrite an existing row
                merged[mid] = row
        write_shard(path, merged)


@dataclass(frozen=True)
class Target:
    """Where an ingest writes, and who the owner is there.

    The first five values always travel together: the filing rule needs to know
    which addresses are *yours* (``identities``) before it can say who the
    counterparty is, and an account is meaningless without the store it writes to.

    ``filing_domain`` and ``labels`` are the two optional write-overrides (the
    ``--domain`` / ``--tags`` flags): a domain every newly-filed message goes
    under instead of its computed counterparty, and tags recorded as each new
    row's ``labels``. Both touch only mail filed *this run* — an already-archived
    row (and a reply that joins its thread) is left byte-for-byte alone.
    """

    store_dir: Path
    archive_dir: Path
    account: str
    identities: Identities  # env.identities(): a test, not a set (wildcards)
    domain_aliases: dict[str, str]  # domains.load_domain_aliases(): subdomain folding
    filing_domain: str | None = None  # --domain: forced filing domain override
    labels: list[str] | None = None  # --tags: recorded as each new row's labels


def _new_rows(
    messages: list[dict[str, object]],
    existing: dict[str, Row],
    thread_of: dict[str, str],
    plan: dict[str, tuple[str, str]],
    labels: list[str] | None = None,
) -> tuple[
    dict[int, dict[str, Row]],
    dict[str, tuple[str, bytes]],
    Counter[str],
    dict[str, list[str]],
]:
    """Rows by year, bodies to write, the domain tally, and the filed-file index.

    The last return is ``{archive directory: [filename, ...]}`` — the
    ``<domain>/YYYY-MM`` folders touched this run and the ``.eml`` filed under
    each, name-sorted. It is what the face reports so the owner can see exactly
    what landed where.
    """
    new_by_year: dict[int, dict[str, Row]] = {}
    to_write: dict[str, tuple[str, bytes]] = {}  # id -> (rel_path, raw)
    domain_counter: Counter[str] = Counter()
    filed: dict[str, list[str]] = {}
    for m in messages:
        mid = str(m["id"])
        if mid in existing:
            continue  # already archived: untouched
        final_tid, domain = plan[thread_of[mid]]
        row, rel = _row_for(m, final_tid, domain, labels)
        dt: datetime = m["date"]  # type: ignore[assignment]
        new_by_year.setdefault(dt.year, {})[mid] = row
        to_write[mid] = (rel, m["raw"])  # type: ignore[assignment]
        domain_counter[domain] += 1
        directory, _, name = rel.rpartition("/")
        filed.setdefault(directory, []).append(name)
    for names in filed.values():
        names.sort()
    return new_by_year, to_write, domain_counter, filed


def source_files(src: Path) -> list[Path]:
    """The ``.eml`` ``src`` names: a flat folder's, or the one file it points at.

    A single message is a folder of one, so ``src`` takes either and every caller
    downstream sees the same list. Raises ``FileNotFoundError`` when the path does
    not exist or holds no ``.eml``, and ``ValueError`` when it is a file that is
    not one (pointing at a ``.pdf`` should say so, not file an empty message).
    """
    if src.is_dir():
        files = sorted(src.glob("*.eml"))
        if not files:
            raise FileNotFoundError(f"no .eml files under {src}")
        return files
    if not src.exists():
        raise FileNotFoundError(f"no such file or folder: {src}")
    if src.suffix.lower() != ".eml":
        raise ValueError(f"not a .eml file: {src}")
    return [src]


def ingest_folder(
    *,
    src: Path,
    target: Target,
    account_for: AccountFor,
    dry_run: bool = False,
    force: bool = False,
    to_trash: bool = False,
) -> dict[str, object]:
    """File the new ``.eml`` at ``src`` into the store + archive, additively.

    ``src`` is a flat folder of ``.eml`` or a single ``.eml``. Returns a stats
    dict for the face to render. Raises ``FileNotFoundError`` when ``src`` holds
    no ``.eml``, and ``ValueError`` when the source is addressed to a different
    account than ``target.account`` (unless ``force``).
    """
    files = source_files(src)
    messages, dup, files_by_id = _dedup_folder(files)
    complaint = check_account_matches(messages, target.account, account_for)
    if complaint and not force:
        raise ValueError(complaint)

    thread_of = _assign_threads(messages)
    stats = _file_into(target, messages, thread_of, dry_run=dry_run)
    durable: set[str] = stats.pop("durable")  # type: ignore[assignment]
    # Already-archived messages are durable too, so a re-run with nothing new to
    # add still clears its source files.
    trashable = (
        sorted(p for mid in durable for p in files_by_id[mid]) if to_trash else []
    )
    stats["warning"] = complaint  # set only when --force overrode a mismatch
    stats["files"] = len(files)
    stats["unique"] = len(messages)
    stats["dup"] = dup
    stats["unrouted"] = 0  # a single-target run files everything it parsed
    stats["trashable"] = len(trashable)
    stats["trashed"] = trash(trashable) if to_trash and not dry_run else 0
    return stats


def _file_into(
    target: Target,
    messages: list[dict[str, object]],
    thread_of: dict[str, str],
    *,
    dry_run: bool,
) -> dict[str, object]:
    """File ``messages`` into one store, additively. The single-destination step.

    ``messages`` is either the whole folder (single-target ingest) or one
    account's share of it (routed ingest); either way it is whole threads, so the
    filing plan sees every member of every thread it decides on.
    """
    existing = _load_existing(target.store_dir)

    members_by_thread: dict[str, list[dict[str, object]]] = {}
    for m in messages:
        members_by_thread.setdefault(thread_of[str(m["id"])], []).append(m)
    plan = _file_plan(members_by_thread, existing, target)
    new_by_year, to_write, domain_counter, filed = _new_rows(
        messages, existing, thread_of, plan, target.labels
    )

    stats: dict[str, object] = {
        "account": target.account,
        "added": len(to_write),
        # Parsed, but already in this store: left byte-for-byte alone. Counted
        # because it is the whole gap between what a run reads and what it adds,
        # and without it a run that trashed every source file while adding fewer
        # rows than it read reads like loss.
        "skipped": len(messages) - len(to_write),
        "unfiled": domain_counter.get(UNFILED, 0),
        "added_by_year": {y: len(rows) for y, rows in sorted(new_by_year.items())},
        "top_domains": domain_counter.most_common(10),
        # {archive dir -> [.eml filename, ...]}: what this run filed and where.
        "filed": filed,
        # Durably in this store once the commit lands: filed now, or already
        # archived. These are the messages whose source files may be trashed.
        "durable": {str(m["id"]) for m in messages},
    }
    if dry_run or not to_write:
        return stats

    _commit(new_by_year, to_write, target.store_dir, target.archive_dir)
    return stats


def _merge_filed(per_account: list[dict[str, object]]) -> dict[str, list[str]]:
    """One filed-file index (``{dir: [name, ...]}``) across every routed account.

    Filenames are per-message unique, so folding same-named dirs from different
    stores together never drops one, and a caller sees the same ``filed`` shape
    whichever mode ran.
    """
    filed: dict[str, list[str]] = {}
    for got in per_account:
        for directory, names in got["filed"].items():  # type: ignore[attr-defined]
            filed.setdefault(directory, []).extend(names)
    for names in filed.values():
        names.sort()
    return filed


def ingest_routed(
    *,
    src: Path,
    targets: dict[str, Target],
    account_for: AccountFor,
    route_for: AccountFor = lambda _: None,
    dry_run: bool = False,
    to_trash: bool = False,
) -> dict[str, object]:
    """File a mixed source into whichever store each *thread* belongs to.

    ``src`` is a flat folder of ``.eml`` or a single ``.eml``. ``targets`` is
    keyed by account and holds only accounts with a real store, so an account that
    exists solely in the alias map is never a destination. A thread nobody claims
    is left entirely alone: not filed, and its source files are not trashed. With
    ``to_trash``, the source files of every message that is durably in a store are
    moved to the Trash; the unrouted keep theirs.
    """
    files = source_files(src)
    messages, dup, files_by_id = _dedup_folder(files)
    thread_of = _assign_threads(messages)

    members_by_thread: dict[str, list[dict[str, object]]] = {}
    for m in messages:
        members_by_thread.setdefault(thread_of[str(m["id"])], []).append(m)
    routed, unrouted = route_threads(members_by_thread, account_for, route_for)

    per_account: list[dict[str, object]] = []
    durable: set[str] = set()
    for account in sorted(routed):
        share = [m for tid in routed[account] for m in members_by_thread[tid]]
        got = _file_into(targets[account], share, thread_of, dry_run=dry_run)
        filed: set[str] = got.pop("durable")  # type: ignore[assignment]
        durable |= filed
        per_account.append(got)

    stranded = [m for tid in unrouted for m in members_by_thread[tid]]
    trashable = (
        sorted(p for mid in durable for p in files_by_id[mid]) if to_trash else []
    )
    stats: dict[str, object] = {
        "files": len(files),
        "unique": len(messages),
        "dup": dup,
        "accounts": per_account,
        "added": sum(int(a["added"]) for a in per_account),  # type: ignore[call-overload]
        "skipped": sum(int(a["skipped"]) for a in per_account),  # type: ignore[call-overload]
        "filed": _merge_filed(per_account),
        "unrouted": len(stranded),
        "unrouted_threads": len(unrouted),
        "trashable": len(trashable),
        "trashed": 0,
    }
    if dry_run:
        return stats

    stats["trashed"] = trash(trashable) if to_trash else 0
    return stats
