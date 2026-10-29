"""The mail providers wicket reads: each one's server and IMAP dialect.

The one home for everything that differs between providers: the host, the folder
(or folders) a whole-mailbox pass walks, the FETCH items that carry a message's
native id, thread and labels, the search a year or ``--last`` becomes, how a native
id orders and names a file, the name ``fetch`` stages a body under, and the words a
HALT uses. A verb asks its provider; no verb tests a provider's name. Which provider
an account uses is where its ``imap.json`` links to (`provider_of`).

Pure stdlib, no wicket imports: the bottom layer, beside `wicket.env`. It issues no
IMAP command; `wicket.auth` is the one door to IMAP and takes a provider.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple

# One IMAP search, as the arguments after ``UID SEARCH``.
Search = tuple[str, ...]


# ``--last``: N, then d (days), m (months) or y (years).
LAST_RE = re.compile(r"^([1-9][0-9]*)([dmy])$")

# A staged body's time: its INTERNALDATE in UTC.
_STAGED_DATE = "%Y%m%dT%H%M%SZ"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun") + (
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def imap_quote(value: str) -> str:
    """``value`` as an IMAP quoted string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def imap_date(day: date) -> str:
    """``day`` as an IMAP search date (``1-Mar-2026``, English month names)."""
    return f"{day.day}-{_MONTHS[day.month - 1]}-{day.year}"


def months_back(day: date, months: int) -> date:
    """``day`` moved back ``months`` calendar months, its day clamped to that month's last."""
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    for last in range(day.day, 27, -1):
        try:
            return date(year, month + 1, last)
        except ValueError:
            continue
    return date(year, month + 1, day.day)


class Staged(NamedTuple):
    """A staged body's name read back: its provider, thread, native id and INTERNALDATE."""

    provider: "Provider"
    thread_id: str
    msgid: str
    internal_dt: datetime


class Provider:
    """A mail provider. The base class is the interface; each provider fills it.

    ``name`` tags a key that has no Message-ID (``<name>:<id>``) and a second message
    sharing one (``<key>|<name>:<id>``). ``all_mail`` is the one folder holding every
    message, or None when a whole-mailbox pass walks every selectable folder but
    those flagged ``skip_flags`` (RFC 6154 special-use flags). ``labels_item`` None
    means a message's labels are the folders holding it. ``required_capability`` is
    one the server must announce after login, or nothing it says can be trusted.
    """

    name = ""
    label = ""
    host = ""
    port = 993
    all_mail: str | None = None
    skip_flags: frozenset[str] = frozenset()
    id_item = ""
    thread_item = ""
    labels_item: str | None = None
    required_capability: str | None = None
    app_password_hint = ""
    _id_re = re.compile(rb"$^")
    _thread_re = re.compile(rb"$^")
    _staged_re = re.compile(r"$^")

    def parse_id(self, meta: bytes) -> str | None:
        """The native id in a FETCH response, as the manifest's ``msgid`` stores it."""
        match = self._id_re.search(meta)
        return self._native(match.group(1)) if match else None

    def parse_thread(self, meta: bytes) -> str | None:
        """The thread id in a FETCH response, as the manifest's ``thread_id`` stores it."""
        match = self._thread_re.search(meta)
        return match.group(1).decode("ascii") if match else None

    def _native(self, raw: bytes) -> str:
        return raw.decode("ascii")

    def order(self, msgid: str, when: str) -> tuple[object, ...]:
        """The order messages are keyed in, so the first recorded keeps the bare key.

        ``when`` is the message's INTERNALDATE as the row stores it (ISO, UTC).
        """
        raise NotImplementedError

    def file_stem(self, msgid: str) -> str:
        """The ``.eml`` file name (no suffix) a message of this provider is filed under."""
        raise NotImplementedError

    def raw_search(self, query: str) -> Search:
        """A provider-native search expression (``--query``, ``--domains``).

        A provider with no such dialect raises ``ValueError``: the run is refused.
        """
        raise ValueError(f"{self.label} takes no --query or --domains")

    def year_search(self, year: int) -> Search:
        """Every message of ``year``."""
        raise NotImplementedError

    def since_search(self, last: str, today: date) -> Search:
        """Every message of the last ``last`` (``2d``, ``3m``, ``1y``) up to ``today``."""
        raise NotImplementedError

    def exclude_list(self, list_id: str) -> Search:
        """The search keys that leave out mail sent via mailing list ``list_id``.

        Appended to a search (IMAP ANDs its keys), so an excluded list's mail is never
        listed, keyed or downloaded (`wicket.exclusions`).
        """
        raise NotImplementedError

    def staged_name(self, thread_id: str, msgid: str, internal_dt: datetime) -> str:
        """The path, under ``staging/``, ``fetch`` gets one message of this provider to."""
        stamp = internal_dt.astimezone(timezone.utc).strftime(_STAGED_DATE)
        thread, name = self._stage(thread_id, msgid)
        return f"{thread}/{name}@{stamp}.eml"

    def _stage(self, thread_id: str, msgid: str) -> tuple[str, str]:
        return thread_id, msgid

    def _unstage(self, thread: str, name: str) -> tuple[str, str] | None:
        return thread, name

    def parse_staged(self, rel: str) -> Staged | None:
        """``rel`` read back as this provider's staged name, or None if it is not one."""
        match = self._staged_re.match(rel)
        if match is None:
            return None
        ids = self._unstage(match.group(1), match.group(2))
        try:
            when = datetime.strptime(match.group(3), _STAGED_DATE)
        except ValueError:
            return None
        if ids is None:
            return None
        return Staged(self, ids[0], ids[1], when.replace(tzinfo=timezone.utc))


class Gmail(Provider):
    """Gmail: one All Mail folder, ``X-GM-*`` items, Gmail search through ``X-GM-RAW``.

    X-GM-MSGID arrives in decimal and is stored as hex, ordered by its value (Gmail's
    arrival order), and names the file as it is.
    """

    name = "gmail"
    label = "Gmail"
    host = "imap.gmail.com"
    # The brackets are mandatory: Gmail's folder list literally names this
    # "[Gmail]/All Mail" (note the space).
    all_mail = "[Gmail]/All Mail"
    id_item = "X-GM-MSGID"
    thread_item = "X-GM-THRID"
    labels_item = "X-GM-LABELS"
    app_password_hint = "make a new one at https://myaccount.google.com/apppasswords"
    _id_re = re.compile(rb"X-GM-MSGID\s+(\d+)")
    _thread_re = re.compile(rb"X-GM-THRID\s+(\d+)")
    # The thread id, the msgid (hex) and the INTERNALDATE in UTC; nothing else is
    # filed, since the msgid becomes an archive file name.
    _staged_re = re.compile(r"^(\d+)/([0-9a-f]+)@(\d{8}T\d{6}Z)\.eml$")

    def _native(self, raw: bytes) -> str:
        return f"{int(raw):x}"

    def order(self, msgid: str, when: str) -> tuple[object, ...]:
        return (int(msgid, 16),)

    def file_stem(self, msgid: str) -> str:
        return msgid

    def raw_search(self, query: str) -> Search:
        return ("CHARSET", "UTF-8", "X-GM-RAW", imap_quote(query))

    def year_search(self, year: int) -> Search:
        return self.raw_search(f"after:{year}/01/01 before:{year + 1}/01/01")

    def since_search(self, last: str, today: date) -> Search:
        return self.raw_search(f"newer_than:{last}")

    def exclude_list(self, list_id: str) -> Search:
        return ("NOT", "X-GM-RAW", imap_quote(f"list:{list_id}"))


# RFC 8474's objectid: 1*255(ALPHA / DIGIT / "_" / "-").
_OBJECTID_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")


class Fastmail(Provider):
    """Fastmail: a folder per mailbox, RFC 8474 ``EMAILID`` and ``THREADID``, IMAP SEARCH.

    No folder holds every message, so a whole-mailbox pass walks every selectable
    folder but Trash, Spam and Fastmail's four non-mail folders (scheduled mail,
    memos, notes, templates) (owner, 2026-10-28), one message per EMAILID, its labels
    the folders holding it. An EMAILID is mixed case (``Str0hkL1JFLB``), and the disk
    may fold case, so a staged name carries the ids hex-encoded under ``fastmail/``
    and an archived file is named by a lowercase digest of the id (owner, 2026-10-28);
    the row keeps the EMAILID itself as ``msgid``. Messages are keyed in INTERNALDATE
    order, then EMAILID: an EMAILID says nothing about arrival.
    """

    name = "fastmail"
    label = "Fastmail"
    host = "imap.fastmail.com"
    skip_flags = frozenset(
        {"\\Trash", "\\Junk", "\\Scheduled", "\\Memos", "\\XNotes", "\\XTemplates"}
    )
    id_item = "EMAILID"
    thread_item = "THREADID"
    required_capability = "OBJECTID"
    app_password_hint = (
        "make a new one in Fastmail under Settings, Privacy & Security, App passwords"
    )
    _id_re = re.compile(rb"\bEMAILID\s+\(([A-Za-z0-9_-]{1,255})\)")
    _thread_re = re.compile(rb"\bTHREADID\s+\(([A-Za-z0-9_-]{1,255})\)")
    _staged_re = re.compile(r"^fastmail/([0-9a-f]+)/([0-9a-f]+)@(\d{8}T\d{6}Z)\.eml$")

    def order(self, msgid: str, when: str) -> tuple[object, ...]:
        return (when, msgid)

    def file_stem(self, msgid: str) -> str:
        # ingest's digest of a local export's key (message.local_msgid), over the
        # provider-tagged id, so it cannot meet a Message-ID's digest.
        return hashlib.sha1(f"{self.name}:{msgid}".encode("ascii")).hexdigest()[:16]

    def year_search(self, year: int) -> Search:
        return ("SINCE", f"1-Jan-{year}", "BEFORE", f"1-Jan-{year + 1}")

    def since_search(self, last: str, today: date) -> Search:
        match = LAST_RE.match(last)
        if match is None:
            raise ValueError(
                f"--last takes N then d, m or y (2d, 3m, 1y), not {last!r}"
            )
        n, unit = int(match.group(1)), match.group(2)
        if unit == "d":
            since = date.fromordinal(today.toordinal() - n)
        else:
            since = months_back(today, n if unit == "m" else 12 * n)
        return ("SINCE", imap_date(since))

    def exclude_list(self, list_id: str) -> Search:
        # The header carries the id in angle brackets; quoting them pins both ends, so a
        # part of one list's name never matches another (probed 2026-10-29).
        return ("NOT", "HEADER", "List-Id", imap_quote(f"<{list_id}>"))

    def _stage(self, thread_id: str, msgid: str) -> tuple[str, str]:
        return (
            f"fastmail/{thread_id.encode('ascii').hex()}",
            msgid.encode("ascii").hex(),
        )

    def _unstage(self, thread: str, name: str) -> tuple[str, str] | None:
        try:
            ids = bytes.fromhex(thread).decode("ascii"), bytes.fromhex(name).decode(
                "ascii"
            )
        except ValueError:
            return None
        if not all(_OBJECTID_RE.match(i) for i in ids):
            return None
        return ids


GMAIL = Gmail()
FASTMAIL = Fastmail()
PROVIDERS = (GMAIL, FASTMAIL)


def parse_staged(rel: str) -> Staged | None:
    """A path under ``staging/`` read back by whichever provider named it, else None."""
    for provider in PROVIDERS:
        staged = provider.parse_staged(rel)
        if staged is not None:
            return staged
    return None


def provider_of(credentials_path: Path) -> Provider:
    """The provider an account's ``imap.json`` declares by where it links to.

    A link into ``~/.config/gmail/<login>/`` is Gmail, into ``~/.config/fastmail/<login>/``
    Fastmail (owner, 2026-10-28): the provider is the link target's grandparent dir.
    Anything else, a real file, a missing file or a link elsewhere, raises
    ``ValueError``: no provider is assumed. A link whose target does not exist yet
    still names its provider, so a first seed writes through it.
    """
    if not credentials_path.is_symlink():
        raise ValueError(
            f"{credentials_path} is not a link: link it to "
            "~/.config/<gmail|fastmail>/<login>/imap.json, which names its provider"
        )
    target = Path(os.readlink(credentials_path))
    if not target.is_absolute():
        target = credentials_path.parent / target
    for provider in PROVIDERS:
        if target.parent.parent.name == provider.name:
            return provider
    raise ValueError(
        f"{credentials_path} links to {target}, under neither ~/.config/gmail/ nor "
        "~/.config/fastmail/: no provider is assumed"
    )
