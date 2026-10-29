"""Get .eml for matching messages into the store's staging dir; file nothing.

fetch only gets mail. Each body lands at
``<store>/staging/<thread_id>/<msgid>@<INTERNALDATE>.eml`` (`manifest.staged_name`),
and ``ingest --staged`` is the separate command that files it into the archive by
`wicket.filing`'s rules (plan rule 6). Staged mail is not in ``report`` or
``held_messages`` until it is filed.

Two entry points. `download` is one pass over a Gmail search (the ``--domains`` /
``--query`` filter). `download_years` gets every message in All Mail not got and
not excluded: this year, last year, then each older year from All Mail's first,
oldest first; each year repeats passes while the count of got messages grows,
and the run stops at the first throttle (a rejected, timed-out or aborted command,
a failed month, a short body), which writes the account's HALT
(`wicket.auth.write_halt`). A pass:

1. Discover (one connection): run the Gmail search; cheap ``X-GM-MSGID`` FETCH;
   drop msgids already **got** (a downloaded or excluded row's, a staged body's,
   a duplicate's under ``<mail root>/.duplicates/<store>/``); header FETCH (msgid,
   thrid, date, From/To, Message-ID, Date) for the rest.
2. Plan: key each message by ``message.gmail_key``, so two Gmail messages sharing
   a Message-ID get two rows, and give it its staged path. No domain is computed
   and no thread is dropped: a fetch gets anything not excluded. A message the
   store already holds under another name (ingest filed it: same ``id``, same send
   date) is linked, its Gmail msgid written onto that row, and not downloaded
   again. A row the owner marked ``excluded`` is never downloaded.
3. Retrieve (``threads`` worker connections, one whole month each): batched body
   FETCH, write each ``.eml`` into staging. No row is settled.

Read-only on the mailbox (``SELECT`` is read-only). No CLI; the CLI layer
(``wicket.fetch.cli``) seeds credentials and calls ``download()``.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import cast

from wicket.auth import (
    IMAP_FAILURES,
    AuthError,
    Halted,
    current_halt,
    first_year,
    halt_path,
    open_mailbox,
    parse_internaldate,
    throttle_halt,
    write_halt,
)
from wicket.env import DEFAULT_THREADS
from wicket.manifest import (
    Row,
    eml_on_disk,
    merge_settlement,
    parse_staged,
    read_shard,
    shard_path,
    staged_files,
    staged_name,
    store_shards,
    write_eml,
    write_shard,
)
from wicket.message import GmailRow, gmail_key, message_id_of, message_key, sent_date
from wicket.reconcile import reconcile

BATCH_SIZE = 500  # UIDs per IMAP FETCH; bounds memory + command size.
_UID_RE = re.compile(rb"\bUID\s+(\d+)")
_MSGID_RE = re.compile(rb"X-GM-MSGID\s+(\d+)")
_THRID_RE = re.compile(rb"X-GM-THRID\s+(\d+)")
_INTERNALDATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]+)"')
_SIZE_RE = re.compile(rb"RFC822\.SIZE\s+(\d+)")


# A message's planning headers: (msgid, thrid, internal_dt_utc, from, to,
# message_id, sent_utc), where sent_utc is the parsed Date header or None.
Meta = tuple[str, str, datetime, str, str, str, datetime | None]
# A discovery record: (uid, *Meta).
Record = tuple[bytes, str, str, datetime, str, str, str, datetime | None]


# --- IMAP plumbing -------------------------------------------------------


def _imap_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _logout_quietly(conn: imaplib.IMAP4_SSL) -> None:
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def _chunked(items: list[bytes], size: int) -> "list[list[bytes]]":
    return [items[i : i + size] for i in range(0, len(items), size)]


def _require_ok(typ: str, what: str) -> None:
    """A rejected command is a throttle, never an empty answer: raise so it HALTs."""
    if typ != "OK":
        raise imaplib.IMAP4.error(f"{what} answered {typ}")


def _search_uids(conn: imaplib.IMAP4_SSL, query: str) -> list[bytes]:
    """Return UIDs matching the Gmail search expression, via X-GM-RAW."""
    typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", _imap_quote(query))
    _require_ok(typ, "UID SEARCH")
    if not data or not data[0]:
        return []
    uids: list[bytes] = data[0].split()
    return uids


def _fetch_msgids_batch(conn: imaplib.IMAP4_SSL, uids: list[bytes]) -> dict[bytes, str]:
    """Cheap batched FETCH of just X-GM-MSGID. Returns ``{uid: msgid_hex}``."""
    if not uids:
        return {}
    uid_set = b",".join(uids).decode("ascii")
    typ, data = conn.uid("FETCH", uid_set, "(X-GM-MSGID)")
    _require_ok(typ, "UID FETCH X-GM-MSGID")
    out: dict[bytes, str] = {}
    for entry in data:
        chunk = entry if isinstance(entry, bytes) else entry[0]
        uid_match = _UID_RE.search(chunk)
        msgid_match = _MSGID_RE.search(chunk)
        if uid_match and msgid_match:
            out[uid_match.group(1)] = f"{int(msgid_match.group(1)):x}"
    return out


def _fetch_plan_metadata_batch(
    conn: imaplib.IMAP4_SSL, uids: list[bytes]
) -> dict[bytes, Meta]:
    """Batched header FETCH of msgid + thrid + date + From/To + Message-ID + Date.

    Returns ``{uid: (msgid_hex, thrid, internal_dt_utc, from, to, message_id,
    sent_utc)}``. Headers only (no bodies); used to plan domains, month buckets,
    the key, and the send date that links a copy the store already holds.
    """
    if not uids:
        return {}
    uid_set = b",".join(uids).decode("ascii")
    typ, data = conn.uid(
        "FETCH",
        uid_set,
        "(X-GM-MSGID X-GM-THRID INTERNALDATE "
        "BODY.PEEK[HEADER.FIELDS (FROM TO MESSAGE-ID DATE)])",
    )
    _require_ok(typ, "UID FETCH headers")
    out: dict[bytes, Meta] = {}
    for entry in data:
        if not isinstance(entry, tuple):
            continue
        header, body = entry
        uid_match = _UID_RE.search(header)
        msgid_match = _MSGID_RE.search(header)
        thrid_match = _THRID_RE.search(header)
        date_match = _INTERNALDATE_RE.search(header)
        if not (uid_match and msgid_match and thrid_match and date_match):
            continue
        internal_dt = parse_internaldate(date_match.group(1))
        if internal_dt is None:
            continue
        parsed = email.message_from_bytes(body, policy=email.policy.default)
        out[uid_match.group(1)] = (
            f"{int(msgid_match.group(1)):x}",
            thrid_match.group(1).decode("ascii"),
            internal_dt.astimezone(timezone.utc),
            str(parsed.get("From", "") or ""),
            str(parsed.get("To", "") or ""),
            message_id_of(body),
            sent_date(parsed.get("Date")),
        )
    return out


def _fetch_bodies_batch(
    conn: imaplib.IMAP4_SSL, uids: list[bytes]
) -> dict[bytes, tuple[int | None, bytes]]:
    """Batched FETCH of RFC822.SIZE and the full body. Returns ``{uid: (size, raw)}``.

    ``size`` is the length Gmail says the message has, so a body shorter than it is
    known to be truncated; None when the response did not carry it. The size may
    arrive before the body literal or after it, so both are read.
    """
    if not uids:
        return {}
    uid_set = b",".join(uids).decode("ascii")
    typ, data = conn.uid("FETCH", uid_set, "(RFC822.SIZE BODY.PEEK[])")
    _require_ok(typ, "UID FETCH bodies")
    out: dict[bytes, tuple[int | None, bytes]] = {}
    last: bytes | None = None
    for entry in data:
        if isinstance(entry, tuple):
            header, body = entry
            uid_match = _UID_RE.search(header)
            if uid_match:
                last = uid_match.group(1)
                size = _SIZE_RE.search(header)
                out[last] = (int(size.group(1)) if size else None, body or b"")
        elif isinstance(entry, bytes) and last is not None:
            size = _SIZE_RE.search(entry)
            if size and out[last][0] is None:
                out[last] = (int(size.group(1)), out[last][1])
    return out


# --- Run context ---------------------------------------------------------


@dataclass(frozen=True)
class ThreadContext:
    """Per-run parameters shared, read-only, by planning and workers.

    ``dest`` is the archive (reconcile checks ``downloaded`` against it, and a link
    needs its copy there); ``staging_dir`` is where bodies are written;
    ``duplicates_dir`` holds copies set aside, which count as got.
    """

    dest: Path
    imap_email: str
    domain_aliases: dict[str, str]
    dry_run: bool
    store_dir: Path | None = None
    staging_dir: Path | None = None
    duplicates_dir: Path | None = None


# --- Discovery + planning ------------------------------------------------


# A row the store holds, with the UTC year of the shard it lives in.
Held = tuple[Row, int]


def _held(store_dir: Path | None) -> tuple[set[str], dict[str, Held]]:
    """What the store already holds, from one pass over its shards.

    Returns ``(msgids, rows)``: the Gmail msgids fetch must not download (the
    cheap prefilter before any header FETCH) -- every downloaded row's and every
    ``excluded`` row's -- and every row of a year shard by ``id`` with its shard
    year, from which a message is keyed, linked, and settled in place. A later
    shard wins an ``id`` seen twice, as in ``manifest.load_store``.
    """
    msgids: set[str] = set()
    by_id: dict[str, Held] = {}
    if store_dir is None:
        return msgids, by_id
    for shard in store_shards(store_dir):
        year = int(shard.stem) if shard.stem.isdigit() else None
        for id_, row in read_shard(shard).items():
            if row.get("msgid") and (row.get("downloaded") or row.get("excluded")):
                msgids.add(str(row["msgid"]))
            if year is not None:  # a settlement or link writes back into a year shard
                by_id[id_] = (row, year)
    return msgids, by_id


def _staged_msgids(staging_dir: Path | None) -> set[str]:
    """Gmail msgids already staged, read off their paths (`manifest.parse_staged`)."""
    if staging_dir is None:
        return set()
    return {
        parsed[1]
        for _path, rel in staged_files(staging_dir)
        if (parsed := parse_staged(rel)) is not None
    }


def _duplicate_msgids(duplicates_dir: Path | None) -> set[str]:
    """Gmail msgids set aside as duplicates: each file there is ``<msgid>.eml``."""
    if duplicates_dir is None or not duplicates_dir.exists():
        return set()
    return {p.stem for p in duplicates_dir.rglob("*.eml") if _is_hex(p.stem)}


def _is_hex(text: str) -> bool:
    return bool(text) and all(c in "0123456789abcdef" for c in text)


def _got(ctx: ThreadContext) -> tuple[set[str], dict[str, Held]]:
    """What the run must not get again, and the store's rows (see `_held`).

    Got is a downloaded or excluded row's msgid, a staged body's, or a duplicate's:
    staged mail has no row field (a catalog merge would drop one), so the disk says.
    """
    msgids, rows = _held(ctx.store_dir)
    msgids |= _staged_msgids(ctx.staging_dir)
    msgids |= _duplicate_msgids(ctx.duplicates_dir)
    return msgids, rows


def _discover_records(
    credentials_path: Path,
    query: str,
    done: set[str],
    limit: int | None,
    conn: imaplib.IMAP4_SSL | None = None,
) -> tuple[list[Record], int, int]:
    """One-connection discovery into raw per-message records.

    Returns ``(records, total_matches, unheld_count)``: a record for every match
    whose msgid the store does not already hold or exclude, up to ``limit``,
    and how many such matches there were before ``limit`` cut them. Uses ``conn``
    when given (a no-filter run keeps one discovery connection), else opens its own
    and logs it out.
    """
    own = conn is None
    if conn is None:
        conn = open_mailbox(credentials_path, interactive=False)
    try:
        search_uids = _search_uids(conn, query)
        total = len(search_uids)
        uid_msgid: dict[bytes, str] = {}
        for chunk in _chunked(search_uids, BATCH_SIZE):
            uid_msgid.update(_fetch_msgids_batch(conn, chunk))
        new_uids = [u for u, m in uid_msgid.items() if m not in done]
        unheld = len(new_uids)
        if limit is not None:
            new_uids = new_uids[:limit]
        meta: dict[bytes, Meta] = {}
        for chunk in _chunked(new_uids, BATCH_SIZE):
            meta.update(_fetch_plan_metadata_batch(conn, chunk))
    finally:
        if own:
            _logout_quietly(conn)
    records: list[Record] = [(uid, *meta[uid]) for uid in new_uids if uid in meta]
    return records, total, unheld


def _gmail_keys(
    records: list[Record], rows: dict[str, Held], dest: Path
) -> dict[str, str]:
    """Each record's manifest key, by Gmail msgid (``message.gmail_key``).

    Keyed in Gmail-id order (Gmail's arrival order) against a working copy of
    the store's rows that each keying updates, so two messages with one
    Message-ID in one run get two keys, the first to arrive keeping the bare one.
    """
    work: dict[str, GmailRow] = {id_: row for id_, (row, _year) in rows.items()}

    def held(row: GmailRow) -> bool:
        return bool(row.get("downloaded")) and eml_on_disk(row, dest)

    keys: dict[str, str] = {}
    for rec in sorted(records, key=lambda r: int(r[1], 16)):
        msgid, message_id, sent = rec[1], rec[6], rec[7]
        keys[msgid] = gmail_key(
            key=message_key(message_id=message_id, provider="gmail", native_id=msgid),
            gmail_id=msgid,
            sent=sent,
            rows=work,
            held=held,
        )
    return keys


def _plan(
    records: list[Record], ctx: ThreadContext, rows: dict[str, Held] | None = None
) -> list[Row]:
    """Key each record and give it its staged path: the messages to get.

    No domain is computed and no thread is dropped (fetch files nothing); only a
    message keyed to a row the owner marked ``excluded`` is left out. ``rows`` is
    the store as ``_held`` returns it, read from ``ctx.store_dir`` when not given.
    Each entry carries ``uid`` / ``month`` / ``year``, its key and Gmail ids, its
    ``path`` under the staging dir, and ``shard_year`` when its key already has a
    row (a link writes back there).
    """
    if rows is None:
        rows = _held(ctx.store_dir)[1]
    keys = _gmail_keys(records, rows, ctx.dest)
    pending: list[Row] = []
    for uid, msgid, thrid, dt, _from, _to, _message_id, _sent in sorted(
        records, key=lambda r: r[3]
    ):
        id_ = keys[msgid]
        held = rows.get(id_)
        if held is not None and held[0].get("excluded"):
            continue
        entry: Row = {
            "uid": uid,
            "month": dt.strftime("%Y-%m"),
            "year": dt.year,
            "id": id_,
            "msgid": msgid,
            "thread_id": thrid,
            "date": dt.isoformat(),
            "path": staged_name(thrid, msgid, dt),
        }
        if held is not None:
            entry["shard_year"] = held[1]
        pending.append(entry)
    return pending


def _split_linked(
    entries: list[Row], rows: dict[str, Held]
) -> tuple[list[Row], list[Row]]:
    """Split planned entries into (to_settle, linked).

    ``gmail_key`` keys a message to a row carrying another msgid only when that
    row is the copy ingest filed (no Gmail id, file on disk, same send time), so
    such an entry is a link: its msgid is recorded on that row and nothing is
    downloaded. Every other entry is downloaded into staging.
    """
    to_settle: list[Row] = []
    linked: list[Row] = []
    for entry in entries:
        held = rows.get(str(entry["id"]))
        if held is not None and held[0].get("msgid") != entry["msgid"]:
            linked.append(entry)
        else:
            to_settle.append(entry)
    return to_settle, linked


def _write_links(store_dir: Path, linked: list[Row]) -> None:
    """Record each linked message's Gmail msgid on the row already holding it.

    Only ``msgid`` is written, into the held row's own shard: its ``path``,
    ``domain``, ``date`` and ``thread_id`` stay as they were filed, and the next
    run's msgid prefilter now sees the message as held.
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
    """Fetch a month's pending bodies in one batched FETCH and stage each .eml.

    A month of automated mail is ~100-200 messages, so the whole month is one
    BODY.PEEK[] round-trip. A body that is empty or shorter than its RFC822.SIZE is
    never written (a staged ``.eml`` is filed as it is, so a truncated one would be
    archived truncated) and writes a HALT. A message the response omits has
    left the mailbox since discovery and is simply not written.
    """
    staging = cast(Path, ctx.staging_dir)  # `_prepare` refused a run without one
    bodies = _fetch_bodies_batch(conn, [cast(bytes, e["uid"]) for e in pending])
    written: list[Row] = []
    short = 0
    for entry in pending:
        got = bodies.get(cast(bytes, entry["uid"]))
        if got is None:
            continue
        size, raw = got
        if not raw or (size is not None and len(raw) < size):
            short += 1
            continue
        write_eml(staging / str(entry["path"]), raw)
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
        conn = open_mailbox(credentials_path, interactive=False)
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
            if conn is None or halt_path(credentials_path).exists():
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
            _logout_quietly(conn)


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
            print(msg, flush=True)
    finally:
        for thread in pool:
            thread.join()
    if halt is None and halt_path(credentials_path).exists():
        halt = current_halt(credentials_path)
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
    query: str,
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
            credentials_path, query, done, limit, conn
        )
    except Halted as refused:
        return counts, refused
    except IMAP_FAILURES as exc:
        return counts, throttle_halt(credentials_path, "discovery", exc)
    got = total - unheld
    candidates = unheld if limit is None else min(unheld, limit)
    print(
        f"[search] {total} matched: {got} got, {candidates} to fetch"
        + (f" ({unheld - candidates} beyond --max)" if unheld > candidates else ""),
        flush=True,
    )
    pending, linked = _split_linked(_plan(records, ctx, rows), rows)
    counts.update(
        matched=total,
        got=got,
        candidates=candidates,
        pending=len(pending),
        linked=len(linked),
    )
    if ctx.dry_run:
        return counts, None

    if ctx.store_dir is not None and linked:
        _write_links(ctx.store_dir, linked)

    written: list[Row] = []
    halt: Halted | None = None
    if pending:
        by_month: dict[str, list[Row]] = {}
        for entry in pending:
            by_month.setdefault(str(entry["month"]), []).append(entry)
        print(
            f"[fetch] {len(pending)} to download across {len(by_month)} month(s)",
            flush=True,
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
    """Make the archive dir and reconcile the store against it (ADR 0002).

    The reconcile ground-truths `downloaded` against disk and derives `domain`, so
    the got set and linking see a true store. Once per run, not per pass. Raises
    ``ValueError`` for a real run with no staging dir: there is nowhere to write a
    body.
    """
    if ctx.staging_dir is None and not ctx.dry_run:
        raise ValueError("fetch needs a staging dir to write into")
    ctx.dest.mkdir(parents=True, exist_ok=True)
    if ctx.store_dir is not None and not ctx.dry_run:
        reconcile(ctx.store_dir, ctx.dest, ctx.imap_email, ctx.domain_aliases)


def download(
    *,
    query: str,
    ctx: ThreadContext,
    credentials_path: Path,
    limit: int | None,
    threads: int = DEFAULT_THREADS,
) -> dict[str, object]:
    """Run one fetch pass over ``query`` (the ``--domains`` / ``--query`` filter).

    Credential prompting is the CLI layer's concern; everything here runs strictly
    non-interactive. A throttle in the pass writes the HALT and returns
    ``halted: True`` with the pass's counts.
    """
    _prepare(ctx)
    counts, halt = _pass(
        conn=None,
        query=query,
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


def _year_query(year: int) -> str:
    return f"after:{year}/01/01 before:{year + 1}/01/01"


def _got_count(ctx: ThreadContext) -> int:
    """How many Gmail messages are got (`_got`), recorded after each year's pass.

    Staged mail counts, so a fetch-only run sees its own progress before any
    ``ingest --staged`` files it.
    """
    return len(_got(ctx)[0])


@dataclass(frozen=True)
class _YearJob:
    """The fixed inputs of every pass in a no-filter run."""

    conn: imaplib.IMAP4_SSL
    ctx: ThreadContext
    credentials_path: Path
    threads: int
    hooks: RunHooks


def _year(job: _YearJob, year: int, run: _Run) -> Halted | None:
    """One pass over the year: every message in it not got is fetched, month by month.

    Returns the HALT the pass stopped on, or None.
    """
    cp = job.credentials_path
    if halt_path(cp).exists():
        return current_halt(cp)
    counts, halt = _pass(
        conn=job.conn,
        query=_year_query(year),
        ctx=job.ctx,
        credentials_path=cp,
        limit=run.budget,
        threads=job.threads,
    )
    after = _got_count(job.ctx)
    run.add(year, 1, counts, after)
    job.hooks.log(
        f"{year} pass 1: {counts['matched']} matched, {counts['candidates']} to fetch, "
        f"{counts['downloaded']} downloaded, {counts['failed']} failed, "
        f"{counts['linked']} linked; {after} got"
    )
    return halt


def download_years(
    *,
    ctx: ThreadContext,
    credentials_path: Path,
    limit: int | None,
    threads: int = DEFAULT_THREADS,
    hooks: RunHooks | None = None,
) -> dict[str, object]:
    """Get every message in All Mail not got and not excluded, until throttled.

    One discovery connection serves the whole run: its first-year lookup and
    every pass's discovery, so no extra LOGIN is made. Years
    run this year, last year, then the rest oldest first from All Mail's first year;
    none is skipped. ``limit`` (``--max``) bounds the candidates of the whole run.
    Returns the summed counts, ``passes`` (one record per pass), and ``halted`` with
    the HALT's first line and file when a throttle stopped the run.
    """
    hooks = hooks or RunHooks()
    _prepare(ctx)
    run = _Run(totals=_counts(), passes=[], budget=limit)
    try:
        conn = open_mailbox(credentials_path, interactive=False)
    except Halted as refused:
        return _result(run.totals, run.passes, refused)
    halt: Halted | None = None
    try:
        try:
            first = first_year(conn)
        except IMAP_FAILURES as exc:
            first, halt = None, throttle_halt(credentials_path, "first year", exc)
        years = [] if first is None else _year_order(first, hooks.this_year())
        job = _YearJob(conn, ctx, credentials_path, threads, hooks)
        for year in years:
            if halt is not None or run.spent():
                break
            halt = _year(job, year, run)
    finally:
        _logout_quietly(conn)
    return _result(run.totals, run.passes, halt)
