"""Get .eml for matching messages into the store's staging dir; file nothing.

fetch only gets mail. Each body lands at its provider's staged name under
``<store>/staging/`` (`Provider.staged_name`; Gmail's
``<thread_id>/<msgid>@<INTERNALDATE>.eml``),
and ``ingest --staged`` is the separate command that files it into the store by
`wicket.filing`'s rules (plan rule 6). Staged mail is not in ``report`` or
``held_messages`` until it is filed.

Two entry points. `download` is one pass over a provider search (``--domains``,
``--query`` or ``--last``). `download_years` gets every message the provider's
whole-mailbox pass walks (Gmail's All Mail; every Fastmail folder `auth.folders` walks),
not got and not excluded: this year, last year, then each older year from the first,
oldest first, one pass each, and the run stops at the first throttle (a rejected,
timed-out or aborted command, a failed month, a short body), which writes the account's
HALT (`wicket.auth.write_halt`). A pass:

1. Discover (one connection): run the search in each folder; cheap native-id FETCH
   (a message in two folders is one, found in the first);
   drop msgids already **got** (a downloaded or excluded row's, a staged body's,
   a duplicate's under ``<mail root>/.duplicates/<store>/``); header FETCH (msgid,
   thrid, date, Message-ID, Date) for the rest.
2. Plan: key each message by ``message.mailbox_key``, so two Gmail messages sharing
   a Message-ID get two rows, and give it its staged path. No thread is dropped: a
   fetch gets anything not excluded. A message the
   store already holds under another name (ingest filed it: same ``id``, same send
   date) is linked, its native msgid written onto that row, and not downloaded
   again. A row the owner marked ``excluded`` is never downloaded.
3. Retrieve (``threads`` worker connections, one whole month each): batched body
   FETCH, write each ``.eml`` into staging. No row is settled.

Read-only on the mailbox (``SELECT`` is read-only). No CLI; the api
(``wicket.root.api``) seeds credentials and calls ``download()``.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import NamedTuple, cast

from wicket.auth import (
    IMAP_FAILURES,
    AuthError,
    Halted,
    chunked,
    first_year,
    folders,
    halt_in_force,
    internaldate,
    logout_quietly,
    open_mailbox,
    rfc822_size,
    throttle_halt,
    uid_fetch,
    uid_search,
    visit,
    write_halt,
)
from wicket.env import DEFAULT_THREADS
from wicket.manifest import (
    Row,
    is_held,
    merge_settlement,
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
    mailbox_keys,
    message_id_of,
    message_key,
    sent_date,
)
from wicket.provider import GMAIL, Provider, Search, parse_staged
from wicket.reconcile import reconcile

BATCH_SIZE = 500  # UIDs per IMAP FETCH; bounds memory + command size.
_UID_RE = re.compile(rb"\bUID\s+(\d+)")
# Progress, one line per step; the CLI's `main()` shows it on stderr.
logger = logging.getLogger(__name__)


class Meta(NamedTuple):
    """A message's planning headers: its native id and thread, its INTERNALDATE in UTC,
    its Message-ID, and its ``Date:`` in UTC (``sent``; None when unreadable)."""

    msgid: str
    thread_id: str
    internal_dt: datetime
    message_id: str
    sent: datetime | None


class Record(NamedTuple):
    """A discovery record: a message's UID, its `Meta`, and its folder (a UID means
    something only in its folder)."""

    uid: bytes
    msgid: str
    thread_id: str
    internal_dt: datetime
    message_id: str
    sent: datetime | None
    folder: str


# --- IMAP FETCH parsing --------------------------------------------------


def _fetch_msgids_batch(
    conn: imaplib.IMAP4_SSL, uids: list[bytes], provider: Provider = GMAIL
) -> dict[bytes, str]:
    """Cheap batched FETCH of just the native id. Returns ``{uid: msgid}``."""
    items = f"({provider.id_item})"
    out: dict[bytes, str] = {}
    for entry in uid_fetch(conn, uids, items, f"UID FETCH {provider.id_item}"):
        chunk = entry if isinstance(entry, bytes) else entry[0]
        uid_match = _UID_RE.search(chunk)
        msgid = provider.parse_id(chunk)
        if uid_match and msgid:
            out[uid_match.group(1)] = msgid
    return out


def _fetch_plan_metadata_batch(
    conn: imaplib.IMAP4_SSL, uids: list[bytes], provider: Provider = GMAIL
) -> dict[bytes, Meta]:
    """Batched header FETCH of msgid + thrid + date + Message-ID + Date: ``{uid: Meta}``.

    Headers only (no bodies); used to plan month buckets, the key, and the send date
    that links a copy the store already holds.
    """
    items = (
        f"({provider.id_item} {provider.thread_item} INTERNALDATE "
        "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE)])"
    )
    out: dict[bytes, Meta] = {}
    for entry in uid_fetch(conn, uids, items, "UID FETCH headers"):
        if not isinstance(entry, tuple):
            continue
        header, body = entry
        uid_match = _UID_RE.search(header)
        msgid = provider.parse_id(header)
        thrid = provider.parse_thread(header)
        internal_dt = internaldate(header)
        if not (uid_match and msgid and thrid and internal_dt):
            continue
        parsed = email.message_from_bytes(body, policy=email.policy.default)
        out[uid_match.group(1)] = Meta(
            msgid,
            thrid,
            internal_dt,
            message_id_of(body),
            sent_date(parsed.get("Date")),
        )
    return out


def _fetch_bodies_batch(
    conn: imaplib.IMAP4_SSL, uids: list[bytes]
) -> dict[bytes, tuple[int | None, bytes]]:
    """Batched FETCH of RFC822.SIZE and the full body. Returns ``{uid: (size, raw)}``.

    ``size`` is the length the server says the message has, so a body shorter than it
    is known to be truncated; None when the response did not carry it. The size may
    arrive before the body literal or after it, so both are read.
    """
    out: dict[bytes, tuple[int | None, bytes]] = {}
    last: bytes | None = None
    items = "(RFC822.SIZE BODY.PEEK[])"
    for entry in uid_fetch(conn, uids, items, "UID FETCH bodies"):
        if isinstance(entry, tuple):
            header, body = entry
            uid_match = _UID_RE.search(header)
            if uid_match:
                last = uid_match.group(1)
                out[last] = (rfc822_size(header), body or b"")
        elif isinstance(entry, bytes) and last is not None:
            size = rfc822_size(entry)
            if size is not None and out[last][0] is None:
                out[last] = (size, out[last][1])
    return out


# --- Run context ---------------------------------------------------------


@dataclass(frozen=True)
class ThreadContext:
    """Per-run parameters shared, read-only, by planning and workers.

    ``dest`` is the store's ``messages/`` (reconcile checks ``downloaded`` against
    it, and a link needs its copy there); ``store_dir`` is its manifest;
    ``staging_dir`` is where bodies are written; ``duplicates_dir`` holds copies set
    aside, which count as got; ``provider`` is the account's mail provider
    (`wicket.provider`). ``exclude`` is the search keys every pass appends: the
    mailing lists the account never fetches (`Provider.exclude_list` over
    `exclusions.excluded_for`).
    """

    dest: Path
    dry_run: bool
    store_dir: Path
    staging_dir: Path
    duplicates_dir: Path
    provider: Provider = GMAIL
    exclude: Search = ()


# --- Discovery + planning ------------------------------------------------


# A row the store holds, with the UTC year of the shard it lives in.
Held = tuple[Row, int]


def _held(store_dir: Path) -> tuple[set[str], dict[str, Held]]:
    """What the store already holds, from one pass over its shards.

    Returns ``(msgids, rows)``: the Gmail msgids fetch must not download (the
    cheap prefilter before any header FETCH) -- every downloaded row's and every
    ``excluded`` row's -- and every row by ``id`` with its shard
    year, from which a message is keyed, linked, and settled in place. A later
    shard wins an ``id`` seen twice, as in ``manifest.load_store``.
    """
    msgids: set[str] = set()
    by_id: dict[str, Held] = {}
    for year, shard in read_shards(store_dir).items():
        for id_, row in shard.items():
            if row.get("msgid") and (row.get("downloaded") or row.get("excluded")):
                msgids.add(str(row["msgid"]))
            by_id[id_] = (row, year)
    return msgids, by_id


def _staged_msgids(staging_dir: Path, provider: Provider) -> set[str]:
    """The provider's msgids already staged, read off their paths (`provider.parse_staged`)."""
    return {
        staged.msgid
        for _path, rel in staged_files(staging_dir)
        if (staged := parse_staged(rel)) is not None and staged.provider is provider
    }


def _duplicate_stems(duplicates_dir: Path) -> set[str]:
    """The file stems set aside as duplicates: each file there is ``<stem>.eml``."""
    if not duplicates_dir.exists():
        return set()
    return {p.stem for p in duplicates_dir.rglob("*.eml") if _is_hex(p.stem)}


@dataclass(frozen=True)
class Got:
    """What a run must not get again: ``msgid in got`` asks it.

    A downloaded or excluded row's msgid, a staged body's, or a duplicate's, whose file
    is named by its provider's `Provider.file_stem` (Gmail's is the msgid itself).
    """

    msgids: set[str]
    stems: set[str]
    provider: Provider = GMAIL

    def __contains__(self, msgid: object) -> bool:
        return isinstance(msgid, str) and (
            msgid in self.msgids or self.provider.file_stem(msgid) in self.stems
        )

    def __len__(self) -> int:
        return len(self.msgids | self.stems)


def _is_hex(text: str) -> bool:
    return bool(text) and all(c in "0123456789abcdef" for c in text)


def _got(ctx: ThreadContext) -> tuple[Got, dict[str, Held]]:
    """What the run must not get again (`Got`), and the store's rows (see `_held`).

    Staged mail has no row field (a catalog merge would drop one), so the disk says.
    """
    msgids, rows = _held(ctx.store_dir)
    msgids |= _staged_msgids(ctx.staging_dir, ctx.provider)
    return Got(msgids, _duplicate_stems(ctx.duplicates_dir), ctx.provider), rows


def _discover_records(  # pylint: disable=too-many-arguments,too-many-locals
    credentials_path: Path,
    search: Search,
    done: Got | set[str],
    limit: int | None,
    *,
    conn: imaplib.IMAP4_SSL | None = None,
    provider: Provider = GMAIL,
) -> tuple[list[Record], int, int]:
    """One-connection discovery into raw per-message records.

    Returns ``(records, total_matches, unheld_count)``: a record for every match
    whose msgid the store does not already hold or exclude, up to ``limit``,
    and how many such matches there were before ``limit`` cut them. Uses ``conn``
    when given (a no-filter run keeps one discovery connection), else opens its own
    and logs it out. A provider with no all-mail folder is searched folder by folder
    (`auth.folders`), each selected read-only; a message in two folders is one match,
    found in the first.
    """
    own = conn is None
    if conn is None:
        conn = open_mailbox(credentials_path, interactive=False, provider=provider)
    try:
        total = 0
        located: dict[str, tuple[str, bytes]] = {}  # msgid -> (folder, uid)
        for folder in visit(conn, provider, folders(conn, provider)):
            search_uids = uid_search(conn, search)
            total += len(search_uids)
            for chunk in chunked(search_uids, BATCH_SIZE):
                for uid, msgid in _fetch_msgids_batch(conn, chunk, provider).items():
                    if msgid in located:
                        total -= 1  # a copy in a second folder: one message
                    else:
                        located[msgid] = (folder, uid)
        new = [where for m, where in located.items() if m not in done]
        unheld = len(new)
        if limit is not None:
            new = new[:limit]
        meta: dict[tuple[str, bytes], Meta] = {}
        for folder in visit(conn, provider, dict.fromkeys(f for f, _uid in new)):
            uids = [uid for f, uid in new if f == folder]
            for chunk in chunked(uids, BATCH_SIZE):
                for uid, row in _fetch_plan_metadata_batch(
                    conn, chunk, provider
                ).items():
                    meta[(folder, uid)] = row
    finally:
        if own:
            logout_quietly(conn)
    records = [Record(uid, *meta[(f, uid)], f) for f, uid in new if (f, uid) in meta]
    return records, total, unheld


def _mailbox_keys(
    records: list[Record], rows: dict[str, Held], dest: Path, provider: Provider
) -> dict[str, str]:
    """Each record's manifest key, by native msgid (``message.mailbox_key``).

    Keyed in the provider's order (Gmail's: its arrival order) against a working copy
    of the store's rows that each keying updates, so two messages with one
    Message-ID in one run get two keys, the first to arrive keeping the bare one.
    """
    work: dict[str, MailRow] = {id_: row for id_, (row, _year) in rows.items()}
    files = message_files(dest)
    requests = [
        KeyRequest(
            message_key(
                message_id=r.message_id, provider=provider.name, native_id=r.msgid
            ),
            r.msgid,
            r.internal_dt.isoformat(),
            r.sent,
        )
        for r in records
    ]
    return mailbox_keys(
        requests, provider=provider, rows=work, held=lambda row: is_held(row, files)
    )


def _plan(
    records: list[Record], ctx: ThreadContext, rows: dict[str, Held]
) -> tuple[list[Row], list[Row]]:
    """Key each record and give it its staged path: ``(pending, linked)``.

    No thread is dropped (fetch files nothing); only a message keyed to a row the
    owner marked ``excluded`` is left out. ``rows`` is the store as ``_held`` returns
    it. Each entry carries ``uid`` / ``folder`` / ``month``, its key, native ``msgid``
    and ``date``, its ``path`` under the staging dir, and ``shard_year`` when its key
    already has a row (a link writes back there).

    ``mailbox_key`` keys a message to a row carrying another msgid only when that
    row is the copy ingest filed (no native id, file on disk, same send time), so
    such an entry is ``linked``: its msgid is recorded on that row and nothing is
    downloaded. Every other entry is ``pending``, downloaded into staging.
    """
    keys = _mailbox_keys(records, rows, ctx.dest, ctx.provider)
    pending: list[Row] = []
    linked: list[Row] = []
    for rec in sorted(records, key=lambda r: r.internal_dt):
        id_ = keys[rec.msgid]
        held = rows.get(id_)
        if held is not None and held[0].get("excluded"):
            continue
        dt = rec.internal_dt
        entry: Row = {
            "uid": rec.uid,
            "folder": rec.folder,
            "month": dt.strftime("%Y-%m"),
            "id": id_,
            "msgid": rec.msgid,
            "date": dt.isoformat(),
            "path": ctx.provider.staged_name(rec.thread_id, rec.msgid, dt),
        }
        if held is not None:
            entry["shard_year"] = held[1]
        if held is not None and held[0].get("msgid") != rec.msgid:
            linked.append(entry)
        else:
            pending.append(entry)
    return pending, linked


def _write_links(store_dir: Path, linked: list[Row]) -> None:
    """Record each linked message's native msgid on the row already holding it.

    Only ``msgid`` is written, into the held row's own shard: its ``stem``, ``date``
    and ``thread_id`` stay as they were filed, and the next run's msgid prefilter now
    sees the message as held.
    """
    by_year: dict[int, list[Row]] = {}
    for entry in linked:
        by_year.setdefault(cast(int, entry["shard_year"]), []).append(entry)
    for year, entries in by_year.items():
        path = shard_path(store_dir, year)
        links = {str(e["id"]): {"msgid": e["msgid"]} for e in entries}
        write_shard(path, merge_settlement(read_shard(path), links))


# --- Per-month body fetch + write ----------------------------------------


@dataclass
class _Month:
    """One month's outcome, reported by a worker to the main thread.

    ``halt`` is set when this month stopped on a HALT it raised (a failed command, a
    short body); ``skipped`` when it never started because a HALT was in force.
    """

    month: str
    written: list[Row]
    halt: Halted | None = None
    skipped: bool = False


def _write_month(
    conn: imaplib.IMAP4_SSL,
    month: str,
    pending: list[Row],
    ctx: ThreadContext,
    credentials_path: Path,
) -> _Month:
    """Fetch a month's pending bodies, one batched FETCH per folder, and stage each .eml.

    A month of automated mail is ~100-200 messages, so a month of one folder is one
    BODY.PEEK[] round-trip; a provider with no all-mail folder selects each folder
    (read-only) first. A body that is empty or shorter than its RFC822.SIZE is
    never written (a staged ``.eml`` is filed as it is, so a truncated one would be
    archived truncated) and writes a HALT. A message the response omits has
    left the mailbox since discovery and is simply not written.
    """
    bodies: dict[tuple[str, bytes], tuple[int | None, bytes]] = {}
    in_month = dict.fromkeys(str(e["folder"]) for e in pending)
    for folder in visit(conn, ctx.provider, in_month):
        uids = [cast(bytes, e["uid"]) for e in pending if e["folder"] == folder]
        for uid, body in _fetch_bodies_batch(conn, uids).items():
            bodies[(folder, uid)] = body
    written: list[Row] = []
    short = 0
    for entry in pending:
        got = bodies.get((str(entry["folder"]), cast(bytes, entry["uid"])))
        if got is None:
            continue
        size, raw = got
        if not raw or (size is not None and len(raw) < size):
            short += 1
            continue
        write_eml(ctx.staging_dir / str(entry["path"]), raw)
        written.append(entry)
    halt = None
    if short:
        halt = write_halt(
            credentials_path,
            f"short body: {short} message(s) of {month} came back shorter than "
            "RFC822.SIZE and were not written",
        )
    return _Month(month, written, halt)


def _month_worker(
    credentials_path: Path,
    ctx: ThreadContext,
    work_q: "Queue[tuple[str, list[Row]] | None]",
    results_q: "Queue[_Month]",
) -> None:
    """Process whole months off the queue on a private IMAP connection.

    Checks the account's HALT file before each month, so after the first HALT the
    queue drains without work (each month reported skipped). A connection
    `open_mailbox` refuses (``Halted``) is not a failure of this worker's months:
    they are skipped, and no second HALT is written. Any other connect failure, or a
    command that is rejected, times out or aborts, writes a HALT; the first
    cause wins. Every month dequeued posts exactly one result, whatever happens, so
    the main thread never waits on a worker that died.
    """
    conn: imaplib.IMAP4_SSL | None = None
    try:
        conn = open_mailbox(credentials_path, interactive=False, provider=ctx.provider)
    except Halted:
        pass
    except AuthError as exc:
        throttle_halt(credentials_path, "a month worker could not connect", exc)
    try:
        while True:
            item = work_q.get()
            if item is None:
                return
            month, pending = item
            if conn is None or halt_in_force(credentials_path) is not None:
                results_q.put(_Month(month, [], skipped=True))
                continue
            try:
                results_q.put(_write_month(conn, month, pending, ctx, credentials_path))
            except IMAP_FAILURES as exc:
                halt = throttle_halt(credentials_path, f"month {month}", exc)
                results_q.put(_Month(month, [], halt=halt))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # Not an IMAP failure (a disk error, a bug), but still a failed month:
                # it HALTs like any other, and the result is posted so nothing hangs.
                halt = write_halt(
                    credentials_path,
                    f"month {month} failed: {type(exc).__name__}",
                )
                results_q.put(_Month(month, [], halt=halt))
    finally:
        if conn is not None:
            logout_quietly(conn)


def _retrieve_months(
    *,
    credentials_path: Path,
    by_month: dict[str, list[Row]],
    ctx: ThreadContext,
    threads: int,
) -> tuple[list[Row], int, Halted | None]:
    """Drive per-month workers; return (written_entries, failed_months, halt).

    ``halt`` is the HALT the months stopped on, or None when every month ran.
    """
    months = sorted(by_month)
    workers = max(1, min(threads, len(months)))
    work_q: "Queue[tuple[str, list[Row]] | None]" = Queue()
    results_q: "Queue[_Month]" = Queue(maxsize=workers * 2)
    for month in months:
        work_q.put((month, by_month[month]))
    for _ in range(workers):
        work_q.put(None)

    pool = [
        Thread(target=_month_worker, args=(credentials_path, ctx, work_q, results_q))
        for _ in range(workers)
    ]
    for thread in pool:
        thread.start()

    written_all: list[Row] = []
    failed = skipped = done = 0
    halt: Halted | None = None
    try:
        for _ in range(len(months)):
            result = results_q.get()
            done += 1
            written_all.extend(result.written)
            if result.skipped:
                skipped += 1
            elif result.halt is not None:
                failed += 1
            if halt is None:
                halt = result.halt
            msg = f"[fetch] {done}/{len(months)} months: {len(written_all)} saved"
            if failed:
                msg += f", {failed} failed"
            if skipped:
                msg += f", {skipped} skipped (halted)"
            logger.info(msg)
    finally:
        for thread in pool:
            thread.join()
    if halt is None:
        halt = halt_in_force(credentials_path)
    return written_all, failed, halt


# --- One pass ------------------------------------------------------------

# ``got``: matches already got (staged, filed, excluded, or set aside as a duplicate);
# ``downloaded``: bodies this run wrote into staging.
COUNT_KEYS = (
    "matched",
    "got",
    "candidates",
    "pending",
    "linked",
    "downloaded",
    "failed",
)


def _counts() -> dict[str, int]:
    return dict.fromkeys(COUNT_KEYS, 0)


def _pass(
    *,
    conn: imaplib.IMAP4_SSL | None,
    search: Search,
    ctx: ThreadContext,
    credentials_path: Path,
    limit: int | None,
    threads: int,
) -> tuple[dict[str, int], Halted | None]:
    """One fetch pass: discover, plan, link, retrieve bodies into staging.

    ``conn`` is the run's discovery connection, or None to open one for this pass.
    Returns the pass's counts and the HALT it stopped on (None when it ran clean).
    A body written before a HALT stays staged: it is got, and ``ingest --staged``
    files it.
    """
    counts = _counts()
    done, rows = _got(ctx)
    try:
        records, total, unheld = _discover_records(
            credentials_path,
            search + ctx.exclude,
            done,
            limit,
            conn=conn,
            provider=ctx.provider,
        )
    except Halted as refused:
        return counts, refused
    except IMAP_FAILURES as exc:
        return counts, throttle_halt(credentials_path, "discovery", exc)
    got = total - unheld
    candidates = unheld if limit is None else min(unheld, limit)
    logger.info(
        "[search] %s matched: %s got, %s to fetch%s",
        total,
        got,
        candidates,
        f" ({unheld - candidates} beyond --max)" if unheld > candidates else "",
    )
    pending, linked = _plan(records, ctx, rows)
    counts.update(
        matched=total,
        got=got,
        candidates=candidates,
        pending=len(pending),
        linked=len(linked),
    )
    if ctx.dry_run:
        return counts, None

    if linked:
        _write_links(ctx.store_dir, linked)

    written: list[Row] = []
    halt: Halted | None = None
    if pending:
        by_month: dict[str, list[Row]] = {}
        for entry in pending:
            by_month.setdefault(str(entry["month"]), []).append(entry)
        logger.info(
            "[fetch] %s to download across %s month(s)", len(pending), len(by_month)
        )
        written, counts["failed"], halt = _retrieve_months(
            credentials_path=credentials_path,
            by_month=by_month,
            ctx=ctx,
            threads=threads,
        )
    counts["downloaded"] = len(written)
    return counts, halt


def _result(
    totals: dict[str, int], passes: list[dict[str, object]], halt: Halted | None
) -> dict[str, object]:
    """The stats a run returns: its counts, one record per pass, and its HALT."""
    return {
        **totals,
        "passes": passes,
        "halted": halt is not None,
        "halt": halt.line if halt is not None else None,
        "halt_file": str(halt.path) if halt is not None else None,
    }


# --- Top-level workers ---------------------------------------------------


def _prepare(ctx: ThreadContext) -> None:
    """Reconcile the store against its messages dir (ADR 0002); a dry run writes nothing.

    The reconcile ground-truths ``downloaded`` against disk, so the got set and
    linking see a true store. Once per run, not per pass.
    """
    if not ctx.dry_run:
        reconcile(ctx.store_dir, ctx.dest)


def download(
    *,
    search: Search,
    ctx: ThreadContext,
    credentials_path: Path,
    limit: int | None,
    threads: int = DEFAULT_THREADS,
) -> dict[str, object]:
    """Run one fetch pass over ``search`` (``--domains``, ``--query`` or ``--last``).

    Credential prompting is the CLI layer's concern; everything here runs strictly
    non-interactive. A throttle in the pass writes the HALT and returns
    ``halted: True`` with the pass's counts.
    """
    _prepare(ctx)
    counts, halt = _pass(
        conn=None,
        search=search,
        ctx=ctx,
        credentials_path=credentials_path,
        limit=limit,
        threads=threads,
    )
    return _result(counts, [{"year": None, "pass": 1, **counts}], halt)


def _this_year() -> int:
    return datetime.now().year


@dataclass(frozen=True)
class RunHooks:
    """What a no-filter run takes from its surroundings, injected so tests do not wait.

    ``this_year`` orders the years, and ``log`` receives one line per year and pass (counts and
    years only; the api writes them to the account's log).
    """

    this_year: Callable[[], int] = _this_year
    log: Callable[[str], None] = field(default=lambda _line: None)


@dataclass
class _Run:
    """What a no-filter run has done so far: totals, one record per pass, the budget."""

    totals: dict[str, int]
    passes: list[dict[str, object]]
    budget: int | None  # --max: candidates the run may still take; None is unbounded

    def add(self, year: int, n: int, counts: dict[str, int], rows: int) -> None:
        """Record one pass of ``year``."""
        for key in COUNT_KEYS:
            self.totals[key] += counts[key]
        self.passes.append({"year": year, "pass": n, **counts, "got_total": rows})
        if self.budget is not None:
            self.budget -= counts["candidates"]

    def spent(self) -> bool:
        """True when ``--max`` has been used up."""
        return self.budget is not None and self.budget <= 0


def _year_order(first: int, this_year: int) -> list[int]:
    """This year, last year, then every older year from ``first``, oldest first."""
    recent = [y for y in (this_year, this_year - 1) if y >= first]
    return recent + list(range(first, this_year - 1))


def _got_count(ctx: ThreadContext) -> int:
    """How many Gmail messages are got (`_got`), recorded after each year's pass.

    Staged mail counts, so a fetch-only run sees its own progress before any
    ``ingest --staged`` files it.
    """
    return len(_got(ctx)[0])


def download_years(
    *,
    ctx: ThreadContext,
    credentials_path: Path,
    limit: int | None,
    threads: int = DEFAULT_THREADS,
    hooks: RunHooks | None = None,
    years: list[int] | None = None,
) -> dict[str, object]:
    """Get every message the provider's whole-mailbox pass walks (`auth.folders`) not
    got and not excluded, until throttled.

    One discovery connection serves the whole run: its first-year lookup and
    every pass's discovery, so no extra LOGIN is made. Years run this year, last
    year, then the rest oldest first from the mailbox's first year (`auth.first_year`);
    none is skipped. Each year is one pass over it: every message in it not got is
    fetched, month by month, unless a HALT is in force first. ``years`` (``--years``)
    replaces that walk with exactly those years in the order given, and the
    first-year lookup is not made. ``limit`` (``--max``) bounds the candidates of the
    whole run.
    Returns the summed counts, ``passes`` (one record per pass), and ``halted`` with
    the HALT's first line and file when a throttle stopped the run.
    """
    hooks = hooks or RunHooks()
    _prepare(ctx)
    run = _Run(totals=_counts(), passes=[], budget=limit)
    try:
        conn = open_mailbox(credentials_path, interactive=False, provider=ctx.provider)
    except Halted as refused:
        return _result(run.totals, run.passes, refused)
    halt: Halted | None = None
    try:
        if years is None:
            try:
                first = first_year(conn, ctx.provider)
            except IMAP_FAILURES as exc:
                first, halt = None, throttle_halt(credentials_path, "first year", exc)
            years = [] if first is None else _year_order(first, hooks.this_year())
        for year in years:
            if halt is not None or run.spent():
                break
            halt = halt_in_force(credentials_path)
            if halt is not None:
                break
            counts, halt = _pass(
                conn=conn,
                search=ctx.provider.year_search(year),
                ctx=ctx,
                credentials_path=credentials_path,
                limit=run.budget,
                threads=threads,
            )
            after = _got_count(ctx)
            run.add(year, 1, counts, after)
            hooks.log(
                f"{year} pass 1: {counts['matched']} matched, "
                f"{counts['candidates']} to fetch, {counts['downloaded']} downloaded, "
                f"{counts['failed']} failed, {counts['linked']} linked; {after} got"
            )
    finally:
        logout_quietly(conn)
    return _result(run.totals, run.passes, halt)
