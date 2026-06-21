"""Download .eml for matching threads, recording settlement in the store.

1. Discover (one connection): run the Gmail search; cheap ``X-GM-MSGID`` FETCH;
   drop msgids the store already records as downloaded; header FETCH (msgid,
   thrid, date, From/To, Message-ID) for the rest.
2. Plan: group by thread, compute the alias-canonical domain from the thread's
   earliest matched message. A thread whose domain is None is skipped and
   re-evaluated cheaply next run. Each message files under
   ``<dest>/<domain>/YYYY-MM/<msgid>.eml``.
3. Retrieve (``threads`` worker connections, one whole month each): batched body
   FETCH, write each ``.eml``.
4. Settle: write ``downloaded`` / ``path`` / ``domain`` into the year-sharded
   store, preserving observation (manifest.merge_settlement).

Read-only on the mailbox (``SELECT`` is read-only). No CLI; the CLI layer
(``wicket.fetch.__main__``) seeds credentials and calls ``download()``.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import cast

from wicket.auth import AuthError, open_mailbox
from wicket.config import DEFAULT_THREADS
from wicket.manifest import (
    Row,
    load_store,
    merge_settlement,
    read_shard,
    shard_path,
    write_shard,
)
from wicket.message import message_key
from wicket.reconcile import compute_domain, reconcile

BATCH_SIZE = 500  # UIDs per IMAP FETCH; bounds memory + command size.
_UID_RE = re.compile(rb"\bUID\s+(\d+)")
_MSGID_RE = re.compile(rb"X-GM-MSGID\s+(\d+)")
_THRID_RE = re.compile(rb"X-GM-THRID\s+(\d+)")
_INTERNALDATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]+)"')

# A discovery record: (uid, msgid, thrid, internal_dt_utc, from, to, message_id).
Record = tuple[bytes, str, str, datetime, str, str, str]


# --- IMAP plumbing -------------------------------------------------------


def _imap_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_internaldate(value: bytes) -> datetime | None:
    """Parse Gmail's INTERNALDATE (e.g. ``22-Mar-2024 14:53:21 +0000``)."""
    try:
        return datetime.strptime(value.decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


def _logout_quietly(conn: imaplib.IMAP4_SSL) -> None:
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def _chunked(items: list[bytes], size: int) -> "list[list[bytes]]":
    return [items[i : i + size] for i in range(0, len(items), size)]


def _search_uids(conn: imaplib.IMAP4_SSL, query: str) -> list[bytes]:
    """Return UIDs matching the Gmail search expression, via X-GM-RAW."""
    typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", _imap_quote(query))
    if typ != "OK" or not data or not data[0]:
        return []
    uids: list[bytes] = data[0].split()
    return uids


def _fetch_msgids_batch(conn: imaplib.IMAP4_SSL, uids: list[bytes]) -> dict[bytes, str]:
    """Cheap batched FETCH of just X-GM-MSGID. Returns ``{uid: msgid_hex}``."""
    if not uids:
        return {}
    uid_set = b",".join(uids).decode("ascii")
    typ, data = conn.uid("FETCH", uid_set, "(X-GM-MSGID)")
    if typ != "OK":
        return {}
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
) -> dict[bytes, tuple[str, str, datetime, str, str, str]]:
    """Batched header FETCH of msgid + thrid + date + From/To + Message-ID.

    Returns ``{uid: (msgid_hex, thrid, internal_dt_utc, from, to, message_id)}``.
    Headers only (no bodies); used to plan domains, month buckets, and the key.
    """
    if not uids:
        return {}
    uid_set = b",".join(uids).decode("ascii")
    typ, data = conn.uid(
        "FETCH",
        uid_set,
        "(X-GM-MSGID X-GM-THRID INTERNALDATE "
        "BODY.PEEK[HEADER.FIELDS (FROM TO MESSAGE-ID)])",
    )
    if typ != "OK":
        return {}
    out: dict[bytes, tuple[str, str, datetime, str, str, str]] = {}
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
        internal_dt = _parse_internaldate(date_match.group(1))
        if internal_dt is None:
            continue
        parsed = email.message_from_bytes(body, policy=email.policy.default)
        out[uid_match.group(1)] = (
            f"{int(msgid_match.group(1)):x}",
            thrid_match.group(1).decode("ascii"),
            internal_dt.astimezone(timezone.utc),
            str(parsed.get("From", "") or ""),
            str(parsed.get("To", "") or ""),
            str(parsed.get("Message-ID", "") or ""),
        )
    return out


def _fetch_bodies_batch(
    conn: imaplib.IMAP4_SSL, uids: list[bytes]
) -> dict[bytes, bytes]:
    """Batched FETCH of full RFC822. Returns ``{uid: raw_bytes}``."""
    if not uids:
        return {}
    uid_set = b",".join(uids).decode("ascii")
    typ, data = conn.uid("FETCH", uid_set, "(BODY.PEEK[])")
    if typ != "OK":
        return {}
    out: dict[bytes, bytes] = {}
    for entry in data:
        if not isinstance(entry, tuple):
            continue
        header, body = entry
        uid_match = _UID_RE.search(header)
        if uid_match:
            out[uid_match.group(1)] = body
    return out


# --- Run context ---------------------------------------------------------


@dataclass(frozen=True)
class ThreadContext:
    """Per-run parameters shared, read-only, by planning and workers."""

    dest: Path
    imap_email: str
    domain_aliases: dict[str, str]
    dry_run: bool
    store_dir: Path | None = None


# --- Discovery + planning ------------------------------------------------


def _downloaded_msgids(store_dir: Path | None) -> set[str]:
    """msgids the store already records as downloaded (cheap-prefilter dedup)."""
    done: set[str] = set()
    if store_dir is None:
        return done
    for row in load_store(store_dir).values():
        if row.get("downloaded") and row.get("msgid"):
            done.add(str(row["msgid"]))
    return done


def _discover_records(
    credentials_path: Path, query: str, done: set[str], limit: int | None
) -> tuple[list[Record], int, int]:
    """One-connection discovery into raw per-message records.

    Returns ``(records, total_matches, candidate_count)`` for every match whose
    msgid the store does not already record as downloaded.
    """
    conn = open_mailbox(credentials_path, interactive=False)
    try:
        search_uids = _search_uids(conn, query)
        total = len(search_uids)
        uid_msgid: dict[bytes, str] = {}
        for chunk in _chunked(search_uids, BATCH_SIZE):
            uid_msgid.update(_fetch_msgids_batch(conn, chunk))
        new_uids = [u for u, m in uid_msgid.items() if m not in done]
        if limit is not None:
            new_uids = new_uids[:limit]
        meta: dict[bytes, tuple[str, str, datetime, str, str, str]] = {}
        for chunk in _chunked(new_uids, BATCH_SIZE):
            meta.update(_fetch_plan_metadata_batch(conn, chunk))
    finally:
        _logout_quietly(conn)
    records: list[Record] = [(uid, *meta[uid]) for uid in new_uids if uid in meta]
    return records, total, len(new_uids)


def _plan(records: list[Record], ctx: ThreadContext) -> tuple[list[Row], list[Row]]:
    """Group records by thread, domain each, and split into (pending, on_disk).

    The domain is computed once per thread from its earliest matched message and
    applied to every message in it. Threads whose domain is None are dropped.
    Each planned entry carries the settlement fields plus planning-only
    ``uid`` / ``month`` / ``year``. ``on_disk`` entries already have their
    ``.eml`` and only need settling; ``pending`` entries need a body fetch.
    """
    by_thread: dict[str, list[Record]] = {}
    for rec in records:
        by_thread.setdefault(rec[2], []).append(rec)

    pending: list[Row] = []
    on_disk: list[Row] = []
    for thrid, msgs in by_thread.items():
        msgs.sort(key=lambda m: m[3])  # earliest matched message first
        domain = compute_domain(
            msgs[0][4], msgs[0][5], ctx.imap_email, ctx.domain_aliases
        )
        if domain is None:
            continue
        for uid, msgid, _thr, dt, _from, _to, message_id in msgs:
            month = dt.strftime("%Y-%m")
            rel = f"{domain}/{month}/{msgid}.eml"
            entry: Row = {
                "uid": uid,
                "month": month,
                "year": dt.year,
                "id": message_key(
                    message_id=message_id, provider="gmail", native_id=msgid
                ),
                "msgid": msgid,
                "thread_id": thrid,
                "date": dt.isoformat(),
                "domain": domain,
                "path": rel,
            }
            (on_disk if (ctx.dest / rel).exists() else pending).append(entry)
    return pending, on_disk


def _settlement_fields(entry: Row) -> Row:
    """The store-bound settlement row for one downloaded message."""
    return {
        "id": entry["id"],
        "msgid": entry["msgid"],
        "thread_id": entry["thread_id"],
        "date": entry["date"],
        "domain": entry["domain"],
        "path": entry["path"],
        "downloaded": True,
    }


def _write_settlement(store_dir: Path, settled: list[Row]) -> None:
    """Merge settlement for downloaded messages into their year shards."""
    by_year: dict[int, list[Row]] = {}
    for entry in settled:
        by_year.setdefault(cast(int, entry["year"]), []).append(entry)
    for year, entries in by_year.items():
        path = shard_path(store_dir, year)
        settlements = {str(e["id"]): _settlement_fields(e) for e in entries}
        write_shard(path, merge_settlement(read_shard(path), settlements))


# --- Per-month body fetch + write ----------------------------------------


def _write_month(conn: imaplib.IMAP4_SSL, pending: list[Row], dest: Path) -> list[Row]:
    """Fetch a month's pending bodies in one batched FETCH and write each .eml.

    Returns the entries whose ``.eml`` was written. A month of automated mail is
    ~100-200 messages, so the whole month is one BODY.PEEK[] round-trip.
    """
    uids = [cast(bytes, e["uid"]) for e in pending]
    bodies = _fetch_bodies_batch(conn, uids)
    written: list[Row] = []
    for entry in pending:
        raw = bodies.get(cast(bytes, entry["uid"]))
        if raw is None:
            continue
        target = dest / str(entry["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        written.append(entry)
    return written


def _month_worker(
    credentials_path: Path,
    dest: Path,
    work_q: "Queue[tuple[str, list[Row]] | None]",
    results_q: "Queue[tuple[str, list[Row] | None]]",
) -> None:
    """Process whole months off the queue on a private IMAP connection."""
    try:
        conn: imaplib.IMAP4_SSL | None = open_mailbox(
            credentials_path, interactive=False
        )
    except (AuthError, imaplib.IMAP4.error, OSError):
        conn = None
    try:
        while True:
            item = work_q.get()
            if item is None:
                return
            month, pending = item
            if conn is None:
                results_q.put((month, None))
                continue
            try:
                results_q.put((month, _write_month(conn, pending, dest)))
            except (imaplib.IMAP4.error, OSError):
                results_q.put((month, None))
    finally:
        if conn is not None:
            _logout_quietly(conn)


def _retrieve_months(
    *,
    credentials_path: Path,
    by_month: dict[str, list[Row]],
    dest: Path,
    threads: int,
) -> tuple[list[Row], int]:
    """Drive per-month workers; return (written_entries, failed_months)."""
    months = sorted(by_month)
    workers = max(1, min(threads, len(months)))
    work_q: "Queue[tuple[str, list[Row]] | None]" = Queue()
    results_q: "Queue[tuple[str, list[Row] | None]]" = Queue(maxsize=workers * 2)
    for month in months:
        work_q.put((month, by_month[month]))
    for _ in range(workers):
        work_q.put(None)

    pool = [
        Thread(target=_month_worker, args=(credentials_path, dest, work_q, results_q))
        for _ in range(workers)
    ]
    for thread in pool:
        thread.start()

    written_all: list[Row] = []
    failed = done = 0
    try:
        for _ in range(len(months)):
            _month, written = results_q.get()
            done += 1
            if written is None:
                failed += 1
            else:
                written_all.extend(written)
            msg = f"[fetch] {done}/{len(months)} months: {len(written_all)} saved"
            if failed:
                msg += f", {failed} failed"
            print(msg, flush=True)
    finally:
        for thread in pool:
            thread.join()
    return written_all, failed


# --- Top-level worker ----------------------------------------------------


def download(
    *,
    query: str,
    ctx: ThreadContext,
    credentials_path: Path,
    limit: int | None,
    threads: int = DEFAULT_THREADS,
) -> dict[str, int]:
    """Run one fetch pass: discover, plan, retrieve bodies, settle into the store.

    Credential prompting is the CLI layer's concern; everything here runs
    strictly non-interactive.
    """
    ctx.dest.mkdir(parents=True, exist_ok=True)
    if ctx.store_dir is not None and not ctx.dry_run:
        # The reconcile runs first (ADR 0002): ground-truth `downloaded` against
        # disk and derive `domain`, so dedup and planning see a true store.
        reconcile(ctx.store_dir, ctx.dest, ctx.imap_email, ctx.domain_aliases)
    done = _downloaded_msgids(ctx.store_dir)
    records, total, candidates = _discover_records(credentials_path, query, done, limit)
    held = total - candidates
    print(
        f"[search] {total} matched: {held} held, {candidates} to fetch",
        flush=True,
    )
    pending, on_disk = _plan(records, ctx)

    if ctx.dry_run:
        return {
            "matched": total,
            "held": held,
            "candidates": candidates,
            "pending": len(pending),
            "on_disk": len(on_disk),
            "downloaded": 0,
            "failed": 0,
        }

    settled = list(on_disk)
    failed = 0
    if pending:
        by_month: dict[str, list[Row]] = {}
        for entry in pending:
            by_month.setdefault(str(entry["month"]), []).append(entry)
        print(
            f"[fetch] {len(pending)} to download across {len(by_month)} month(s)",
            flush=True,
        )
        written, failed = _retrieve_months(
            credentials_path=credentials_path,
            by_month=by_month,
            dest=ctx.dest,
            threads=threads,
        )
        settled.extend(written)

    if ctx.store_dir is not None and settled:
        _write_settlement(ctx.store_dir, settled)
    if failed:
        print(
            f"warning: {failed} month(s) failed; re-run to retry them",
            flush=True,
        )
    return {
        "matched": total,
        "held": held,
        "candidates": candidates,
        "pending": len(pending),
        "on_disk": len(on_disk),
        "downloaded": len(settled) - len(on_disk),
        "failed": failed,
    }
