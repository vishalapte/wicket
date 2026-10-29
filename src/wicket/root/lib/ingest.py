"""File ``.eml`` into the manifest and archive, additively: the only verb that files.

Two sources: a local export (``ingest_folder`` / ``ingest_routed``: an Outlook /
Apple Mail drag-export of RFC822 ``.eml``, for a mailbox IMAP cannot reach), and what
``fetch`` got into a store's ``staging/`` (``ingest_staged``).

**Additive, never destructive.** The source folder is a volatile inbox; the
archive is the durable record. A message already archived (its row, by the
provider-neutral ``message_key``, is downloaded and its ``.eml`` is on disk) keeps
both byte-for-byte. A message with no row is filed with a new row; one whose row
exists without a file on disk (a ``catalog`` observation never downloaded) is
filed and its file recorded on that row, the observation untouched. Nothing is
ever deleted, so removing a file from the source folder can never remove it from
the archive. Re-running with nothing new is a no-op.

A local export's threads are reconstructed deterministically from headers
(`message.assign_threads`, no subject-matching); a staged message carries its
provider's thread id. The folder is `wicket.filing`'s rank (anchor, ``--domain``, self, sent,
computed domain, fallback, ``_unfiled``) over the thread's opener, and each new row
records which rank won as ``filed``. Only an archived member anchors: when a thread
already has one, new members join its folder so a reply never splits from its
thread. A message whose row the owner marked ``excluded`` is skipped and counted.

No argparse, no CLI, no network: the CLI (`wicket.__main__`) parses argv and
the api (`wicket.root.api`) resolves paths; this worker computes and writes. A store is
written only under its lock (`env.store_lock`), taken here because only the worker
knows which stores a routed run writes: a single-target run raises `env.StoreBusy`
when its store is locked; a routed run takes each destination's lock in name order
and skips, and names, a busy one. A dry run writes nothing and takes no lock.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import hashlib
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from wicket.env import Identities, StoreBusy, store_lock
from wicket.filing import (
    UNFILED,
    AccountFor,
    Owner,
    anchor_folder,
    file_thread,
    opener,
    participants,
    route_threads,
)
from wicket.manifest import (
    Row,
    eml_on_disk,
    merge_settlement,
    read_shard,
    shard_path,
    staged_files,
    write_eml,
    write_shard,
)
from wicket.message import (
    KeyRequest,
    MailRow,
    assign_threads,
    conversation_key,
    local_msgid,
    mailbox_keys,
    message_id_of,
    message_key,
    referenced_ids,
    sent_date,
)
from wicket.provider import Provider, parse_staged
from wicket.reconcile import reconcile

# The export origin, tagged into the fallback key when a message carries no
# ``Message-ID``. Kept as "outlook" for continuity with the existing store
# (whose fallback keys were minted that way); every real header still keys on
# its portable ``Message-ID`` and is unaffected.
_PROVIDER_TAG = "outlook"

# Headers that say who a message was delivered to. `To` alone misses a message
# that reached you by Cc or through a forwarder, which is exactly the mail whose
# owning account is easiest to get wrong.
RECIPIENT_HEADERS = ("To", "Cc", "Delivered-To", "X-Original-To")


def _parse_eml(path: Path) -> dict[str, object]:
    """Parse one ``.eml`` into the fields threading, filing, and the row need."""
    raw = path.read_bytes()
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    message_id = message_id_of(raw)
    native = hashlib.sha1(raw).hexdigest()
    key = message_key(message_id=message_id, provider=_PROVIDER_TAG, native_id=native)

    sent = sent_date(msg.get("Date"))
    dt = sent or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

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
        "message_id": message_id,
        "sent": sent,
        "raw": raw,
        "date": dt,
        "from_header": from_header,
        "from": from_addr.strip().lower(),
        "to": to_addrs,
        "rcpt": delivered,
        "subject": str(msg.get("Subject", "") or ""),
        "size": len(raw),
        "refs": referenced_ids(msg),
        "conv": conversation_key(msg),
    }


def check_account_matches(
    messages: list[dict[str, object]], account: str, account_for: AccountFor
) -> str | None:
    """Warn when ``account`` is not the account this folder was addressed to.

    ``--mail-account`` is a *destination*, not a filter: it says which store to write,
    and nothing about the folder. Naming the wrong one files the whole export
    into the wrong mailbox, silently and additively. So compare the folder's
    recipients against the target and refuse when another known account plainly
    owns this mail. Returns the complaint, or None when the target is defensible
    (it leads, it ties, or the alias map is too thin to tell).
    """
    tally = participants(messages, account_for)
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
        f"--mail-account names the store to write into, it does not filter the "
        f"folder, so this would file mail belonging to {leader} into {account}. "
        f"Re-run with --mail-account {leader}, or pass --force if you meant it."
    )


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


@dataclass(frozen=True)
class Existing:
    """What a store already holds, as filing needs it.

    ``rows`` is every row by id (a later shard wins an id seen twice). ``held`` is
    the ids with a copy that is archived: downloaded, its ``.eml`` on disk; an id
    in two shards is held when either copy is. ``shard_of`` is the year shard of
    each row, so a file recorded on an existing row lands in that row's shard.
    ``by_thread``: every row by ``thread_id``, where anchors and openers are found.
    """

    rows: dict[str, Row]
    held: set[str]
    shard_of: dict[str, int]
    by_thread: dict[str, list[Row]] = field(default_factory=dict)


def _load_existing(store_dir: Path, archive_dir: Path) -> Existing:
    """Every row already in the store, which are archived, their shards and threads."""
    rows: dict[str, Row] = {}
    held: set[str] = set()
    shard_of: dict[str, int] = {}
    for shard in sorted(store_dir.glob("*.jsonl")) if store_dir.exists() else []:
        for id_, row in read_shard(shard).items():
            rows[id_] = row
            if shard.stem.isdigit():
                shard_of[id_] = int(shard.stem)
            if row.get("downloaded") and eml_on_disk(row, archive_dir):
                held.add(id_)
    by_thread: dict[str, list[Row]] = {}
    for row in rows.values():
        if row.get("thread_id"):
            by_thread.setdefault(str(row["thread_id"]), []).append(row)
    return Existing(rows=rows, held=held, shard_of=shard_of, by_thread=by_thread)


@dataclass(frozen=True)
class Target:
    """Where an ingest writes, and who the owner is there.

    The first five values always travel together: the filing rule needs to know
    which addresses are *yours* (``identities``) before it can say who the
    counterparty is, and an account is meaningless without the store it writes to.

    ``filing_domain`` and ``labels`` are the two optional write-overrides (the
    ``--domain`` / ``--tag`` flags): a domain every newly-filed message goes
    under instead of its computed counterparty, and tags recorded as each new
    row's ``labels``. Both touch only mail filed *this run* — an already-archived
    row (and a reply that joins its thread) is left byte-for-byte alone, and a
    row that was only observed keeps its own ``labels`` when its file is filed.
    """

    store_dir: Path
    archive_dir: Path
    account: str
    identities: Identities  # env.identities(): a test, not a set (wildcards)
    domain_aliases: dict[str, str]  # domains.load_domain_aliases(): subdomain folding
    filing_domain: str | None = None  # --domain: forced filing domain override
    labels: list[str] | None = None  # --tag: recorded as each new row's labels

    @property
    def owner(self) -> Owner:
        """Who is *you* in this store, for `wicket.filing`'s rules."""
        return Owner(self.account, self.identities, self.domain_aliases)


@dataclass(frozen=True)
class Placed:
    """Where one thread files: its final thread id, folder, and the rank that won."""

    thread_id: str
    folder: str
    filed: str | None


def _as_row(m: dict[str, object]) -> Row:
    """A parsed message in a row's shape, so it can be a thread's opener."""
    dt: datetime = m["date"]  # type: ignore[assignment]
    return {
        "id": m["id"],
        "date": dt.isoformat(),
        "from": m["from_header"],
        "to": m["to"],
    }


def _place_threads(
    members_by_thread: dict[str, list[dict[str, object]]],
    existing: Existing,
    target: Target,
    *,
    staged: bool = False,
) -> dict[str, Placed]:
    """Per thread of ``members_by_thread``, where its new members file (`filing.file_thread`).

    A staged thread keeps its provider's thread id; a local one adopts that of its earliest
    member the store has (an archived one first). Only an archived row anchors
    (split plan rule 3). The opener is the earliest observed row among the store's
    rows of the thread and the new members, the store's copy of a member first.
    """
    plan: dict[str, Placed] = {}
    for tid, members in members_by_thread.items():
        known = [
            existing.rows[i] for m in members if (i := str(m["id"])) in existing.rows
        ]
        final_tid = tid
        if not staged:
            first_known = opener(
                [r for r in known if str(r["id"]) in existing.held] or known
            )
            if first_known is not None:
                final_tid = str(first_known.get("thread_id") or tid)
        thread_rows = existing.by_thread.get(final_tid, [])
        anchor = opener(
            r for r in [*known, *thread_rows] if str(r["id"]) in existing.held
        )
        candidates: dict[str, Row] = {}
        for row in [*known, *thread_rows]:
            if "from" in row:
                candidates.setdefault(str(row["id"]), row)
        for m in members:
            candidates.setdefault(str(m["id"]), _as_row(m))
        folder, filed = file_thread(
            anchor=anchor,
            forced=target.filing_domain,
            first=opener(candidates.values()),
            owner=target.owner,
        )
        plan[tid] = Placed(final_tid, folder, filed)
    return plan


def _row_for(
    m: dict[str, object],
    placed: Placed,
    labels: list[str] | None = None,
) -> tuple[Row, str]:
    """Build the manifest row and archive rel-path for one new message.

    Named by a staged message's provider (``stem``: Gmail's msgid, a digest of
    Fastmail's), else `message.local_msgid` of the key. ``labels`` is the ``--tag``
    payload (a flat export carries no provider folders, so ``None`` unless the owner
    names tags).
    """
    mid = str(m["id"])
    mgid = str(m.get("msgid") or local_msgid(mid))
    dt: datetime = m["date"]  # type: ignore[assignment]
    rel = f"{placed.folder}/{dt.strftime('%Y-%m')}/{m.get('stem') or mgid}.eml"
    row: Row = {
        "id": mid,
        "msgid": mgid,
        "thread_id": placed.thread_id,
        "date": dt.isoformat(),
        "from": m["from"],
        "to": m["to"],
        "subject": m["subject"],
        "size": m["size"],
        "labels": labels,  # --tag, or None for a bare .eml export
        "deleted": False,
        "downloaded": True,
        "domain": placed.folder,
        "path": rel,
    }
    if placed.filed:
        row["filed"] = placed.filed
    return row, rel


def _commit(
    new_by_year: dict[int, dict[str, Row]],
    to_write: dict[str, tuple[str, bytes]],
    store_dir: Path,
    archive_dir: Path,
) -> None:
    """Merge rows into each year shard, then write the new bodies (additive only).

    ``new_by_year`` holds a whole row for a message the store had no row for, and
    only the file fields for one it had observed; ``merge_settlement`` adds the
    first and merges the second into its row. A rewritten shard's other rows
    serialize byte-identically (same deterministic write_shard), so nothing
    already archived is disturbed. Rows go first: after a crash before a body lands,
    the next run restores the body at its row's path from the source left in place.
    """
    for year, rows in new_by_year.items():
        path = shard_path(store_dir, year)
        write_shard(path, merge_settlement(read_shard(path), rows))
    for rel, raw in to_write.values():
        target = archive_dir / rel
        if not target.exists():
            write_eml(target, raw)


@dataclass
class _Filed:
    """What one filing pass decided, before anything is written."""

    new_by_year: dict[int, dict[str, Row]] = field(default_factory=dict)
    to_write: dict[str, tuple[str, bytes]] = field(
        default_factory=dict
    )  # id -> (rel, raw)
    domains: Counter[str] = field(default_factory=Counter)
    by_rule: Counter[str] = field(default_factory=Counter)
    index: dict[str, list[str]] = field(default_factory=dict)  # dir -> [file name]


def _new_rows(
    messages: list[dict[str, object]],
    existing: Existing,
    thread_of: dict[str, str],
    plan: dict[str, Placed],
    labels: list[str] | None = None,
) -> _Filed:
    """Rows by year, bodies to write, the domain and rank tallies, and the filed-file index.

    The index (``{<folder>/YYYY-MM: [filename, ...]}``, name-sorted) is what the face
    reports. A held message is skipped. A message with a row but no file gets only
    the file fields (``path``, ``downloaded``, ``domain``, and ``filed`` when a rank
    placed it) in that row's own shard, its observation untouched. A row naming a
    ``path`` whose file is gone is restored there, its ``filed`` unchanged.
    """
    out = _Filed()
    for m in messages:
        mid = str(m["id"])
        if mid in existing.held:
            continue  # already archived: untouched
        placed = plan[thread_of[mid]]
        row, rel = _row_for(m, placed, labels)
        dt: datetime = m["date"]  # type: ignore[assignment]
        year = dt.year
        prior = existing.rows.get(mid)
        if prior is not None:  # observed, not archived: record the file on its row
            rel = str(prior.get("path") or rel)
            row = {"downloaded": True, "path": rel, "domain": rel.split("/", 1)[0]}
            if not prior.get("path") and placed.filed:
                row["filed"] = placed.filed
            year = existing.shard_of.get(mid, year)
        out.new_by_year.setdefault(year, {})[mid] = row
        out.to_write[mid] = (rel, m["raw"])  # type: ignore[assignment]
        out.domains[str(row["domain"])] += 1
        if row.get("filed"):
            out.by_rule[str(row["filed"])] += 1
        directory, _, name = rel.rpartition("/")
        out.index.setdefault(directory, []).append(name)
    for names in out.index.values():
        names.sort()
    return out


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
    no ``.eml``, ``ValueError`` when the source is addressed to a different
    account than ``target.account`` (unless ``force``), and `env.StoreBusy` when
    another process holds the store's lock.
    """
    files = source_files(src)
    messages, dup, files_by_id = _dedup_folder(files)
    complaint = check_account_matches(messages, target.account, account_for)
    if complaint and not force:
        raise ValueError(complaint)

    thread_of = assign_threads(messages)
    stats = _file_under_lock(target, messages, thread_of, dry_run=dry_run)
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


def _file_under_lock(
    target: Target,
    messages: list[dict[str, object]],
    thread_of: dict[str, str],
    *,
    dry_run: bool,
) -> dict[str, object]:
    """`_file_into`, holding the store's lock unless this is a dry run (which writes nothing).

    Raises `env.StoreBusy` when another process holds the lock.
    """
    if dry_run:
        return _file_into(target, messages, thread_of, dry_run=True)
    with store_lock(target.store_dir):
        return _file_into(target, messages, thread_of, dry_run=False)


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
    filing plan sees every member of every thread it decides on. A message whose
    row the owner marked ``excluded`` is skipped and counted (plan rule 14): it is
    not filed, and its source file is not trashed.
    """
    existing = _load_existing(target.store_dir, target.archive_dir)
    excluded = {
        mid
        for m in messages
        if existing.rows.get(mid := str(m["id"]), {}).get("excluded")
    }
    kept = [m for m in messages if str(m["id"]) not in excluded]

    members_by_thread: dict[str, list[dict[str, object]]] = {}
    for m in kept:
        members_by_thread.setdefault(thread_of[str(m["id"])], []).append(m)
    plan = _place_threads(members_by_thread, existing, target)
    filed = _new_rows(kept, existing, thread_of, plan, target.labels)
    stats = _filed_stats(target, filed)
    # Parsed, but already in this store: left byte-for-byte alone. Counted because
    # without it a run that trashed every source file while adding fewer rows than
    # it read reads like loss.
    stats["skipped"] = len(kept) - len(filed.to_write)
    stats["excluded"] = len(excluded)
    # Durably in this store once the commit lands: filed now, or already
    # archived. These are the messages whose source files may be trashed.
    stats["durable"] = {str(m["id"]) for m in kept}
    if dry_run or not filed.to_write:
        return stats

    _commit(filed.new_by_year, filed.to_write, target.store_dir, target.archive_dir)
    return stats


def _filed_stats(target: Target, filed: _Filed) -> dict[str, object]:
    """The counts every filing run reports, whichever source it read."""
    return {
        "account": target.account,
        "added": len(filed.to_write),
        "unfiled": filed.domains.get(UNFILED, 0),
        "added_by_year": {
            y: len(rows) for y, rows in sorted(filed.new_by_year.items())
        },
        "top_domains": filed.domains.most_common(10),
        # Which rank placed each new row (`wicket.filing`): rule, forced, self, ...
        "by_rule": dict(sorted(filed.by_rule.items())),
        # {archive dir -> [.eml filename, ...]}: what this run filed and where.
        "filed": filed.index,
    }


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
    moved to the Trash; the unrouted keep theirs. A store whose lock another
    process holds is skipped: its threads are not filed, their source files are not
    trashed, and ``busy`` names it with the holder's pid.
    """
    files = source_files(src)
    messages, dup, files_by_id = _dedup_folder(files)
    thread_of = assign_threads(messages)

    members_by_thread: dict[str, list[dict[str, object]]] = {}
    for m in messages:
        members_by_thread.setdefault(thread_of[str(m["id"])], []).append(m)
    routed, unrouted = route_threads(members_by_thread, account_for, route_for)

    per_account: list[dict[str, object]] = []
    busy: list[dict[str, object]] = []
    durable: set[str] = set()
    for account in sorted(routed):  # name order: every run takes the locks alike
        share = [m for tid in routed[account] for m in members_by_thread[tid]]
        try:
            got = _file_under_lock(targets[account], share, thread_of, dry_run=dry_run)
        except StoreBusy as exc:
            busy.append({"account": account, "pid": exc.pid, "messages": len(share)})
            continue
        filed: set[str] = got.pop("durable")  # type: ignore[assignment]
        durable |= filed
        per_account.append(got)

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
        "excluded": sum(int(a["excluded"]) for a in per_account),  # type: ignore[call-overload]
        "filed": _merge_filed(per_account),
        "unrouted": sum(len(members_by_thread[tid]) for tid in unrouted),
        "unrouted_threads": len(unrouted),
        "busy": busy,
        "trashable": len(trashable),
        "trashed": 0,
    }
    if dry_run:
        return stats

    stats["trashed"] = trash(trashable) if to_trash else 0
    return stats


# --- Staged mail (what fetch got) ----------------------------------------


@dataclass
class _StagedRun:
    """What one ``ingest --staged`` decided beyond the rows and bodies it files.

    ``remove``: staged copies whose archived twin has the same bytes. ``duplicates``:
    (staged, ``.duplicates`` target, body). ``links``: native msgids onto archived rows,
    by year shard. ``sources``: each filed message's staged file.
    """

    unrecognized: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    excluded: int = 0
    archived: int = 0
    remove: list[Path] = field(default_factory=list)
    duplicates: list[tuple[Path, Path, bytes]] = field(default_factory=list)
    links: dict[int, dict[str, Row]] = field(default_factory=dict)
    sources: dict[str, Path] = field(default_factory=dict)


def _load_staged(
    found: list[tuple[Path, str]], run: _StagedRun
) -> list[dict[str, object]]:
    """Parse each file named as staged (ids and INTERNALDATE off its path); report the rest.

    The row's ``date`` is the INTERNALDATE, as catalog writes and shards by it. A
    second file for one msgid is refused and kept.
    """
    messages: list[dict[str, object]] = []
    seen: set[str] = set()
    for path, rel in found:
        staged = parse_staged(rel)
        if staged is None:
            run.unrecognized.append(rel)
            continue
        provider, msgid = staged.provider, staged.msgid
        if msgid in seen:
            run.refused.append(rel)
            continue
        seen.add(msgid)
        m = _parse_eml(path)
        m.update(source=path, rel=rel, msgid=msgid, thread=staged.thread_id)
        m.update(date=staged.internal_dt, provider=provider)
        m.update(stem=provider.file_stem(msgid))
        messages.append(m)
    return messages


def _key_staged(
    messages: list[dict[str, object]], existing: Existing, archive_dir: Path
) -> None:
    """Key each staged message as catalog keyed it (`message.mailbox_key`): by the
    provider its staged name says, in that provider's order."""
    work: dict[str, MailRow] = dict(existing.rows)

    def held(row: MailRow) -> bool:
        return bool(row.get("downloaded")) and eml_on_disk(row, archive_dir)

    def request(m: dict[str, object]) -> KeyRequest:
        msgid, when = str(m["msgid"]), cast(datetime, m["date"]).isoformat()
        tag = cast(Provider, m["provider"]).name
        key = message_key(
            message_id=str(m["message_id"]), provider=tag, native_id=msgid
        )
        return KeyRequest(key, msgid, when, cast(datetime | None, m["sent"]))

    for provider in dict.fromkeys(cast(Provider, m["provider"]) for m in messages):
        batch = [m for m in messages if m["provider"] is provider]
        keys = mailbox_keys(
            map(request, batch), provider=provider, rows=work, held=held
        )
        for m in batch:
            m["id"] = keys[str(m["msgid"])]


def _settle_archived(
    m: dict[str, object],
    existing: Existing,
    target: Target,
    duplicates_dir: Path,
    run: _StagedRun,
) -> None:
    """A staged message whose row is archived: drop an identical copy, or set it aside.

    The same mailbox message: same bytes are removed, other bytes refused and kept. A
    row archived under another name (an ingest-filed copy `mailbox_key` links): the
    msgid is linked onto it and the body set aside in the duplicates dir at the path
    it would have had, never over a taken one.
    """
    mid, src, raw = str(m["id"]), cast(Path, m["source"]), cast(bytes, m["raw"])
    row = existing.rows[mid]
    if row.get("msgid") == m["msgid"]:
        if (target.archive_dir / str(row["path"])).read_bytes() == raw:
            run.archived += 1
            run.remove.append(src)
        else:
            run.refused.append(str(m["rel"]))
        return
    dt: datetime = m["date"]  # type: ignore[assignment]
    dst = (
        duplicates_dir / anchor_folder(row) / dt.strftime("%Y-%m") / f"{m['stem']}.eml"
    )
    if dst.exists():
        if dst.read_bytes() == raw:
            run.remove.append(src)
        else:
            run.refused.append(str(m["rel"]))
            return
    else:
        run.duplicates.append((src, dst, raw))
    if mid in existing.shard_of:
        run.links.setdefault(existing.shard_of[mid], {})[mid] = {"msgid": m["msgid"]}


def _refuse_collisions(
    filed: _Filed, messages: list[dict[str, object]], target: Target, run: _StagedRun
) -> set[str]:
    """Ids to plan out: their archive target holds other bytes (refused, staged kept).

    A target with the same bytes is settled and its staged copy removed.
    """
    by_id = {str(m["id"]): m for m in messages}
    refused: set[str] = set()
    for mid, (rel, raw) in filed.to_write.items():
        archived = target.archive_dir / rel
        if not archived.exists():
            continue
        m = by_id[mid]
        if archived.read_bytes() == raw:
            run.remove.append(cast(Path, m["source"]))
        else:
            run.refused.append(str(m["rel"]))
            refused.add(mid)
    return refused


def _commit_staged(
    filed: _Filed, run: _StagedRun, target: Target, staging_dir: Path
) -> None:
    """Rows first, then bodies (`manifest.write_eml`), then the staged copies go.

    After a crash between, the next run's reconcile clears ``downloaded`` and the
    staged body, still got, is filed again offline.
    """
    years = set(filed.new_by_year) | set(run.links)
    for year in sorted(years):
        path = shard_path(target.store_dir, year)
        merged = merge_settlement(read_shard(path), run.links.get(year, {}))
        write_shard(path, merge_settlement(merged, filed.new_by_year.get(year, {})))
    for mid, (rel, raw) in filed.to_write.items():
        archived = target.archive_dir / rel
        if not archived.exists():
            write_eml(archived, raw)
        run.sources[mid].unlink()
    for src, dst, raw in run.duplicates:
        write_eml(dst, raw)
        src.unlink()
    for src in run.remove:
        src.unlink(missing_ok=True)
    touched = [*run.sources.values(), *(d[0] for d in run.duplicates), *run.remove]
    for directory in sorted({src.parent for src in touched}):
        if directory != staging_dir and not any(directory.iterdir()):
            directory.rmdir()  # an emptied thread dir; staging/ itself stays


def _staged_into(
    target: Target,
    found: list[tuple[Path, str]],
    duplicates_dir: Path,
    *,
    staging_dir: Path,
    dry_run: bool,
) -> dict[str, object]:
    """Decide, and unless ``dry_run`` write, the filing of every staged body."""
    run = _StagedRun()
    existing = _load_existing(target.store_dir, target.archive_dir)
    messages = _load_staged(found, run)
    _key_staged(messages, existing, target.archive_dir)
    to_file: list[dict[str, object]] = []
    for m in messages:
        mid = str(m["id"])
        if existing.rows.get(mid, {}).get("excluded"):
            run.excluded += 1  # plan rule 14: skipped, counted, left staged
        elif mid in existing.held:
            _settle_archived(m, existing, target, duplicates_dir, run)
        else:
            to_file.append(m)

    def plan_of(batch: list[dict[str, object]]) -> _Filed:
        by_thread: dict[str, list[dict[str, object]]] = {}
        for m in batch:
            by_thread.setdefault(str(m["thread"]), []).append(m)
        plan = _place_threads(by_thread, existing, target, staged=True)
        thread_of = {str(m["id"]): str(m["thread"]) for m in batch}
        return _new_rows(batch, existing, thread_of, plan, target.labels)

    filed = plan_of(to_file)
    refused = _refuse_collisions(filed, to_file, target, run)
    if refused:
        to_file = [m for m in to_file if str(m["id"]) not in refused]
        filed = plan_of(to_file)
    run.sources = {str(m["id"]): cast(Path, m["source"]) for m in to_file}

    stats = _filed_stats(target, filed)
    stats.update(
        staged=len(found),
        archived=run.archived,
        duplicates=len(run.duplicates),
        linked=sum(len(rows) for rows in run.links.values()),
        excluded=run.excluded,
        refused=sorted(run.refused),
        unrecognized=run.unrecognized,
    )
    if not dry_run:
        _commit_staged(filed, run, target, staging_dir)
    return stats


def ingest_staged(
    *,
    target: Target,
    staging_dir: Path,
    duplicates_dir: Path,
    dry_run: bool = False,
) -> dict[str, object]:
    """File what ``fetch`` got into ``staging_dir`` into ``target``'s archive (plan rule 6).

    Holds the store's lock and reconciles first (ADR 0002 §4). Each message is keyed
    as catalog keyed it (`message.mailbox_key`) and filed by `wicket.filing`'s rank, so
    ``--domain D`` files each one without an archived anchor under D (plan rule 7). A
    file not named as staged is reported in ``unrecognized``; nothing goes to the
    Trash. An empty staging dir is a no-op; a dry run takes no lock and writes
    nothing. Raises `env.StoreBusy` when another process holds the lock.
    """
    found = list(staged_files(staging_dir))
    if dry_run or not found:
        return _staged_into(
            target, found, duplicates_dir, staging_dir=staging_dir, dry_run=True
        )
    with store_lock(target.store_dir):
        reconcile(
            target.store_dir, target.archive_dir, target.account, target.domain_aliases
        )
        return _staged_into(
            target, found, duplicates_dir, staging_dir=staging_dir, dry_run=False
        )
