"""Provider-neutral message identity and the observation/settlement join.

One home (ADR 0002) for the things the unified manifest needs:

  * the **portable message key** — the normalized RFC ``Message-ID``, with a
    provider-tagged fallback — that replaces Gmail's ``X-GM-MSGID`` as the
    cross-store, cross-provider key;
  * the **field-ownership** split between *observation*, *settlement*, and
    *derived* fields;
  * the **send date** (``sent_date``), the parsed ``Date:`` header ingest
    stores and fetch matches against;
  * the **Message-ID reader** (``message_id_of``), the one way every verb reads
    the header the key is made from;
  * the **header thread** (``assign_threads``): which messages of a local export
    belong together, from ``References``/``In-Reply-To`` and Outlook's
    ``Thread-Index``;
  * the **mailbox key** (``mailbox_key``), which gives every message of a mailbox
    its own row when two share a Message-ID, and ``local_msgid``, the ``msgid`` ingest
    gives a row of a local export, by which an ingest row is recognized;
  * the **file name and month** (``message_stem``, ``message_date``): where a
    message's one file lives under ``messages/`` (ADR-MAIL, 2026-11-14), read from
    the message itself so every copy of it in one store lands on one path;
  * the **attachment names** (``attachment_names``) a row records.

Pure stdlib beside `wicket.provider`: this sits in the shared layer below the verbs
and is imported by ``root.lib.catalog``, ``root.lib.fetch`` and ``root.lib.ingest``.
"""

from __future__ import annotations

import base64
import binascii
import email.header
import email.message
import email.parser
import email.policy
import email.utils
import hashlib
import re
import urllib.parse
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import NamedTuple

from wicket.provider import Provider

MailRow = dict[str, object]


def normalize_message_id(raw: str | None) -> str | None:
    """Return the bare RFC ``Message-ID`` (``<...>`` stripped, lowercased).

    Returns None when the header is absent or carries no usable token, so the
    caller can fall back to a provider-tagged key. A ``Message-ID`` may arrive
    wrapped in comments or whitespace; ``parseaddr`` pulls the addr-spec out of
    the angle brackets, and a manual strip covers the rest.
    """
    if not raw:
        return None
    _, addr = email.utils.parseaddr(raw)
    candidate = (addr or raw).strip().strip("<>").strip().lower()
    return candidate or None


def message_id_of(header_bytes: bytes) -> str:
    """A message's ``Message-ID`` header exactly as written, unfolded; '' when absent.

    The one reader for the header the manifest key is made from, used by
    catalog, fetch and ingest alike so they can never key a message two ways.
    ``policy.default`` cannot serve: its msg-id parser cuts an id short at a
    ``:`` or ``,`` (``<urn:correios:msg:...@amazon.com>`` reads as ``<urn``, so
    every such message shared the key ``urn``) and raises on Exchange's
    bracketed ids. ``compat32`` returns the raw value; its bytes arrive as
    surrogate-escaped ASCII, so they are re-decoded as UTF-8 (a non-ASCII id
    reads as it did under ``policy.default``), an invalid byte becoming U+FFFD.
    ``header_bytes`` may be a header block or a whole message.
    """
    headers = email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(
        header_bytes
    )
    raw = next(
        (str(v) for k, v in headers.raw_items() if k.lower() == "message-id"), ""
    )
    unfolded = raw.replace("\r\n", "").replace("\n", "")
    return unfolded.encode("ascii", "surrogateescape").decode("utf-8", "replace")


def message_key(*, message_id: str | None, provider: str, native_id: str) -> str:
    """The provider-neutral key for one message.

    The normalized ``Message-ID`` when present (portable across providers),
    else ``"<provider>:<native_id>"`` so every message still has a stable,
    unique key even when the header is missing or malformed.
    """
    normalized = normalize_message_id(message_id)
    if normalized:
        return normalized
    return f"{provider}:{native_id}"


def sent_date(header: object) -> datetime | None:
    """A ``Date:`` header as an aware UTC datetime, or None when absent or unparseable.

    One home for the send date, because two verbs compare it: ingest stores it as
    a row's ``date``, and fetch matches it to link a message ingest already filed.
    A naive date is taken as UTC.
    """
    if not header:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(str(header))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (
        TypeError,
        ValueError,
        OverflowError,
    ):  # OverflowError: a year past 9999 in UTC
        return None


# --- the file: its stem and its month (ADR-MAIL, 2026-11-14) ---------------


def _header_block(raw: bytes) -> email.message.Message:
    """A message's headers, raw values (``compat32``), the body not parsed."""
    return email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(raw)


def _utc_stamp(when: datetime | None) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if when else ""


def message_stem(raw: bytes) -> str:
    """The 16-hex name of a message's one file: a hash of the message, not the provider.

    ``sha1(message-id + "\n" + date + "\n" + from)[:16]``: the normalized
    ``Message-ID`` (`normalize_message_id`), the ``Date:`` header in UTC
    (``YYYY-MM-DDTHH:MM:SSZ``) and the ``From:`` addr-spec in lower case. A header
    that is missing, or a ``Date:`` that cannot be read, contributes ``""``. Two
    copies of one message get one stem whichever provider or export they came from.
    ``raw`` may be a whole message or its header block.
    """
    headers = _header_block(raw)
    message_id = normalize_message_id(message_id_of(raw)) or ""
    date = _utc_stamp(sent_date(headers.get("Date")))
    _, sender = email.utils.parseaddr(str(headers.get("From", "") or ""))
    text = f"{message_id}\n{date}\n{sender.strip().lower()}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def received_date(raw: bytes) -> datetime | None:
    """The topmost ``Received:`` header's timestamp (RFC 5322 §3.6.7), UTC; None if none."""
    for value in _header_block(raw).get_all("Received", [])[:1]:
        _, _, stamp = str(value).rpartition(";")
        return sent_date(stamp.strip())
    return None


def message_date(raw: bytes, fallback: datetime) -> datetime:
    """``MESSAGE_DATE``: the month a message's file is filed under, as UTC.

    RFC 5322 is canon: the ``Date:`` header, the origination date. Only for a
    message whose ``Date:`` is missing or unreadable, the topmost ``Received:``,
    else ``fallback`` (the provider's INTERNALDATE, or a local export's file time).
    """
    when = sent_date(_header_block(raw).get("Date")) or received_date(raw) or fallback
    return when.astimezone(timezone.utc)


def address_list(values: Iterable[object]) -> list[str]:
    """The sorted, de-duplicated, lower-case addr-specs in header ``values``."""
    return sorted(
        {
            addr.strip().lower()
            for _, addr in email.utils.getaddresses([str(v) for v in values if v])
            if addr.strip()
        }
    )


def rfc2231_param(params: dict[str, str], base: str) -> str | None:
    """MIME parameter ``base`` from ``params`` (lower-case keys), RFC 2231 decoded.

    ``base``, else ``base*`` (``charset'lang'%XX``), else the ``base*0``, ``base*1``
    ... continuations joined. The decoder catalog applies to ``BODYSTRUCTURE``'s
    parameters; ingest's MIME walk gets the same from
    `email.message.Message.get_filename`, and a parity test holds them equal.
    """
    if base in params:
        return params[base]
    if f"{base}*" in params:
        return _decode_2231(params[f"{base}*"])
    parts = sorted(
        (int(m.group(1)), key.endswith("*"), value)
        for key, value in params.items()
        if (m := re.fullmatch(rf"{re.escape(base)}\*(\d+)\*?", key))
    )
    if not parts:
        return None
    joined = "".join(value for _, _, value in parts)
    return _decode_2231(joined) if parts[0][1] else joined


def _decode_2231(value: str) -> str:
    """An RFC 2231 extended value (``charset'lang'%XX...``) as text."""
    charset, _, text = value.split("'", 2) if value.count("'") >= 2 else ("", "", value)
    try:
        return urllib.parse.unquote_to_bytes(text).decode(charset or "utf-8", "replace")
    except LookupError:  # an unknown charset: the bytes as UTF-8
        return urllib.parse.unquote_to_bytes(text).decode("utf-8", "replace")


def attachment_name(value: object) -> str | None:
    """One attachment file name, normalized: RFC 2047 encoded-words decoded, trimmed.

    The one rule for what a name is, applied to ingest's MIME walk
    (`attachment_names`) and to catalog's ``BODYSTRUCTURE``, so the row reads the same
    whichever verb wrote it. None for an empty name.
    """
    text = str(value or "")
    if "=?" in text:
        try:
            text = str(email.header.make_header(email.header.decode_header(text)))
        except (ValueError, LookupError, UnicodeDecodeError):
            pass  # an encoded-word that does not decode is kept as written
    text = " ".join(text.split())
    return text or None


def attachment_names(msg: email.message.Message) -> list[str]:
    """The file name of every leaf part that carries one, in the message's order.

    A part's name is its ``Content-Disposition`` ``filename``, else its
    ``Content-Type`` ``name`` (`email.message.Message.get_filename`, which decodes
    RFC 2231), then `attachment_name`. A ``message/rfc822`` part is walked into, not
    named. Catalog applies the same rule to ``BODYSTRUCTURE``.
    """
    names: list[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        try:
            name = attachment_name(part.get_filename())
        except (TypeError, ValueError, LookupError):
            name = None
        if name is not None:
            names.append(name)
    return names


def local_msgid(key: str) -> str:
    """Compact 16-hex ``msgid`` for a portable key (Gmail X-GM-MSGID parity).

    The ``msgid`` ingest gives a row of a local export (its file is named by
    `message_stem`). Deterministic (same key -> same name,
    so re-runs are idempotent) and collision-safe at this scale; the full key
    stays the manifest ``id``. A row whose ``msgid`` is this name of its own
    ``id`` is an ingest row: no Gmail id has been recorded on it.
    """
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def mailbox_key(  # pylint: disable=too-many-arguments
    *,
    key: str,
    native_id: str,
    tag: str,
    sent: datetime | None,
    rows: dict[str, MailRow],
    held: Callable[[MailRow], bool],
) -> str:
    """The manifest key for one mailbox message, recorded in ``rows`` as it is decided.

    ``key`` is the message's ``message_key``, ``native_id`` the provider's id for it
    (Gmail's X-GM-MSGID), ``tag`` the provider's name; ``rows`` maps every row id in
    the store to its row and is updated in place, so messages keyed one after another
    in one run (in the provider's order) each see those before them. Two messages
    with one Message-ID (a sender reusing it, or two copies in the mailbox) get two
    rows: the first recorded keeps the bare key, every later one is keyed
    ``<key>|<tag>:<native id>``, so no run can move a recorded message to another
    row. The one exception links instead: a row ingest filed (no native id on it,
    its file on disk, the same send time) is this message's copy and keeps the
    bare key, with the native id recorded on it.
    """
    row = rows.get(key)
    if row is not None and row.get("msgid") == native_id:
        return key
    split = f"{key}|{tag}:{native_id}"
    if split in rows:
        return split
    if row is None:
        rows[key] = {"id": key, "msgid": native_id}
        return key
    if _is_ingest_copy(key, row, sent, held):
        rows[key] = {**row, "msgid": native_id}
        return key
    rows[split] = {"id": split, "msgid": native_id}
    return split


class KeyRequest(NamedTuple):
    """One mailbox message to key: its ``message_key``, native id, INTERNALDATE (ISO, UTC)
    and send date."""

    key: str
    native_id: str
    when: str
    sent: datetime | None


def mailbox_keys(
    requests: Iterable[KeyRequest],
    *,
    provider: Provider,
    rows: dict[str, MailRow],
    held: Callable[[MailRow], bool],
) -> dict[str, str]:
    """Each message's manifest key by native id, keyed with `mailbox_key` in the
    provider's order: the one keying loop catalog, fetch and ingest --staged share."""
    keys: dict[str, str] = {}
    for req in sorted(requests, key=lambda r: provider.order(r.native_id, r.when)):
        keys[req.native_id] = mailbox_key(
            key=req.key,
            native_id=req.native_id,
            tag=provider.name,
            sent=req.sent,
            rows=rows,
            held=held,
        )
    return keys


def _is_ingest_copy(
    key: str, row: MailRow, sent: datetime | None, held: Callable[[MailRow], bool]
) -> bool:
    """Whether ``row`` is a held ingest row whose stored send time is ``sent``.

    ingest stores the parsed ``Date:`` header as ``date``; a message with no
    parseable Date never links.
    """
    if sent is None or row.get("msgid") != local_msgid(key) or not held(row):
        return False
    try:
        return datetime.fromisoformat(str(row.get("date", ""))) == sent
    except ValueError:
        return False


# --- the header thread (a local export carries no provider thread id) ------


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


def referenced_ids(msg: email.message.Message) -> list[str]:
    """Normalized message-ids this message replies to (References + In-Reply-To)."""
    out: list[str] = []
    for header in ("References", "In-Reply-To"):
        raw = str(msg.get(header, "") or "")
        for tok in raw.replace("<", " <").split():
            normalized = normalize_message_id(tok)
            if normalized:
                out.append(normalized)
    return out


def conversation_key(msg: email.message.Message) -> str | None:
    """Outlook Thread-Index conversation id: hex of the shared leading 22 bytes."""
    raw = str(msg.get("Thread-Index", "") or "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw)
    except (ValueError, binascii.Error):
        return None
    return decoded[:22].hex() if len(decoded) >= 22 else None


def assign_threads(messages: list[dict[str, object]]) -> dict[str, str]:
    """Return {id -> thread_id} via References/In-Reply-To + Thread-Index.

    Connected components over ``Message-ID`` <-> ``References``/``In-Reply-To``,
    augmented by the Outlook ``Thread-Index`` conversation id, with no
    subject-matching. Each message is a dict carrying ``id``, ``date``, ``refs``
    (`referenced_ids`) and ``conv`` (`conversation_key`); ``thread_id`` is the
    earliest member's id in its connected component.
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
