"""Mailbox inventory sweep — worker logic, headers only.

For every message in the mailbox (optionally restricted to INTERNALDATE years), fetch
the provider's native id and thread (Gmail's X-GM-MSGID and X-GM-THRID, Fastmail's
EMAILID and THREADID), RFC822.SIZE, INTERNALDATE, the labels (Gmail's X-GM-LABELS; on
Fastmail the folders holding it), and the ``From:``, ``To:``, ``Subject:``,
``Message-ID:``, ``Date:`` and ``List-Id:`` headers — never bodies — and write one JSON Lines
row per message, sharded by INTERNALDATE year (UTC):

    <inventory-dir>/<YYYY>.jsonl

Row shape (one JSON object per line, ADR 0002 unified schema)::

    {"id": "<message-id>", "msgid": "<gmail-msgid>",
     "thread_id": "<gmail-thrid>", "date": "<internaldate, `ISO-8601`>",
     "size": <bytes>,
     "from": "sender@example.com", "to": ["you@example.com"],
     "subject": "Your account statement is ready",
     "labels": ["\\\\Inbox"], "list_id": "news.example.com"}

``subject`` is RFC 2047-decoded to plain Unicode ('' when absent). ``list_id`` is the
``List-Id`` header as `exclusions.normalize_list_id` reads it, and the key is absent when the
message has none (never null): a fact about the message, which report reads against
today's excluded-lists map (`manifest.is_excluded`). ``id`` is
the provider-neutral key (normalized Message-ID, else ``gmail:<msgid>``)
shared with the archive manifest so the two faces join; a second Gmail message
with the same Message-ID is keyed ``<key>|gmail:<msgid>`` (``message.mailbox_key``).
The workers only observe: the main thread keys every observation against the
whole store, then merges each into the year shard its row already lives in. These are the
observation-owned fields; the archive manifest owns the settlement fields
(status / domain / path) under the same key.

The inventory is the *observation* face of the mailbox (what exists,
re-swept and replaced whole); the archive manifest is the *settlement*
face (what has been saved or noop'd, append-only). Same key, two write
regimes; they are never merged. Each shard is replaced atomically
(tmp + rename, `0600`). A ``years``-restricted sweep replaces only the
requested years' shards; a message whose row lives in another year's shard is
merged there without dropping anything. (Removing stale year shards on a full sweep is
not yet implemented.)

Utility module: no CLI, no argparse, no main guard. The verb's api
(`wicket.root.api`) dispatches into `sweep()`.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, cast

from wicket.auth import (
    IMAP_FAILURES,
    first_year,
    folders,
    open_mailbox,
    parse_internaldate,
    select_folder,
    throttle_halt,
)
from wicket.env import DEFAULT_THREADS
from wicket.exclusions import normalize_list_id
from wicket.manifest import (
    Shard,
    eml_on_disk,
    merge_catalog,
    message_files,
    read_shard,
    shard_path,
    store_shards,
    write_shard,
)
from wicket.message import (
    KeyRequest,
    MailRow,
    address_list,
    attachment_name,
    mailbox_keys,
    message_id_of,
    message_key,
    rfc2231_param,
    sent_date,
)
from wicket.provider import GMAIL, Provider

BATCH_SIZE = 1000  # UIDs per IMAP FETCH; headers only, so larger than archive's.
_SIZE_RE = re.compile(rb"RFC822\.SIZE\s+(\d+)")
_INTERNALDATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]+)"')
# The headers catalog reads. The FETCH and the marker its reply carries are both made from
# this one tuple, so they cannot drift apart.
_HEADER_FIELDS = ("FROM", "TO", "CC", "BCC", "SUBJECT", "MESSAGE-ID", "DATE", "LIST-ID")
_HEADER_ITEM = f"HEADER.FIELDS ({' '.join(_HEADER_FIELDS)})"
_HEADER_LITERAL_MARKER = f"BODY[{_HEADER_ITEM}]".encode("ascii")
# A literal's ``{size}`` at the end of the segment that announces it.
_LITERAL_SIZE_RE = re.compile(rb"\{\d+\}\s*$")
# Progress, one line per step; the CLI's `main()` shows it on stderr.
logger = logging.getLogger(__name__)


# --- IMAP plumbing -------------------------------------------------------


def _quoted(literal: bytes) -> bytes:
    """An IMAP literal as a quoted string, so the parsers see one token, not ``{n}``."""
    return b'"' + literal.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'


def _chunked(items: list[bytes], size: int) -> Iterator[list[bytes]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _search_year_uids(conn: imaplib.IMAP4_SSL, year: int) -> list[bytes]:
    """UIDs whose INTERNALDATE *UTC* year may be `year`.

    IMAP SINCE/BEFORE compare the stored INTERNALDATE's date portion with
    its timezone disregarded (`RFC 3501`), so a UTC-year boundary message can
    sit a server-date day outside the year. Widen the window by one day on
    each side; the caller's UTC bucketing drops the overshoot. Sharding
    stays strictly UTC; the search is merely a superset.
    """
    typ, data = conn.uid(
        "SEARCH", "SINCE", f"31-Dec-{year - 1}", "BEFORE", f"02-Jan-{year + 1}"
    )
    if typ != "OK":
        raise imaplib.IMAP4.error(f"UID SEARCH {year} answered {typ}")
    if not data or not data[0]:
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


def _parse_labels(meta: bytes, item: str) -> list[str] | None:
    """Extract the label list (FETCH item ``item``) from FETCH metadata bytes.

    Returns None when the list cannot be recovered (e.g. a label was
    transmitted as an IMAP literal, which splits the response): the row
    then records ``labels: null`` — an honest gap, never a guess.
    """
    opening = f"{item} (".encode("ascii")
    start = meta.find(opening)
    if start == -1:
        return None
    i = start + len(opening)
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


def _parse_header_block(
    header_block: bytes,
) -> tuple[str, list[str], str, str, datetime | None, str, list[str], list[str]]:
    """Return ``(from_address, to_addresses, subject, message_id, sent, list_id, cc,
    bcc)`` from a raw header block (`_HEADER_FIELDS`). ``cc`` and ``bcc`` are sorted
    lower-case addr-specs (`message.address_list`), like ``to``.

    ``email.policy.default`` unfolds continuation lines and decodes RFC 2047
    encoded-words, so the subject is plain Unicode and addresses survive a
    display-name wrapper. ``from`` is the single lowercased address; ``to`` is
    the sorted set of lowercased recipient addresses (what the filing-domain
    rule consumes); ``message_id`` is the raw header (normalized downstream),
    read by ``message.message_id_of`` rather than ``policy.default``, which
    cuts some ids short; ``sent`` is the ``Date:`` header (``message.sent_date``);
    ``list_id`` is the ``List-Id:`` header (``exclusions.normalize_list_id``).
    All default to '' / [] / None when absent or unparseable.
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
    message_id = message_id_of(header_block)
    sent = sent_date(parsed.get("Date"))
    list_id = normalize_list_id(str(parsed.get("List-Id", "") or ""))
    cc = address_list(parsed.get_all("Cc", []))
    bcc = address_list(parsed.get_all("Bcc", []))
    return (
        from_addr.strip().lower(),
        to_addrs,
        subject,
        message_id,
        sent,
        list_id,
        cc,
        bcc,
    )


# --- BODYSTRUCTURE: the attachment names (ADR 0002 §5, amended 2026-11-14) ---------

_BODYSTRUCTURE_RE = re.compile(rb"\bBODYSTRUCTURE\s+\(")
_TOKEN_RE = re.compile(rb'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def _sexpr(data: bytes, start: int) -> tuple[list[object], int]:
    """The parenthesized IMAP list opening at ``data[start]``, as nested lists.

    A quoted string is unescaped and decoded (UTF-8, a bad byte becoming U+FFFD);
    ``NIL`` is None; any other atom is kept as text. Raises ``ValueError`` when the
    list does not close.
    """
    stack: list[list[object]] = []
    i = start
    while True:
        match = _TOKEN_RE.match(data, i)
        if match is None:
            raise ValueError("unbalanced BODYSTRUCTURE")
        i = match.end()
        opening, closing, quoted, atom = match.groups()
        if opening:
            stack.append([])
            continue
        if closing:
            done = stack.pop()
            if not stack:
                return done, i
            stack[-1].append(done)
            continue
        if quoted is not None:
            value: object = re.sub(rb"\\(.)", rb"\1", quoted).decode("utf-8", "replace")
        else:
            text = atom.decode("utf-8", "replace")
            value = None if text.upper() == "NIL" else text
        stack[-1].append(value)


def _params(value: object) -> dict[str, str]:
    """An IMAP parameter list (``("name" "a.pdf" ...)``) as lower-case keys to values."""
    if not isinstance(value, list):
        return {}
    pairs = zip(value[0::2], value[1::2])
    return {str(k).lower(): str(v) for k, v in pairs if k is not None and v is not None}


def _part_names(node: list[object], names: list[str]) -> None:
    """Append the file name of each leaf part under ``node``, in order.

    The rule `message.attachment_names` applies to a parsed message: the
    disposition's ``filename``, else the type's ``name``; a ``message/rfc822`` part is
    walked into (its body is element 8), never named itself.
    """
    if node and isinstance(node[0], list):  # multipart: its parts, then the subtype
        for child in node:
            if not isinstance(child, list):
                break
            _part_names(child, names)
        return
    kind = [str(x or "").lower() for x in node[:2]]
    enveloped = kind in (["message", "rfc822"], ["message", "global"])
    if enveloped and len(node) > 8 and isinstance(node[8], list):
        _part_names(node[8], names)
        return
    # The disposition follows the fields each type adds: text has a line count;
    # message/rfc822 and message/global an envelope, a body and a line count.
    at = 9 if kind[:1] == ["text"] else 11 if enveloped else 8
    disposition = node[at] if len(node) > at else None
    found = None
    if isinstance(disposition, list) and len(disposition) > 1:
        found = rfc2231_param(_params(disposition[1]), "filename")
    if found is None and len(node) > 2:
        found = rfc2231_param(_params(node[2]), "name")
    name = attachment_name(found)
    if name is not None:
        names.append(name)


def _attachments(meta: bytes) -> list[str] | None:
    """The attachment names a FETCH reply's ``BODYSTRUCTURE`` declares; None if absent
    or unreadable (an honest gap, as for labels)."""
    match = _BODYSTRUCTURE_RE.search(meta)
    if match is None:
        return None
    try:
        tree, _ = _sexpr(meta, match.end() - 1)
    except (ValueError, IndexError):
        return None
    names: list[str] = []
    _part_names(tree, names)
    return names


def _finalize_record(
    meta: bytes, header_block: bytes, provider: Provider = GMAIL
) -> dict[str, object] | None:
    """Turn accumulated FETCH bytes for one message into an inventory row.

    ``id`` is the message's ``message_key``; the planning-only ``_sent`` (its
    Date) lets the main thread key it with ``mailbox_key`` and is never stored.
    """
    msgid = provider.parse_id(meta)
    thread_id = provider.parse_thread(meta)
    size_match = _SIZE_RE.search(meta)
    date_match = _INTERNALDATE_RE.search(meta)
    if not (msgid and thread_id and size_match and date_match):
        return None
    internal_dt = parse_internaldate(date_match.group(1))
    if internal_dt is None:
        return None
    from_addr, to_addrs, subject, message_id, sent, list_id, cc, bcc = (
        _parse_header_block(header_block)
    )
    labels_item = provider.labels_item
    row: dict[str, object] = {
        "id": message_key(
            message_id=message_id, provider=provider.name, native_id=msgid
        ),
        "msgid": msgid,
        "thread_id": thread_id,
        "date": internal_dt.astimezone(timezone.utc).isoformat(),
        "size": int(size_match.group(1)),
        "from": from_addr,
        "to": to_addrs,
        "cc": cc,
        "bcc": bcc,
        "subject": subject,
        "labels": _parse_labels(meta, labels_item) if labels_item else None,
        "attachments": _attachments(meta),
        "_sent": sent,
    }
    if list_id:
        row["list_id"] = list_id
    return row


def _fetch_items(provider: Provider) -> str:
    """The header-only FETCH items: native id, thread, size, date, labels, headers."""
    labels = f" {provider.labels_item}" if provider.labels_item else ""
    return (
        f"({provider.id_item} {provider.thread_item} RFC822.SIZE INTERNALDATE"
        f"{labels} BODYSTRUCTURE BODY.PEEK[{_HEADER_ITEM}])"
    )


def _fetch_rows_batch(
    conn: imaplib.IMAP4_SSL, uids: list[bytes], provider: Provider = GMAIL
) -> tuple[list[dict[str, object]], int]:
    """Batched header-only FETCH. Returns ``(rows, parse_skips)``.

    A message's response may span several imaplib items when the reply
    carries more than one literal (rare: a label sent as a literal). The
    accumulator concatenates metadata segments and keeps the literal whose
    preceding segment carries the ``BODY[HEADER.FIELDS (...)]`` marker as the
    header block; a record closes on the standalone ``)`` item. The marker is
    matched anywhere in that segment: the segment also carries the literal's
    ``{size}`` after it, so a fixed window from its end misses a long marker.
    """
    if not uids:
        return [], 0
    typ, data = conn.uid(
        "FETCH", b",".join(uids).decode("ascii"), _fetch_items(provider)
    )
    if typ != "OK":
        raise imaplib.IMAP4.error(f"UID FETCH headers answered {typ}")
    rows: list[dict[str, object]] = []
    skips = 0
    meta = bytearray()
    header_block = b""
    for item in data:
        if isinstance(item, tuple):
            if _HEADER_LITERAL_MARKER in item[0]:
                meta += item[0]
                header_block = item[1]
            else:  # another literal (a file name, a label): inline it, quoted
                meta += _LITERAL_SIZE_RE.sub(b"", item[0]) + _quoted(item[1])
        elif isinstance(item, bytes):
            meta += item
            if item.endswith(b")"):
                row = _finalize_record(bytes(meta), header_block, provider)
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


def _sweep_year(
    *,
    credentials_path: Path,
    mailbox: str | None,
    year: int,
    provider: Provider = GMAIL,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Sweep one year on its own IMAP connection; return its rows and stats.

    Keeps only rows whose INTERNALDATE-UTC year matches `year` — the
    widened search window (see `_search_year_uids`) means boundary
    messages surface in two adjacent workers, and this filter is what
    guarantees each lands in exactly one worker. Writes nothing: the main
    thread keys and merges every year's rows (`_merge_observed`). A provider with no
    all-mail folder is swept folder by folder (`auth.folders`), each selected
    read-only; a message in two folders is one row whose labels are both folders, and
    the year is complete only when every folder was swept.

    Opening the connection is the worker's stop check: `open_mailbox` refuses with
    `Halted` once any HALT is in force. A command that is rejected, times out or
    aborts writes a HALT and raises `Halted`, so no later year opens.
    """
    conn = open_mailbox(
        credentials_path, interactive=False, provider=provider, mailbox=mailbox
    )
    rows_for_year: list[dict[str, object]] = []
    by_msgid: dict[str, dict[str, object]] = {}
    parse_skips = 0
    label_misses = 0
    boundary_drops = 0
    walk = provider.all_mail is None
    try:
        for folder in folders(conn, provider) if walk else [mailbox or ""]:
            if walk:
                select_folder(conn, folder)
            for chunk in _chunked(_search_year_uids(conn, year), BATCH_SIZE):
                rows, skips = _fetch_rows_batch(conn, chunk, provider)
                parse_skips += skips
                for row in rows:
                    if datetime.fromisoformat(str(row["date"])).year != year:
                        boundary_drops += 1
                        continue
                    if walk:  # its labels are the folders holding it
                        row["labels"] = [folder]
                    seen = by_msgid.get(str(row["msgid"]))
                    if seen is not None:  # one message in a second folder
                        seen["labels"] = sorted({*seen["labels"], folder})  # type: ignore[misc]
                        continue
                    if row["labels"] is None:
                        label_misses += 1
                    by_msgid[str(row["msgid"])] = row
                    rows_for_year.append(row)
    except IMAP_FAILURES as exc:
        raise throttle_halt(credentials_path, f"catalog {year}", exc) from exc
    finally:
        _logout_quietly(conn)
    return rows_for_year, {
        "messages": len(rows_for_year),
        "parse_skips": parse_skips,
        "label_misses": label_misses,
        "boundary_drops": boundary_drops,
    }


def _merge_observed(  # pylint: disable=too-many-arguments
    *,
    store_dir: Path,
    messages_dir: Path | None,
    observed: list[dict[str, object]],
    swept: set[int],
    dry_run: bool,
    provider: Provider = GMAIL,
) -> int:
    """Key every observed row against the whole store, then merge and write.

    Rows are keyed in the provider's order with ``mailbox_key``, so a message already
    recorded keeps its row and a second message sharing its Message-ID gets its
    own. Each row merges into the shard its key already lives in, else its own
    INTERNALDATE year. A swept year's shard merges as a complete catalog (absence
    proves deletion); a shard that only receives a row from another year's
    sweep merges incompletely, so nothing in it is dropped or tombstoned.
    Returns how many shards were merged.
    """
    prior: dict[int, Shard] = {
        int(p.stem): read_shard(p) for p in store_shards(store_dir)
    }
    rows: dict[str, MailRow] = {}
    year_of: dict[str, int] = {}
    for year in sorted(prior):  # a later shard wins an id seen twice
        for id_, row in prior[year].items():
            rows[id_] = row
            year_of[id_] = year

    files = message_files(messages_dir) if messages_dir is not None else {}

    def held(row: MailRow) -> bool:
        return bool(row.get("downloaded")) and eml_on_disk(row, files)

    requests = [
        KeyRequest(
            str(row["id"]),
            str(row["msgid"]),
            str(row["date"]),
            cast(datetime | None, row.pop("_sent", None)),
        )
        for row in observed
    ]
    keys = mailbox_keys(requests, provider=provider, rows=rows, held=held)
    by_year: dict[int, Shard] = {}
    for row in observed:
        key = row["id"] = keys[str(row["msgid"])]
        year = year_of.get(key, datetime.fromisoformat(str(row["date"])).year)
        by_year.setdefault(year, {})[key] = row

    touched = sorted(swept | set(by_year))
    for year in touched:
        observed_year = by_year.get(year, {})
        if dry_run:
            logger.info("[dry-run] %s: %s row(s)", year, len(observed_year))
            continue
        merged = merge_catalog(
            prior.get(year, {}), observed_year, complete=year in swept
        )
        write_shard(shard_path(store_dir, year), merged)
        logger.info(
            "[catalog] %s: %s observed, %s in shard",
            year,
            len(observed_year),
            len(merged),
        )
    return len(touched)


# --- Top-level worker ----------------------------------------------------


def sweep(  # pylint: disable=too-many-arguments
    *,
    store_dir: Path,
    credentials_path: Path,
    mailbox: str | None,
    years: list[int] | None,
    dry_run: bool,
    threads: int = DEFAULT_THREADS,
    messages_dir: Path | None = None,
    provider: Provider = GMAIL,
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
    ``messages_dir`` lets keying tell a held ingest row (its ``.eml`` on disk),
    which a mailbox copy links to; without it no row counts as held. ``mailbox`` None
    is the provider's all-mail folder.
    """
    if years:
        targets = sorted(set(years))
    else:
        conn = open_mailbox(
            credentials_path, interactive=False, provider=provider, mailbox=mailbox
        )
        try:
            first = first_year(conn, provider)
        except IMAP_FAILURES as exc:
            raise throttle_halt(credentials_path, "catalog first year", exc) from exc
        finally:
            _logout_quietly(conn)
        if first is None:
            logger.info("[discover] mailbox is empty")
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
    logger.info(
        "[plan] %s year(s) %s..%s, %s thread(s)",
        len(targets),
        targets[0],
        targets[-1],
        workers,
    )

    per_year: list[dict[str, int]] = []
    observed: list[dict[str, object]] = []
    swept: set[int] = set()
    failure: BaseException | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _sweep_year,
                credentials_path=credentials_path,
                mailbox=mailbox,
                year=year,
                provider=provider,
            ): year
            for year in targets
        }
        for future in as_completed(futures):
            try:
                rows, stats = future.result()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                failure = (
                    failure or exc
                )  # re-raised once the finished years are written
                continue
            observed.extend(rows)
            per_year.append(stats)
            swept.add(futures[future])
    shards = _merge_observed(
        store_dir=store_dir,
        messages_dir=messages_dir,
        observed=observed,
        swept=swept,
        dry_run=dry_run,
        provider=provider,
    )
    if failure is not None:
        raise failure

    return {
        "messages": sum(s["messages"] for s in per_year),
        "shards": shards,
        "removed": 0,
        "parse_skips": sum(s["parse_skips"] for s in per_year),
        "label_misses": sum(s["label_misses"] for s in per_year),
        "boundary_drops": sum(s["boundary_drops"] for s in per_year),
    }
