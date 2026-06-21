"""Mailbox inventory sweep — worker logic, headers only.

For every message in the mailbox (optionally restricted to INTERNALDATE
years), fetch X-GM-MSGID, X-GM-THRID, RFC822.SIZE, INTERNALDATE,
X-GM-LABELS, and the ``From:``, ``To:``, ``Subject:`` and ``Message-ID:``
headers — never bodies — and write one JSON Lines row per message, sharded
by INTERNALDATE year (UTC):

    <inventory-dir>/<YYYY>.jsonl

Row shape (one JSON object per line, ADR 0002 unified schema)::

    {"id": "caa+1x@mail.gmail.com", "msgid": "16f3...",
     "thread_id": "1690...", "date": "2026-06-07T...", "size": 48211,
     "from": "fidelity.alerts@fidelity.com", "to": ["you@gmail.com"],
     "subject": "Your account statement is ready",
     "labels": ["\\\\Inbox"]}

``subject`` is RFC 2047-decoded to plain Unicode ('' when absent). ``id`` is
the provider-neutral key (normalized Message-ID, else ``gmail:<msgid>``)
shared with the archive manifest so the two faces join. These are the
observation-owned fields; the archive manifest owns the settlement fields
(status / domain / path) under the same key.

The inventory is the *observation* face of the mailbox (what exists,
re-swept and replaced whole); the archive manifest is the *settlement*
face (what has been saved or noop'd, append-only). Same key, two write
regimes; they are never merged. Each shard is replaced atomically
(tmp + rename, 0600). A ``years``-restricted sweep touches only the
requested years' shards. (Removing stale year shards on a full sweep is
not yet implemented.)

Utility module: no CLI, no argparse, no main guard. The verb's api
(`wicket.catalog.api`) dispatches into `sweep()`.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from wicket.auth import open_mailbox
from wicket.config import DEFAULT_THREADS
from wicket.manifest import merge_catalog, read_shard, shard_path, write_shard
from wicket.message import message_key

# Gmail answers `UID SEARCH ALL` with a single line listing every UID —
# ~1.2MB at 155k messages — which trips imaplib's hard-coded 1MB line guard
# and kills the full-sweep year discovery. imaplib exposes no public knob,
# so raising the module attribute is the sanctioned workaround; 10MB gives
# ~1.2M-message headroom. (protected-access/attr-defined: stdlib limitation.)
_NEW_MAXLINE = max(getattr(imaplib, "_MAXLINE", 0), 10_000_000)
setattr(imaplib, "_MAXLINE", _NEW_MAXLINE)

BATCH_SIZE = 1000  # UIDs per IMAP FETCH; headers only, so larger than archive's.
_UID_RE = re.compile(rb"\bUID\s+(\d+)")
_MSGID_RE = re.compile(rb"X-GM-MSGID\s+(\d+)")
_THRID_RE = re.compile(rb"X-GM-THRID\s+(\d+)")
_SIZE_RE = re.compile(rb"RFC822\.SIZE\s+(\d+)")
_INTERNALDATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]+)"')
_HEADER_LITERAL_MARKER = b"BODY[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID)]"
_SHARD_RE = re.compile(r"^\d{4}\.jsonl$")


# --- IMAP plumbing -------------------------------------------------------


def _parse_internaldate(value: bytes) -> datetime | None:
    """Parse Gmail's INTERNALDATE (e.g. ``22-Mar-2024 14:53:21 +0000``)."""
    try:
        return datetime.strptime(value.decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


def _chunked(items: list[bytes], size: int) -> Iterator[list[bytes]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _search_all_uids(conn: imaplib.IMAP4_SSL) -> list[bytes]:
    typ, data = conn.uid("SEARCH", "ALL")
    if typ != "OK" or not data or not data[0]:
        return []
    uids: list[bytes] = data[0].split()
    return uids


def _search_year_uids(conn: imaplib.IMAP4_SSL, year: int) -> list[bytes]:
    """UIDs whose INTERNALDATE *UTC* year may be `year`.

    IMAP SINCE/BEFORE compare the stored INTERNALDATE's date portion with
    its timezone disregarded (RFC 3501), so a UTC-year boundary message can
    sit a server-date day outside the year. Widen the window by one day on
    each side; the caller's UTC bucketing drops the overshoot. Sharding
    stays strictly UTC; the search is merely a superset.
    """
    typ, data = conn.uid(
        "SEARCH", "SINCE", f"31-Dec-{year - 1}", "BEFORE", f"02-Jan-{year + 1}"
    )
    if typ != "OK" or not data or not data[0]:
        return []
    uids: list[bytes] = data[0].split()
    return uids


# --- FETCH response parsing ----------------------------------------------


def _tokenize_labels(inner: bytes) -> list[str]:
    """Split the inside of ``X-GM-LABELS (...)`` into label strings.

    Quote-aware: a quoted label may contain spaces and backslash escapes;
    unquoted tokens (``\\\\Inbox``-style system labels) are taken verbatim.
    """
    labels: list[str] = []
    token = bytearray()
    in_quote = False
    escaped = False
    for byte in inner:
        char = bytes([byte])
        if escaped:
            token += char
            escaped = False
        elif in_quote and char == b"\\":
            escaped = True
        elif char == b'"':
            in_quote = not in_quote
        elif char == b" " and not in_quote:
            if token:
                labels.append(token.decode("utf-8", "replace"))
                token = bytearray()
        else:
            token += char
    if token:
        labels.append(token.decode("utf-8", "replace"))
    return labels


def _parse_labels(meta: bytes) -> list[str] | None:
    """Extract the label list from FETCH metadata bytes.

    Returns None when the list cannot be recovered (e.g. a label was
    transmitted as an IMAP literal, which splits the response): the row
    then records ``labels: null`` — an honest gap, never a guess.
    """
    start = meta.find(b"X-GM-LABELS (")
    if start == -1:
        return None
    i = start + len(b"X-GM-LABELS (")
    depth = 1
    in_quote = False
    escaped = False
    inner = bytearray()
    while i < len(meta):
        char = meta[i : i + 1]
        if escaped:
            escaped = False
        elif in_quote and char == b"\\":
            escaped = True
        elif char == b'"':
            in_quote = not in_quote
        elif not in_quote and char == b"(":
            depth += 1
        elif not in_quote and char == b")":
            depth -= 1
            if depth == 0:
                return _tokenize_labels(bytes(inner))
        inner += meta[i : i + 1]
        i += 1
    return None  # unbalanced — labels arrived as a literal; punt.


def _parse_header_block(header_block: bytes) -> tuple[str, list[str], str, str]:
    """Return ``(from_address, to_addresses, subject, message_id)`` from a raw
    FROM+TO+SUBJECT+MESSAGE-ID header block.

    ``email.policy.default`` unfolds continuation lines and decodes RFC 2047
    encoded-words, so the subject is plain Unicode and addresses survive a
    display-name wrapper. ``from`` is the single lowercased address; ``to`` is
    the sorted set of lowercased recipient addresses (what the filing-domain
    rule consumes); ``message_id`` is the raw header (normalized downstream).
    All default to '' / [] when absent or unparseable.
    """
    parsed = email.message_from_bytes(header_block, policy=email.policy.default)
    _, from_addr = email.utils.parseaddr(str(parsed.get("From", "") or ""))
    to_addrs = sorted(
        {
            addr.strip().lower()
            for _, addr in email.utils.getaddresses([str(parsed.get("To", "") or "")])
            if addr
        }
    )
    subject = str(parsed.get("Subject", "") or "")
    message_id = str(parsed.get("Message-ID", "") or "")
    return from_addr.strip().lower(), to_addrs, subject, message_id


def _finalize_record(meta: bytes, header_block: bytes) -> dict[str, object] | None:
    """Turn accumulated FETCH bytes for one message into an inventory row."""
    msgid_match = _MSGID_RE.search(meta)
    thrid_match = _THRID_RE.search(meta)
    size_match = _SIZE_RE.search(meta)
    date_match = _INTERNALDATE_RE.search(meta)
    if not (msgid_match and thrid_match and size_match and date_match):
        return None
    internal_dt = _parse_internaldate(date_match.group(1))
    if internal_dt is None:
        return None
    from_addr, to_addrs, subject, message_id = _parse_header_block(header_block)
    gmail_msgid = f"{int(msgid_match.group(1)):x}"
    return {
        "id": message_key(
            message_id=message_id, provider="gmail", native_id=gmail_msgid
        ),
        "msgid": gmail_msgid,
        "thread_id": thrid_match.group(1).decode("ascii"),
        "date": internal_dt.astimezone(timezone.utc).isoformat(),
        "size": int(size_match.group(1)),
        "from": from_addr,
        "to": to_addrs,
        "subject": subject,
        "labels": _parse_labels(meta),
    }


def _fetch_rows_batch(
    conn: imaplib.IMAP4_SSL, uids: list[bytes]
) -> tuple[list[dict[str, object]], int]:
    """Batched header-only FETCH. Returns ``(rows, parse_skips)``.

    A message's response may span several imaplib items when the reply
    carries more than one literal (rare: a label sent as a literal). The
    accumulator concatenates metadata segments and keeps the literal whose
    preceding segment ends with the ``BODY[HEADER.FIELDS (FROM SUBJECT)]``
    marker as the FROM+SUBJECT header block; a record closes on the
    standalone ``)`` item.
    """
    if not uids:
        return [], 0
    typ, data = conn.uid(
        "FETCH",
        b",".join(uids).decode("ascii"),
        "(X-GM-MSGID X-GM-THRID RFC822.SIZE INTERNALDATE X-GM-LABELS "
        "BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID)])",
    )
    if typ != "OK":
        return [], len(uids)
    rows: list[dict[str, object]] = []
    skips = 0
    meta = bytearray()
    header_block = b""
    for item in data:
        if isinstance(item, tuple):
            meta += item[0]
            if _HEADER_LITERAL_MARKER in item[0][-64:]:
                header_block = item[1]
        elif isinstance(item, bytes):
            meta += item
            if item.endswith(b")"):
                row = _finalize_record(bytes(meta), header_block)
                if row is None:
                    skips += 1
                else:
                    rows.append(row)
                meta = bytearray()
                header_block = b""
    return rows, skips


# --- Per-year worker ------------------------------------------------------


def _logout_quietly(conn: imaplib.IMAP4_SSL) -> None:
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError):
        pass  # logout failure must not mask the sweep result


def _min_year(conn: imaplib.IMAP4_SSL) -> int | None:
    """UTC year of the oldest message in the mailbox (None if empty)."""
    uids = _search_all_uids(conn)
    if not uids:
        return None
    typ, data = conn.uid("FETCH", uids[0].decode("ascii"), "(INTERNALDATE)")
    if typ != "OK":
        return None
    for item in data:
        chunk = item if isinstance(item, bytes) else item[0]
        date_match = _INTERNALDATE_RE.search(chunk)
        if date_match:
            parsed = _parse_internaldate(date_match.group(1))
            if parsed is not None:
                return parsed.astimezone(timezone.utc).year
    return None


def _sweep_year(
    *,
    credentials_path: Path,
    mailbox: str,
    year: int,
    store_dir: Path,
    dry_run: bool,
) -> dict[str, int]:
    """Sweep one year on its own IMAP connection; write (or count) its shard.

    Keeps only rows whose INTERNALDATE-UTC year matches `year` — the
    widened search window (see `_search_year_uids`) means boundary
    messages surface in two adjacent workers, and this filter is what
    guarantees each lands in exactly one shard. Returns per-year stats.
    """
    conn = open_mailbox(credentials_path, interactive=False, mailbox=mailbox)
    rows_for_year: list[dict[str, object]] = []
    parse_skips = 0
    label_misses = 0
    boundary_drops = 0
    try:
        uids = _search_year_uids(conn, year)
        for chunk in _chunked(uids, BATCH_SIZE):
            rows, skips = _fetch_rows_batch(conn, chunk)
            parse_skips += skips
            for row in rows:
                if datetime.fromisoformat(str(row["date"])).year != year:
                    boundary_drops += 1
                    continue
                if row["labels"] is None:
                    label_misses += 1
                rows_for_year.append(row)
    finally:
        _logout_quietly(conn)

    observed = {str(r["id"]): r for r in rows_for_year}
    if dry_run:
        print(f"[dry-run] {year}: {len(observed)} row(s)")
    else:
        path = shard_path(store_dir, year)
        merged = merge_catalog(read_shard(path), observed, complete=True)
        write_shard(path, merged)
        print(f"[catalog] {year}: {len(observed)} observed, {len(merged)} in shard")
    return {
        "messages": len(observed),
        "parse_skips": parse_skips,
        "label_misses": label_misses,
        "boundary_drops": boundary_drops,
    }


# --- Top-level worker ----------------------------------------------------


def sweep(
    *,
    store_dir: Path,
    credentials_path: Path,
    mailbox: str,
    years: list[int] | None,
    dry_run: bool,
    threads: int = DEFAULT_THREADS,
) -> dict[str, int]:
    """Run one inventory sweep, one worker (and IMAP connection) per year.

    Returns a stats dict for the CLI to print. Keys: ``messages`` (rows
    written/counted), ``shards`` (year shards), ``removed`` (always 0;
    stale-shard removal is not yet implemented), ``parse_skips`` (FETCH
    responses that could
    not be parsed into a row), ``label_misses`` (rows recorded with
    ``labels: null``), ``boundary_drops`` (rows a worker discarded because
    their UTC year belongs to the adjacent worker — expected, the widened
    windows overlap by design).

    Credential *prompting* is the CLI layer's concern: the caller seeds
    imap.json before calling. Everything here runs strictly
    non-interactive — a missing/revoked credential raises `AuthError`
    instead of blocking a worker thread on a prompt. A full sweep derives
    the year range from the oldest message's UTC year through the current
    UTC year (stale-shard removal outside that range is not yet implemented).
    """
    if years:
        targets = sorted(set(years))
    else:
        conn = open_mailbox(credentials_path, interactive=False, mailbox=mailbox)
        try:
            first = _min_year(conn)
        finally:
            _logout_quietly(conn)
        if first is None:
            print("[discover] mailbox is empty")
            return {
                "messages": 0,
                "shards": 0,
                "removed": 0,
                "parse_skips": 0,
                "label_misses": 0,
                "boundary_drops": 0,
            }
        targets = list(range(first, datetime.now(timezone.utc).year + 1))

    workers = max(1, min(threads, len(targets)))
    print(
        f"[plan] {len(targets)} year(s) {targets[0]}..{targets[-1]}, "
        f"{workers} thread(s)"
    )

    per_year: list[dict[str, int]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _sweep_year,
                credentials_path=credentials_path,
                mailbox=mailbox,
                year=year,
                store_dir=store_dir,
                dry_run=dry_run,
            )
            for year in targets
        ]
        for future in as_completed(futures):
            per_year.append(future.result())  # re-raises worker failures

    return {
        "messages": sum(s["messages"] for s in per_year),
        "shards": len(per_year),
        "removed": 0,
        "parse_skips": sum(s["parse_skips"] for s in per_year),
        "label_misses": sum(s["label_misses"] for s in per_year),
        "boundary_drops": sum(s["boundary_drops"] for s in per_year),
    }
