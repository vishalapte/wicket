# pylint: disable=too-many-lines  # one module per verb's worker/api; split is follow-up work
"""File ``.eml`` into the manifest and ``messages/``, additively: the only verb that files.

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
provider's thread id. There is no filing rank and no folder: a row is linked to every
domain it touches by its ``from``/``to``/``cc``/``bcc``. A message whose row the owner
marked ``excluded`` is skipped and counted.

**One file per message** (ADR-MAIL, 2026-11-14): a message's file is
``<store>/messages/YYYY/MM/<stem>.eml``, named by `message.message_stem` and filed
under its `message.message_date`, written once and never moved; the row records
``stem``. A message whose stem the store already holds (another row's file, or an
earlier message of the same run) is not filed again: it is a copy, counted, and
reported in ``subject_mismatches`` when its ``Subject`` differs from the held one's.
A staged copy's row is marked ``excluded: "duplicate of <holder id>"`` and its body
set aside in the duplicates dir, so fetch never gets it again.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from wicket.env import StoreBusy, store_lock
from wicket.exclusions import normalize_list_id
from wicket.filing import (
    AccountFor,
    opener,
    participants,
    route_threads,
)
from wicket.manifest import (
    Row,
    eml_on_disk,
    merge_settlement,
    message_file,
    message_files,
    read_shard,
    shard_path,
    staged_files,
    store_shards,
    write_eml,
    write_shard,
)
from wicket.message import (
    KeyRequest,
    MailRow,
    address_list,
    assign_threads,
    attachment_names,
    conversation_key,
    local_msgid,
    mailbox_keys,
    message_date,
    message_id_of,
    message_key,
    message_stem,
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
        "cc": address_list(msg.get_all("Cc", [])),
        "bcc": address_list(msg.get_all("Bcc", [])),
        "attachments": attachment_names(msg),
        "stem": message_stem(raw),
        "rcpt": delivered,
        "subject": str(msg.get("Subject", "") or ""),
        "size": len(raw),
        "refs": referenced_ids(msg),
        "conv": conversation_key(msg),
        "list_id": normalize_list_id(str(msg.get("List-Id", "") or "")),
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
    An unroutable message keeps its file unless a store already holds it, so
    nothing leaves the folder without a home. Name collisions in the Trash get a
    numeric suffix rather than clobbering whatever is already there.
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
    ``files`` is the store's message files by stem (`manifest.message_files`), and
    ``holder`` the id of the held row each stem belongs to: a stem in ``files`` with no
    holder is a file no row names (filed by another tool).
    """

    rows: dict[str, Row]
    held: set[str]
    shard_of: dict[str, int]
    by_thread: dict[str, list[Row]] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)
    holder: dict[str, str] = field(default_factory=dict)
    messages_dir: Path = Path()


def _load_existing(store_dir: Path, messages_dir: Path) -> Existing:
    """Every row already in the store, which are archived, their shards and threads."""
    rows: dict[str, Row] = {}
    shard_of: dict[str, int] = {}
    shards = [(shard, read_shard(shard)) for shard in store_shards(store_dir)]
    files = message_files(messages_dir)
    held: set[str] = set()
    holder: dict[str, str] = {}
    for shard, got in shards:
        for id_, row in got.items():
            rows[id_] = row
            shard_of[id_] = int(shard.stem)
            if row.get("downloaded") and eml_on_disk(row, files):  # either copy
                held.add(id_)
                holder.setdefault(str(row["stem"]), id_)
    by_thread: dict[str, list[Row]] = {}
    for row in rows.values():
        if row.get("thread_id"):
            by_thread.setdefault(str(row["thread_id"]), []).append(row)
    return Existing(
        rows=rows,
        held=held,
        shard_of=shard_of,
        by_thread=by_thread,
        files=files,
        holder=holder,
        messages_dir=messages_dir,
    )


@dataclass(frozen=True)
class Target:
    """Where an ingest writes: the store, its ``messages/``, and the account.

    ``labels`` is the optional ``--tag`` payload, recorded as each new row's
    ``labels``. It touches only mail filed *this run*: an already-archived row is
    left byte-for-byte alone, and a row that was only observed keeps its own
    ``labels`` when its file is filed.
    """

    store_dir: Path
    messages_dir: Path
    account: str
    labels: list[str] | None = None  # --tag: recorded as each new row's labels
    # exclusions.excluded_for: mailing lists whose mail is never filed here.
    excluded_lists: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Placed:
    """One thread's final thread id."""

    thread_id: str


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
    *,
    staged: bool = False,
) -> dict[str, Placed]:
    """Per thread of ``members_by_thread``: its final thread id.

    A staged thread keeps its provider's thread id; a local one adopts that of its
    earliest member the store has (an archived one first).
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
        plan[tid] = Placed(final_tid)
    return plan


def _row_for(
    m: dict[str, object],
    placed: Placed,
    labels: list[str] | None = None,
) -> tuple[Row, str]:
    """Build the manifest row and ``messages/`` rel-path for one new message.

    The file is ``YYYY/MM/<stem>.eml`` (`manifest.message_file`), its month the
    message's own date (`message.message_date`, ``date`` the fallback). A staged
    message's ``msgid`` is its provider's, else `message.local_msgid` of the key.
    ``labels`` is the ``--tag`` payload (a flat export carries no provider folders,
    so ``None`` unless the owner names tags).
    """
    mid = str(m["id"])
    mgid = str(m.get("msgid") or local_msgid(mid))
    dt: datetime = m["date"]  # type: ignore[assignment]
    rel = _rel(m)
    row: Row = {
        "id": mid,
        "msgid": mgid,
        "thread_id": placed.thread_id,
        "date": dt.isoformat(),
        "from": m["from"],
        "to": m["to"],
        "cc": m["cc"],
        "bcc": m["bcc"],
        "subject": m["subject"],
        "size": m["size"],
        "labels": labels,  # --tag, or None for a bare .eml export
        "attachments": m["attachments"],
        "deleted": False,
        "downloaded": True,
        "stem": m["stem"],
    }
    if m.get("list_id"):  # only when the message has a List-Id header, never null
        row["list_id"] = m["list_id"]
    return row, rel


def _rel(m: dict[str, object]) -> str:
    """A message's file relative to ``messages/``: ``YYYY/MM/<stem>.eml``."""
    when = message_date(cast(bytes, m["raw"]), cast(datetime, m["date"]))
    return message_file(Path(), str(m["stem"]), when).as_posix()


def _commit(
    new_by_year: dict[int, dict[str, Row]],
    to_write: dict[str, tuple[str, bytes]],
    store_dir: Path,
    messages_dir: Path,
) -> None:
    """Merge rows into each year shard, then write the new bodies (additive only).

    ``new_by_year`` holds a whole row for a message the store had no row for, and
    only the file fields for one it had observed; ``merge_settlement`` adds the
    first and merges the second into its row. A rewritten shard's other rows
    serialize byte-identically (same deterministic write_shard), so nothing
    already archived is disturbed. Rows go first: after a crash before a body lands,
    the next run's reconcile clears ``downloaded`` and files it again from the
    source left in place.
    """
    for year, rows in new_by_year.items():
        path = shard_path(store_dir, year)
        write_shard(path, merge_settlement(read_shard(path), rows))
    for rel, raw in to_write.values():
        target = messages_dir / rel
        if not target.exists():
            write_eml(target, raw)


@dataclass
class _Filed:
    """What one filing pass decided, before anything is written."""

    new_by_year: dict[int, dict[str, Row]] = field(default_factory=dict)
    to_write: dict[str, tuple[str, bytes]] = field(
        default_factory=dict
    )  # id -> (rel, raw)
    index: dict[str, list[str]] = field(default_factory=dict)  # dir -> [file name]
    # Messages not filed because their stem is already held: id -> the holder's id
    # (None for a file no row names).
    copies: dict[str, str | None] = field(default_factory=dict)
    subject_mismatches: list[dict[str, object]] = field(default_factory=list)


def _new_rows(
    messages: list[dict[str, object]],
    existing: Existing,
    thread_of: dict[str, str],
    plan: dict[str, Placed],
    labels: list[str] | None = None,
) -> _Filed:
    """Rows by year, bodies to write, the copies, and the filed-file index.

    The index (``{YYYY/MM: [filename, ...]}``, name-sorted) is what the face
    reports. A held message is skipped. A message whose stem the store already holds,
    or an earlier message of this run took, is a copy: not filed, recorded in
    ``copies`` (and in ``subject_mismatches`` when its ``Subject`` differs). A
    message with a row but no file gets only the file fields (``stem``,
    ``downloaded``) in that row's own shard, its observation untouched. A row whose
    file is gone is filed again.
    """
    out = _Filed()
    taken: dict[str, tuple[str | None, str | None]] = {
        stem: (holder, None) for stem, holder in existing.holder.items()
    }
    for m in messages:
        mid = str(m["id"])
        if mid in existing.held:
            continue  # already archived: untouched
        stem = str(m["stem"])
        if stem in taken and taken[stem][0] != mid:
            _copy_of(m, taken[stem], existing, out)
            continue
        if stem in existing.files and not _same_subject(m, existing.files[stem]):
            _copy_of(m, (None, None), existing, out)  # a file no row names, not this
            continue
        taken[stem] = (mid, str(m["subject"]))
        placed = plan[thread_of[mid]]
        row, rel = _row_for(m, placed, labels)
        if stem in existing.files:  # taken over where it is, whatever month it chose
            rel = existing.files[stem].relative_to(existing.messages_dir).as_posix()
        dt: datetime = m["date"]  # type: ignore[assignment]
        year = dt.year
        prior = existing.rows.get(mid)
        if prior is not None:  # observed, not archived: record the file on its row
            row = {"downloaded": True, "stem": stem}
            year = existing.shard_of.get(mid, year)
        out.new_by_year.setdefault(year, {})[mid] = row
        out.to_write[mid] = (rel, m["raw"])  # type: ignore[assignment]
        directory, _, name = rel.rpartition("/")
        out.index.setdefault(directory, []).append(name)
    for names in out.index.values():
        names.sort()
    return out


def _copy_of(
    m: dict[str, object],
    held_by: tuple[str | None, str | None],
    existing: Existing,
    out: _Filed,
) -> None:
    """Record ``m`` as a copy of the message holding its stem; compare their Subjects.

    ``held_by`` is the holder's id (None for a file no row names) and, for a message
    filed earlier in this run, its Subject. A held row's Subject is its row's; a
    file no row names is read.
    """
    holder, subject = held_by
    if subject is None:
        row = existing.rows.get(holder) if holder else None
        if row is not None and "subject" in row:
            subject = str(row["subject"])
        else:
            subject = _file_subject(existing.files[str(m["stem"])])
    out.copies[str(m["id"])] = holder
    if subject.strip() != str(m["subject"]).strip():
        out.subject_mismatches.append(
            {
                "stem": str(m["stem"]),
                "id": str(m["id"]),
                "subject": str(m["subject"]),
                "holder": holder,
                "holder_subject": subject,
            }
        )


def _same_subject(m: dict[str, object], path: Path) -> bool:
    """Whether ``m``'s Subject is the filed ``.eml``'s at ``path``."""
    return _file_subject(path).strip() == str(m["subject"]).strip()


def _file_subject(path: Path) -> str:
    """The decoded ``Subject`` of a filed ``.eml``; '' when it has none."""
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    return str(msg.get("Subject", "") or "")


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
    row the owner marked ``excluded`` is skipped and counted (plan rule 14), and so is
    one sent via a mailing list the target excludes (``excluded_lists``): neither is
    filed, and its source file is not trashed.
    """
    existing = _load_existing(target.store_dir, target.messages_dir)
    excluded = {
        mid
        for m in messages
        if existing.rows.get(mid := str(m["id"]), {}).get("excluded")
    }
    listed = {
        str(m["id"]) for m in messages if m["list_id"] in target.excluded_lists
    } - excluded
    kept = [m for m in messages if str(m["id"]) not in excluded | listed]

    members_by_thread: dict[str, list[dict[str, object]]] = {}
    for m in kept:
        members_by_thread.setdefault(thread_of[str(m["id"])], []).append(m)
    plan = _place_threads(members_by_thread, existing)
    filed = _new_rows(kept, existing, thread_of, plan, target.labels)
    stats = _filed_stats(target, filed)
    # Parsed, but already in this store: left byte-for-byte alone. Counted because
    # without it a run that trashed every source file while adding fewer rows than
    # it read reads like loss.
    stats["skipped"] = len(kept) - len(filed.to_write) - len(filed.copies)
    stats["excluded"] = len(excluded)
    stats["excluded_lists"] = len(listed)
    # Durably in this store once the commit lands: filed now, or already
    # archived. These are the messages whose source files may be trashed.
    # A copy whose Subject differs may be a different message under a reused
    # Message-ID: the store does not hold it, so its source file is kept.
    stats["durable"] = {str(m["id"]) for m in kept} - {
        str(x["id"]) for x in filed.subject_mismatches
    }
    if dry_run or not filed.to_write:
        return stats

    _commit(filed.new_by_year, filed.to_write, target.store_dir, target.messages_dir)
    return stats


def _filed_stats(target: Target, filed: _Filed) -> dict[str, object]:
    """The counts every filing run reports, whichever source it read."""
    return {
        "account": target.account,
        "added": len(filed.to_write),
        "added_by_year": {
            y: len(rows) for y, rows in sorted(filed.new_by_year.items())
        },
        # {messages/ dir -> [.eml filename, ...]}: what this run filed and where.
        "filed": filed.index,
        # Not filed: their stem is already held (one file per message per store).
        "copies": len(filed.copies),
        "subject_mismatches": filed.subject_mismatches,
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


def _held_anywhere(
    messages: list[dict[str, object]], targets: dict[str, Target]
) -> set[str]:
    """The ids of ``messages`` some store in ``targets`` already holds: its row is
    archived, or a file with its stem and its Subject is on disk there."""
    held: set[str] = set()
    for target in targets.values() if messages else ():
        existing = _load_existing(target.store_dir, target.messages_dir)
        for m in messages:
            mid, stem = str(m["id"]), str(m["stem"])
            if mid in existing.held:
                held.add(mid)
            elif stem in existing.files:
                holder = existing.rows.get(existing.holder.get(stem, ""))
                if holder is not None and "subject" in holder:
                    same = str(holder["subject"]).strip() == str(m["subject"]).strip()
                else:
                    same = _same_subject(m, existing.files[stem])
                if same:  # the same message: a reused Message-ID is not held
                    held.add(mid)
    return held


def ingest_routed(  # pylint: disable=too-many-locals
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
    is left alone: not filed. With ``to_trash``, the source files of every message
    that is durably in a store are moved to the Trash: what this run filed, and an
    unrouted message a store already holds (``unrouted_held``); the other unrouted
    keep theirs. A store whose lock another
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
        durable |= got.pop("durable")  # type: ignore[arg-type]
        per_account.append(got)

    held = _held_anywhere(
        [m for tid in unrouted for m in members_by_thread[tid]], targets
    )
    trashable = (
        sorted(p for mid in durable | held for p in files_by_id[mid])
        if to_trash
        else []
    )
    stats: dict[str, object] = {
        "files": len(files),
        "unique": len(messages),
        "dup": dup,
        "accounts": per_account,
        "added": sum(int(a["added"]) for a in per_account),  # type: ignore[call-overload]
        "skipped": sum(int(a["skipped"]) for a in per_account),  # type: ignore[call-overload]
        "copies": sum(int(a["copies"]) for a in per_account),  # type: ignore[call-overload]
        "subject_mismatches": [
            mismatch
            for a in per_account
            for mismatch in a["subject_mismatches"]  # type: ignore[attr-defined]
        ],
        "excluded": sum(int(a["excluded"]) for a in per_account),  # type: ignore[call-overload]
        "excluded_lists": sum(
            int(a["excluded_lists"]) for a in per_account  # type: ignore[call-overload]
        ),
        "filed": _merge_filed(per_account),
        "unrouted": sum(len(members_by_thread[tid]) for tid in unrouted),
        "unrouted_threads": len(unrouted),
        "unrouted_held": len(held),
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
    (staged, ``.duplicates`` target, body). ``links``: fields merged onto existing rows
    by year shard (a native msgid linked onto an archived row, or a copy's
    ``excluded``). ``sources``: each filed message's staged file.
    """

    unrecognized: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    excluded: int = 0
    excluded_lists: int = 0
    archived: int = 0
    remove: list[Path] = field(default_factory=list)
    duplicates: list[tuple[Path, Path, bytes]] = field(default_factory=list)
    links: dict[int, dict[str, Row]] = field(default_factory=dict)
    sources: dict[str, Path] = field(default_factory=dict)
    adopted: set[str] = field(default_factory=set)


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
        m.update(dup_name=provider.file_stem(msgid))  # its name if set aside
        messages.append(m)
    return messages


def _key_staged(messages: list[dict[str, object]], existing: Existing) -> None:
    """Key each staged message as catalog keyed it (`message.mailbox_key`): by the
    provider its staged name says, in that provider's order."""
    work: dict[str, MailRow] = dict(existing.rows)

    def held(row: MailRow) -> bool:
        return bool(row.get("downloaded")) and eml_on_disk(row, existing.files)

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
        if existing.files[str(row["stem"])].read_bytes() == raw:
            run.archived += 1
            run.remove.append(src)
        else:
            run.refused.append(str(m["rel"]))
        return
    if not _set_aside(m, duplicates_dir, run):
        return
    if mid in existing.shard_of:
        run.links.setdefault(existing.shard_of[mid], {})[mid] = {"msgid": m["msgid"]}


def _set_aside(m: dict[str, object], duplicates_dir: Path, run: _StagedRun) -> bool:
    """Plan a staged body's move to ``<duplicates>/YYYY-MM/<name>.eml``.

    False when a different file is already there: the staged body is refused and kept.
    """
    src, raw = cast(Path, m["source"]), cast(bytes, m["raw"])
    dt: datetime = m["date"]  # type: ignore[assignment]
    dst = duplicates_dir / dt.strftime("%Y-%m") / f"{m['dup_name']}.eml"
    if not dst.exists():
        run.duplicates.append((src, dst, raw))
    elif dst.read_bytes() == raw:
        run.remove.append(src)
    else:
        run.refused.append(str(m["rel"]))
        return False
    return True


def _settle_copies(
    filed: _Filed,
    messages: list[dict[str, object]],
    existing: Existing,
    duplicates_dir: Path,
    run: _StagedRun,
) -> None:
    """Each staged copy of a held stem: set its body aside, mark its row excluded.

    The mark (``"duplicate of <holder id>"``) is what keeps fetch from getting the
    message again. A copy catalog has not observed yet gets a whole row (from the
    staged message, never downloaded) holding the mark.
    """
    by_id = {str(m["id"]): m for m in messages}
    for mid, holder in filed.copies.items():
        m = by_id[mid]
        if not _set_aside(m, duplicates_dir, run):
            continue
        dt: datetime = m["date"]  # type: ignore[assignment]
        year = existing.shard_of.get(mid, dt.year)
        mark: Row = {"excluded": f"duplicate of {holder or 'stem ' + str(m['stem'])}"}
        if mid not in existing.rows:
            mark = {**_copy_row(m), **mark}
        run.links.setdefault(year, {}).setdefault(mid, {}).update(mark)


def _copy_row(m: dict[str, object]) -> Row:
    """A row for a staged message catalog has not observed: its observation, unfiled."""
    dt: datetime = m["date"]  # type: ignore[assignment]
    row: Row = {
        "id": str(m["id"]),
        "msgid": str(m["msgid"]),
        "thread_id": str(m["thread"]),
        "date": dt.isoformat(),
        "from": m["from"],
        "to": m["to"],
        "cc": m["cc"],
        "bcc": m["bcc"],
        "subject": m["subject"],
        "size": m["size"],
        "labels": None,
        "attachments": m["attachments"],
        "deleted": False,
        "downloaded": False,
    }
    if m.get("list_id"):
        row["list_id"] = m["list_id"]
    return row


def _adopt(
    filed: _Filed,
    messages: list[dict[str, object]],
    target: Target,
    duplicates_dir: Path,
    run: _StagedRun,
) -> None:
    """A message whose file is already on disk, named by no row: the row takes it.

    Another tool (delphi, enine-mail) files into the same ``messages/``. The file stays
    as it is; the staged body is removed when its bytes are the same, else set aside.
    """
    by_id = {str(m["id"]): m for m in messages}
    for mid, (rel, raw) in filed.to_write.items():
        archived = target.messages_dir / rel
        if not archived.exists():
            continue
        m = by_id[mid]
        run.adopted.add(mid)
        if archived.read_bytes() == raw:
            run.remove.append(cast(Path, m["source"]))
        else:
            _set_aside(m, duplicates_dir, run)


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
        archived = target.messages_dir / rel
        if not archived.exists():
            write_eml(archived, raw)
        if mid not in run.adopted:  # an adopted body is removed or set aside below
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
    existing = _load_existing(target.store_dir, target.messages_dir)
    messages = _load_staged(found, run)
    _key_staged(messages, existing)
    to_file: list[dict[str, object]] = []
    for m in messages:
        mid = str(m["id"])
        holder = existing.holder.get(str(m["stem"]))
        if existing.rows.get(mid, {}).get("excluded"):
            if holder != mid and str(m["stem"]) in existing.files:
                _set_aside(m, duplicates_dir, run)  # a copy an earlier run marked
            else:
                run.excluded += 1  # plan rule 14: skipped, counted, left staged
        elif m["list_id"] in target.excluded_lists:
            run.excluded_lists += 1  # an excluded mailing list's: left staged
        elif mid in existing.held:
            _settle_archived(m, existing, duplicates_dir, run)
        else:
            to_file.append(m)

    def plan_of(batch: list[dict[str, object]]) -> _Filed:
        by_thread: dict[str, list[dict[str, object]]] = {}
        for m in batch:
            by_thread.setdefault(str(m["thread"]), []).append(m)
        plan = _place_threads(by_thread, existing, staged=True)
        thread_of = {str(m["id"]): str(m["thread"]) for m in batch}
        return _new_rows(batch, existing, thread_of, plan, target.labels)

    filed = plan_of(to_file)
    _adopt(filed, to_file, target, duplicates_dir, run)
    _settle_copies(filed, to_file, existing, duplicates_dir, run)
    run.sources = {str(m["id"]): cast(Path, m["source"]) for m in to_file}

    stats = _filed_stats(target, filed)
    stats.update(
        staged=len(found),
        archived=run.archived,
        duplicates=len(run.duplicates),
        linked=sum(
            "msgid" in row for rows in run.links.values() for row in rows.values()
        ),
        excluded=run.excluded,
        excluded_lists=run.excluded_lists,
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
    as catalog keyed it (`message.mailbox_key`) and filed at its stem. A file not
    named as staged is reported in ``unrecognized``; nothing goes to the Trash. An
    empty staging dir is a no-op; a dry run takes no lock and writes nothing. Raises
    `env.StoreBusy` when another process holds the lock.
    """
    found = list(staged_files(staging_dir))
    if dry_run or not found:
        return _staged_into(
            target, found, duplicates_dir, staging_dir=staging_dir, dry_run=True
        )
    with store_lock(target.store_dir):
        reconcile(target.store_dir, target.messages_dir)
        return _staged_into(
            target, found, duplicates_dir, staging_dir=staging_dir, dry_run=False
        )
