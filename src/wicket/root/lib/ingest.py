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
provider's thread id. A row is linked to every domain it touches by its
``from``/``to``/``cc``/``bcc``. A message whose row the owner marked ``excluded`` is
skipped and counted.

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
from collections import Counter
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

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
    is_held,
    merge_settlement,
    message_file,
    message_files,
    read_shard,
    read_shards,
    shard_path,
    staged_files,
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

    _, from_addr = email.utils.parseaddr(str(msg.get("From", "") or ""))
    # Each header is parsed on its own, so a malformed one loses only its own addresses.
    delivered = sorted(
        {a for h in RECIPIENT_HEADERS for a in address_list(msg.get_all(h, []))}
    )
    return {
        "id": key,
        "message_id": message_id,
        "sent": sent,
        "raw": raw,
        "date": dt,
        "from": from_addr.strip().lower(),
        "to": address_list([msg.get("To")]),
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


class AccountMismatch(ValueError):
    """A folder addressed to another account than the one named: its fact is ``leader``,
    the account most of it is addressed to."""

    reason = "account_mismatch"

    def __init__(self, message: str, leader: str) -> None:
        super().__init__(message)
        self.facts: dict[str, object] = {"leader": leader}


def check_account_matches(
    messages: list[dict[str, object]], account: str, account_for: AccountFor
) -> AccountMismatch | None:
    """Warn when ``account`` is not the account this folder was addressed to.

    ``--mail-account`` is a *destination*, not a filter: it says which store to write,
    and nothing about the folder. Naming the wrong one files the whole export
    into the wrong mailbox, silently and additively. So compare the folder's
    recipients against the target and refuse when another known account plainly
    owns this mail. Returns the complaint (`AccountMismatch`, which the caller raises
    or, under ``--force``, reports), or None when the target is defensible (it leads, it
    ties, or the alias map is too thin to tell).
    """
    tally = participants(messages, account_for)
    if not tally:
        return None
    leader, top = tally.most_common(1)[0]
    mine = tally.get(account, 0)
    if mine >= top:
        return None
    counts = ", ".join(f"{acct} ({n})" for acct, n in tally.most_common())
    return AccountMismatch(
        f"account mismatch: {mine} of {len(messages)} message(s) are addressed to "
        f"{account}, but {top} are addressed to {leader}. Recipients here: {counts}. "
        f"--mail-account names the store to write into, it does not filter the "
        f"folder, so this would file mail belonging to {leader} into {account}.",
        leader,
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
    ``files`` is the store's message files by stem (`manifest.message_files`), and
    ``holder`` the id of the held row each stem belongs to: a stem in ``files`` with no
    holder is a file no row names (filed by another tool).
    """

    rows: dict[str, Row]
    held: set[str]
    shard_of: dict[str, int]
    files: dict[str, Path]
    holder: dict[str, str]
    messages_dir: Path


def _load_existing(store_dir: Path, messages_dir: Path) -> Existing:
    """Every row already in the store, which are archived, and their shards."""
    rows: dict[str, Row] = {}
    shard_of: dict[str, int] = {}
    files = message_files(messages_dir)
    held: set[str] = set()
    holder: dict[str, str] = {}
    for year, shard in read_shards(store_dir).items():
        for id_, row in shard.items():
            rows[id_] = row
            shard_of[id_] = year
            if is_held(row, files):  # either copy
                held.add(id_)
                holder.setdefault(str(row["stem"]), id_)
    return Existing(
        rows=rows,
        held=held,
        shard_of=shard_of,
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


def _by_thread(
    messages: list[dict[str, object]], thread_of: dict[str, str]
) -> dict[str, list[dict[str, object]]]:
    """``messages`` grouped by their thread id in ``thread_of``."""
    members_by_thread: dict[str, list[dict[str, object]]] = {}
    for m in messages:
        members_by_thread.setdefault(thread_of[str(m["id"])], []).append(m)
    return members_by_thread


def _place_threads(
    members_by_thread: dict[str, list[dict[str, object]]], existing: Existing
) -> dict[str, str]:
    """Each member's final thread id, by message id.

    A local thread adopts the thread id of its earliest member the store has (an
    archived one first), else keeps its own.
    """
    thread_ids: dict[str, str] = {}
    for tid, members in members_by_thread.items():
        known = [
            existing.rows[i] for m in members if (i := str(m["id"])) in existing.rows
        ]
        first_known = opener(
            [r for r in known if str(r["id"]) in existing.held] or known
        )
        final_tid = tid
        if first_known is not None:
            final_tid = str(first_known.get("thread_id") or tid)
        thread_ids.update((str(m["id"]), final_tid) for m in members)
    return thread_ids


def _row(
    m: dict[str, object],
    thread_id: str,
    labels: list[str] | None,
    *,
    downloaded: bool,
) -> Row:
    """The manifest row for one message: filed (``downloaded``, its ``stem``), or not.

    A staged message's ``msgid`` is its provider's, else `message.local_msgid` of the
    key. ``labels`` is the ``--tag`` payload (a flat export carries no provider folders,
    so ``None`` unless the owner names tags).
    """
    mid = str(m["id"])
    dt: datetime = m["date"]  # type: ignore[assignment]
    row: Row = {
        "id": mid,
        "msgid": str(m.get("msgid") or local_msgid(mid)),
        "thread_id": thread_id,
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
        "downloaded": downloaded,
    }
    if downloaded:
        row["stem"] = m["stem"]
    if m.get("list_id"):  # only when the message has a List-Id header, never null
        row["list_id"] = m["list_id"]
    return row


def _rel(m: dict[str, object]) -> str:
    """A message's file relative to ``messages/``: ``YYYY/MM/<stem>.eml``.

    `manifest.message_file`, its month the message's own date (`message.message_date`,
    ``date`` the fallback).
    """
    when = message_date(cast(bytes, m["raw"]), cast(datetime, m["date"]))
    return message_file(Path(), str(m["stem"]), when).as_posix()


def _write_rows(store_dir: Path, *layers: dict[int, dict[str, Row]]) -> None:
    """Merge each layer's rows into its year shard, in layer order (additive only).

    A layer holds a whole row for a message the store had no row for, and only the
    fields to merge for one it has; ``merge_settlement`` adds the first and merges the
    second into its row. A rewritten shard's other rows serialize byte-identically
    (same deterministic write_shard), so nothing already archived is disturbed. Rows
    go before bodies (`_write_bodies`): after a crash before a body lands, the next
    run's reconcile clears ``downloaded`` and files it again from the source left in
    place.
    """
    for year in sorted({year for layer in layers for year in layer}):
        path = shard_path(store_dir, year)
        rows = read_shard(path)
        for layer in layers:
            rows = merge_settlement(rows, layer.get(year, {}))
        write_shard(path, rows)


def _write_bodies(messages_dir: Path, to_write: dict[str, tuple[str, bytes]]) -> None:
    """Write each new body (`manifest.write_eml`) where no file is yet."""
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
    thread_ids: dict[str, str],
    labels: list[str] | None = None,
) -> _Filed:
    """Rows by year, bodies to write, the copies, and the filed-file index.

    The index (``{YYYY/MM: [filename, ...]}``, name-sorted) is what the face
    reports. ``thread_ids`` is each message's final thread id (`_place_threads`). A
    held message is skipped. A message whose stem the store already holds,
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
        if stem in taken:
            _copy_of(m, taken[stem], existing, out)
            continue
        if stem in existing.files and not _same_subject(m, existing, stem):
            _copy_of(m, (None, None), existing, out)  # a file no row names, not this
            continue
        taken[stem] = (mid, str(m["subject"]))
        row = _row(m, thread_ids[mid], labels, downloaded=True)
        if stem in existing.files:  # taken over where it is, whatever month it chose
            rel = existing.files[stem].relative_to(existing.messages_dir).as_posix()
        else:
            rel = _rel(m)
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
    filed earlier in this run, its Subject; else the store's (`_held_subject`).
    """
    holder, subject = held_by
    if subject is None:
        subject = _held_subject(existing, str(m["stem"]))
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


def _held_subject(existing: Existing, stem: str) -> str:
    """The Subject of what the store holds at ``stem``: its holder row's, else the file's."""
    holder = existing.holder.get(stem)
    row = existing.rows.get(holder) if holder else None
    if row is not None and "subject" in row:
        return str(row["subject"])
    return _file_subject(existing.files[stem])


def _same_subject(m: dict[str, object], existing: Existing, stem: str) -> bool:
    """Whether ``m``'s Subject is that of what the store holds at ``stem``."""
    return _held_subject(existing, stem).strip() == str(m["subject"]).strip()


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
    no ``.eml``, `AccountMismatch` (a ``ValueError``) when the source is addressed to a
    different account than ``target.account`` (unless ``force``), and `env.StoreBusy` when
    another process holds the store's lock.
    """
    files = source_files(src)
    messages, dup, files_by_id = _dedup_folder(files)
    complaint = check_account_matches(messages, target.account, account_for)
    if complaint and not force:
        raise complaint

    thread_of = assign_threads(messages)
    with _lock(target.store_dir, idle=dry_run):
        stats, durable = _file_into(target, messages, thread_of, dry_run=dry_run)
    # Already-archived messages are durable too, so a re-run with nothing new to
    # add still clears its source files.
    trashable, trashed = _trash_sources(
        files_by_id, durable, to_trash=to_trash, dry_run=dry_run
    )
    # set only when --force overrode a mismatch
    stats["warning"] = str(complaint) if complaint else None
    stats["files"] = len(files)
    stats["unique"] = len(messages)
    stats["dup"] = dup
    stats["unrouted"] = 0  # a single-target run files everything it parsed
    stats["trashable"] = trashable
    stats["trashed"] = trashed
    return stats


def _lock(store_dir: Path, *, idle: bool) -> AbstractContextManager[None]:
    """The store's lock (`env.store_lock`), or none for a run that writes nothing.

    Entering it raises `env.StoreBusy` when another process holds the lock.
    """
    return nullcontext() if idle else store_lock(store_dir)


def _trash_sources(
    files_by_id: dict[str, list[Path]],
    ids: set[str],
    *,
    to_trash: bool,
    dry_run: bool,
) -> tuple[int, int]:
    """With ``to_trash``, how many source files of ``ids`` go to the Trash, and how many
    went: none on a dry run. Without it, ``(0, 0)``."""
    if not to_trash:
        return 0, 0
    paths = sorted(p for mid in ids for p in files_by_id[mid])
    return len(paths), 0 if dry_run else trash(paths)


def _skip_reason(
    m: dict[str, object], existing: Existing, target: Target
) -> Literal["excluded", "excluded_lists"] | None:
    """Why ``m`` is not filed into ``target``, or None to file it.

    The owner's ``excluded`` mark on its row wins (plan rule 14); then a mailing list
    the target excludes (``excluded_lists``).
    """
    if existing.rows.get(str(m["id"]), {}).get("excluded"):
        return "excluded"
    if m["list_id"] in target.excluded_lists:
        return "excluded_lists"
    return None


def _file_into(
    target: Target,
    messages: list[dict[str, object]],
    thread_of: dict[str, str],
    *,
    dry_run: bool,
) -> tuple[dict[str, object], set[str]]:
    """File ``messages`` into one store, additively. The single-destination step.

    ``messages`` is either the whole folder (single-target ingest) or one
    account's share of it (routed ingest); either way it is whole threads, so the
    filing plan sees every member of every thread it decides on. A message
    `_skip_reason` names is skipped and counted: not filed, and its source file is not
    trashed. Returns the stats and the ids durably in the store (see below).
    """
    existing = _load_existing(target.store_dir, target.messages_dir)
    skip = {str(m["id"]): _skip_reason(m, existing, target) for m in messages}
    kept = [m for m in messages if skip[str(m["id"])] is None]
    thread_ids = _place_threads(_by_thread(kept, thread_of), existing)
    filed = _new_rows(kept, existing, thread_ids, target.labels)
    stats = _filed_stats(target, filed)
    # Parsed, but already in this store: left byte-for-byte alone. Counted because
    # without it a run that trashed every source file while adding fewer rows than
    # it read reads like loss.
    stats["skipped"] = len(kept) - len(filed.to_write) - len(filed.copies)
    skipped = Counter(skip.values())
    stats["excluded"] = skipped["excluded"]
    stats["excluded_lists"] = skipped["excluded_lists"]
    # Durably in this store once the write lands: filed now, or already
    # archived. These are the messages whose source files may be trashed.
    # A copy whose Subject differs may be a different message under a reused
    # Message-ID: the store does not hold it, so its source file is kept.
    durable = {str(m["id"]) for m in kept} - {
        str(x["id"]) for x in filed.subject_mismatches
    }
    if not dry_run and filed.to_write:
        _write_rows(target.store_dir, filed.new_by_year)
        _write_bodies(target.messages_dir, filed.to_write)
    return stats, durable


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
            # Another Subject at its stem is a reused Message-ID: not held.
            if mid in existing.held or (
                stem in existing.files and _same_subject(m, existing, stem)
            ):
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
    members_by_thread = _by_thread(messages, thread_of)
    routed, unrouted = route_threads(members_by_thread, account_for, route_for)

    per_account: list[dict[str, object]] = []
    busy: list[dict[str, object]] = []
    durable: set[str] = set()
    for account in sorted(routed):  # name order: every run takes the locks alike
        share = [m for tid in routed[account] for m in members_by_thread[tid]]
        target = targets[account]
        try:
            with _lock(target.store_dir, idle=dry_run):
                got, got_durable = _file_into(target, share, thread_of, dry_run=dry_run)
        except StoreBusy as exc:
            busy.append({"account": account, "pid": exc.pid, "messages": len(share)})
            continue
        durable |= got_durable
        per_account.append(got)

    held = _held_anywhere(
        [m for tid in unrouted for m in members_by_thread[tid]], targets
    )
    trashable, trashed = _trash_sources(
        files_by_id, durable | held, to_trash=to_trash, dry_run=dry_run
    )

    def total(key: str) -> int:
        return sum(cast(int, a[key]) for a in per_account)

    return {
        "files": len(files),
        "unique": len(messages),
        "dup": dup,
        "accounts": per_account,
        "added": total("added"),
        "skipped": total("skipped"),
        "copies": total("copies"),
        "subject_mismatches": [
            mismatch
            for a in per_account
            for mismatch in a["subject_mismatches"]  # type: ignore[attr-defined]
        ],
        "excluded": total("excluded"),
        "excluded_lists": total("excluded_lists"),
        "filed": _merge_filed(per_account),
        "unrouted": sum(len(members_by_thread[tid]) for tid in unrouted),
        "unrouted_threads": len(unrouted),
        "unrouted_held": len(held),
        "busy": busy,
        "trashable": trashable,
        "trashed": trashed,
    }


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
        messages.append(m)
    return messages


def _key_staged(messages: list[dict[str, object]], existing: Existing) -> None:
    """Key each staged message as catalog keyed it (`message.mailbox_key`): by the
    provider its staged name says, in that provider's order."""
    work: dict[str, MailRow] = dict(existing.rows)

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
            map(request, batch),
            provider=provider,
            rows=work,
            held=lambda row: is_held(row, existing.files),
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
    name = cast(Provider, m["provider"]).file_stem(str(m["msgid"]))
    dst = duplicates_dir / dt.strftime("%Y-%m") / f"{name}.eml"
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
            mark = {**_row(m, str(m["thread"]), None, downloaded=False), **mark}
        run.links.setdefault(year, {}).setdefault(mid, {}).update(mark)


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
    _write_rows(target.store_dir, run.links, filed.new_by_year)
    _write_bodies(target.messages_dir, filed.to_write)
    for mid in filed.to_write:
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
        reason = _skip_reason(m, existing, target)
        if reason == "excluded":
            if (
                existing.holder.get(str(m["stem"])) != mid
                and str(m["stem"]) in existing.files
            ):
                _set_aside(m, duplicates_dir, run)  # a copy an earlier run marked
            else:
                run.excluded += 1  # plan rule 14: skipped, counted, left staged
        elif reason == "excluded_lists":
            run.excluded_lists += 1  # an excluded mailing list's: left staged
        elif mid in existing.held:
            _settle_archived(m, existing, duplicates_dir, run)
        else:
            to_file.append(m)

    # A staged message keeps its provider's thread id.
    thread_ids = {str(m["id"]): str(m["thread"]) for m in to_file}
    filed = _new_rows(to_file, existing, thread_ids, target.labels)
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
    idle = dry_run or not found
    with _lock(target.store_dir, idle=idle):
        if not idle:
            reconcile(target.store_dir, target.messages_dir)
        return _staged_into(
            target, found, duplicates_dir, staging_dir=staging_dir, dry_run=idle
        )
