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
    its own row when two share a Message-ID, and ``local_msgid``, the name ingest
    gives a file, by which an ingest row is recognized.

Pure stdlib beside `wicket.provider`: this sits in the shared layer below the verbs
and is imported by ``root.lib.catalog``, ``root.lib.fetch`` and ``root.lib.ingest``.
"""

from __future__ import annotations

import base64
import binascii
import email.message
import email.parser
import email.policy
import email.utils
import hashlib
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import NamedTuple

from wicket.provider import Provider

# Field ownership (ADR 0002 §2). Module-level tuples, not enums.
IDENTITY_FIELDS = ("id", "msgid", "thread_id", "account")
OBSERVATION_FIELDS = ("date", "from", "to", "subject", "size", "labels", "deleted")
SETTLEMENT_FIELDS = ("downloaded", "path", "filed")
DERIVED_FIELDS = ("domain",)
# Set only by the owner's cleanup, never by a verb: ``excluded`` holds why a
# message on the mailbox is not kept locally (``"duplicate of <row id>"``).
OWNER_FIELDS = ("excluded",)

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
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_msgid(key: str) -> str:
    """Compact 16-hex archive filename for a portable key (Gmail X-GM-MSGID parity).

    The name ingest gives a file it files. Deterministic (same key -> same name,
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
