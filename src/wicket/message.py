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
  * the **Gmail key** (``gmail_key``), which gives every Gmail message its own
    row when two share a Message-ID, and ``local_msgid``, the name ingest gives
    a file, by which an ingest row is recognized.

Pure stdlib, no wicket imports: this sits in the shared layer below the verbs
and is imported by ``root.lib.catalog``, ``root.lib.fetch`` and ``root.lib.ingest``.
"""

from __future__ import annotations

import email.parser
import email.policy
import email.utils
import hashlib
from collections.abc import Callable
from datetime import datetime, timezone

# Field ownership (ADR 0002 §2). Module-level tuples, not enums.
IDENTITY_FIELDS = ("id", "msgid", "thread_id", "account")
OBSERVATION_FIELDS = ("date", "from", "to", "subject", "size", "labels", "deleted")
SETTLEMENT_FIELDS = ("downloaded", "path", "filed")
DERIVED_FIELDS = ("domain",)
# Set only by the owner's cleanup, never by a verb: ``excluded`` holds why a
# message on the mailbox is not kept locally (``"duplicate of <row id>"``).
OWNER_FIELDS = ("excluded",)

GmailRow = dict[str, object]


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


def gmail_key(
    *,
    key: str,
    gmail_id: str,
    sent: datetime | None,
    rows: dict[str, GmailRow],
    held: Callable[[GmailRow], bool],
) -> str:
    """The manifest key for one Gmail message, recorded in ``rows`` as it is decided.

    ``key`` is the message's ``message_key``; ``rows`` maps every row id in the
    store to its row and is updated in place, so messages keyed one after another
    in one run (in Gmail-id order) each see those before them. Two Gmail
    messages with one Message-ID (a sender reusing it, or two Gmail copies) get
    two rows: the first recorded keeps the bare key, every later one is keyed
    ``<key>|gmail:<gmail id>``, so no run can move a recorded message to another
    row. The one exception links instead: a row ingest filed (no Gmail id on it,
    its file on disk, the same send time) is this message's copy and keeps the
    bare key, with the Gmail id recorded on it.
    """
    row = rows.get(key)
    if row is not None and row.get("msgid") == gmail_id:
        return key
    split = f"{key}|gmail:{gmail_id}"
    if split in rows:
        return split
    if row is None:
        rows[key] = {"id": key, "msgid": gmail_id}
        return key
    if _is_ingest_copy(key, row, sent, held):
        rows[key] = {**row, "msgid": gmail_id}
        return key
    rows[split] = {"id": split, "msgid": gmail_id}
    return split


def _is_ingest_copy(
    key: str, row: GmailRow, sent: datetime | None, held: Callable[[GmailRow], bool]
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
