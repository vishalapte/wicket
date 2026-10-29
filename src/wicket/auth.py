"""IMAP authentication with app passwords, for each provider in `wicket.provider`.

No OAuth, no cloud project. The owner makes an "App password" once with the
provider (Gmail: https://myaccount.google.com/apppasswords, which needs 2-Step
Verification; Fastmail: Settings, Privacy & Security, App passwords) and saves it to
the state dir. From then on every run logs in with that password — refresh tokens,
browser consent, and per-app verification don't enter the picture.

It is also the home of the **HALT**: a fact about the credential (the provider is
throttling it, or has rejected it), so it lives beside the code that opens every
connection. The folder helpers (`folders`, `select_folder`, `first_year`) are here too:
every SELECT is read-only, and only this module issues one. ``open_mailbox`` is the one
door to IMAP; it refuses past a HALT. `write_halt` is the one way a HALT is made and
`clear_halt` the one way one is removed; each writes its line to the account's log, so
every HALT set and cleared is recorded. They, `read_halt` and `list_halts` are the only
code that touches the halt dir.
"""

from __future__ import annotations

import getpass
import imaplib
import json
import os
import re
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from wicket.env import (
    IMAP_TIMEOUT_SECONDS,
    append_log,
    normalize_account,
    resolve_halt_dir,
    resolve_halt_path,
    write_secret,
)
from wicket.provider import GMAIL, Provider, imap_quote

# What an IMAP command raises when it is rejected (``IMAP4.error``, raised by the
# workers for any answer that is not OK), aborted (``IMAP4.abort``, a subclass) or
# timed out (``TimeoutError``, an ``OSError``). Each is a throttle (`throttle_halt`).
IMAP_FAILURES = (imaplib.IMAP4.error, OSError)

_INTERNALDATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]+)"')

# Gmail answers `UID SEARCH ALL` (`first_year`) with a single line listing every
# UID — ~1.2MB at 155k messages — which trips imaplib's hard-coded 1MB line guard.
# imaplib exposes no public knob, so raising the module attribute is the sanctioned
# workaround; 10MB gives ~1.2M-message headroom. It lives here, beside the one door
# to IMAP, so every verb that reaches `first_year` has it, whichever it imports.
_NEW_MAXLINE = max(getattr(imaplib, "_MAXLINE", 0), 10_000_000)
setattr(imaplib, "_MAXLINE", _NEW_MAXLINE)


class AuthError(RuntimeError):
    """Raised when usable IMAP credentials cannot be obtained."""


class Halted(AuthError):
    """A HALT is in force for this account: no IMAP connection opens past it.

    ``path`` is the HALT file, whose name is the account (``python -m wicket.halt clear
    --mail-account <account>`` clears it, after fixing its cause), and ``line`` says
    why: the cause, in the run that set it, else when it was set. A face reports it as a
    refusal, exit 2.
    """

    def __init__(self, path: Path, line: str) -> None:
        super().__init__(f"{line} (HALT at {path})")
        self.path = path
        self.line = line


class HaltInfo(NamedTuple):
    """One HALT in force: its account (the file's name), when it was set, and its age."""

    account: str
    halted: datetime
    age_seconds: int


def halt_path(credentials_path: Path) -> Path:
    """The account's HALT file. The account is the credential file's directory name."""
    return resolve_halt_path(credentials_path.parent.name)


def current_halt(credentials_path: Path) -> Halted:
    """A `Halted` describing the account's HALT file, which is in force."""
    path = halt_path(credentials_path)
    info = read_halt(path.name)
    if info is None:
        raise FileNotFoundError(path)
    return Halted(path, f"halted since {info.halted:%Y-%m-%d %H:%M %Z}")


def _one_line(text: str) -> str:
    """``text`` as one line: a newline in a cause would split the log's line."""
    return " ".join(text.splitlines()).strip()


def write_halt(credentials_path: Path, cause: str) -> Halted:
    """Halt the account; the first HALT wins. Returns the HALT in force.

    The HALT is an empty file, made with ``O_EXCL`` like ``touch``: its name is
    the account, its timestamp is when it was set, and present means halted; only
    `clear_halt` removes it. A HALT made here appends ``HALT set <cause>`` to the
    account's log; one already in force is kept and logs nothing. ``cause`` carries no
    password and no path. A file that cannot be made raises.
    """
    account = normalize_account(credentials_path.parent.name)
    resolve_halt_dir(create=True)
    path = resolve_halt_path(account)
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return current_halt(credentials_path)
    append_log(account, f"HALT set {_one_line(cause)}")
    return Halted(path, _one_line(cause))


def throttle_halt(credentials_path: Path, what: str, exc: BaseException) -> Halted:
    """`write_halt` for an IMAP command that was rejected, timed out or aborted.

    The line names the command and the exception's type, never its text: a disk error's
    text names a path.
    """
    name = type(exc).__name__
    return write_halt(credentials_path, f"{what}: {name}")


def read_halt(account: str) -> HaltInfo | None:
    """The account's HALT, or None when it is not halted (no file). Reads the timestamp."""
    path = resolve_halt_path(account)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None
    halted = datetime.fromtimestamp(mtime, timezone.utc).astimezone()
    return HaltInfo(path.name, halted, max(int(time.time() - mtime), 0))


def list_halts() -> list[HaltInfo]:
    """Every HALT in force, by account: one per file in the halt dir."""
    root = resolve_halt_dir()
    if not root.is_dir():
        return []
    found = (read_halt(p.name) for p in sorted(root.iterdir()) if p.is_file())
    return [info for info in found if info is not None]


def clear_halt(account: str) -> HaltInfo | None:
    """Remove the account's HALT, logging it first. Returns what was cleared, else None.

    Appends ``HALT cleared`` to the account's log, then unlinks the file: a crash
    between the two leaves the account still halted, never a removal the log does not
    hold. Not halted: writes nothing and returns None. A file another process removed
    first counts as cleared.
    """
    info = read_halt(account)
    if info is None:
        return None
    append_log(info.account, "HALT cleared")
    try:
        resolve_halt_path(info.account).unlink()
    except FileNotFoundError:
        pass
    return info


def _refuse_past_halt(credentials_path: Path) -> None:
    """Raise `Halted` when the account's HALT file is there.

    A HALT never expires; only `clear_halt` removes it.
    """
    if halt_path(credentials_path).exists():
        raise current_halt(credentials_path)


def parse_internaldate(value: bytes) -> datetime | None:
    """Parse an INTERNALDATE (e.g. ``22-Mar-2024 14:53:21 +0000``)."""
    try:
        return datetime.strptime(value.decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


# One line of a LIST answer: ``(<flags>) <delimiter> <name>``.
_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\) (?:"(?:[^"\\]|\\.)*"|NIL) (?P<name>.*)$')
_UNSELECTABLE = frozenset({"\\noselect", "\\nonexistent"})


def _list_name(raw: bytes) -> str:
    """A LIST name as SELECT takes it: a quoted string unquoted, an atom as it is."""
    text = raw.decode("ascii", "replace")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return re.sub(r"\\(.)", r"\1", text[1:-1])
    return text


def folders(conn: imaplib.IMAP4_SSL, provider: Provider = GMAIL) -> list[str]:
    """The folders a whole-mailbox pass walks, in the server's LIST order.

    A provider with an all-mail folder walks only that one. Otherwise every folder
    LIST names but one that cannot be selected (``\\Noselect``, ``\\NonExistent``) or
    carries one of the provider's ``skip_flags``. A rejected LIST raises
    ``imaplib.IMAP4.error``, so the caller maps it to a HALT.
    """
    if provider.all_mail is not None:
        return [provider.all_mail]
    typ, data = conn.list()
    if typ != "OK":
        raise imaplib.IMAP4.error(f"LIST answered {typ}")
    skip = _UNSELECTABLE | {flag.lower() for flag in provider.skip_flags}
    found: list[str] = []
    for item in data:
        if item is None:
            continue
        line, literal = (item, None) if isinstance(item, bytes) else item
        match = _LIST_RE.match(line)
        if match is None:
            continue
        flags = {flag.lower() for flag in match.group("flags").decode().split()}
        if flags & skip:
            continue
        found.append(
            literal.decode("ascii") if literal else _list_name(match.group("name"))
        )
    return found


def select_folder(conn: imaplib.IMAP4_SSL, name: str) -> None:
    """SELECT ``name`` read-only (EXAMINE): the one SELECT after `open_mailbox`'s own.

    Read-only always, and no argument can change that. A folder the server refuses
    raises ``imaplib.IMAP4.error``, so the caller maps it to a HALT.
    """
    typ, _ = conn.select(imap_quote(name), readonly=True)
    if typ != "OK":
        raise imaplib.IMAP4.error(f"SELECT {name!r} answered {typ}")


def first_year(conn: imaplib.IMAP4_SSL, provider: Provider = GMAIL) -> int | None:
    """UTC year of the oldest message the provider's whole-mailbox pass walks (None if empty).

    With an all-mail folder (selected by `open_mailbox`), the INTERNALDATE of its
    lowest UID: one SEARCH and one FETCH. Otherwise the lowest of that over every
    folder `folders` walks, each selected read-only in turn. The one rule for where a
    whole-mailbox pass starts, shared by `catalog` and `fetch`. A rejected command
    raises ``imaplib.IMAP4.error``, so the caller maps it to a HALT.
    """
    if provider.all_mail is not None:
        return _first_year_here(conn)
    years = []
    for name in folders(conn, provider):
        select_folder(conn, name)
        years.append(_first_year_here(conn))
    return min((y for y in years if y is not None), default=None)


def _first_year_here(conn: imaplib.IMAP4_SSL) -> int | None:
    """The UTC year of the lowest UID's INTERNALDATE in the selected folder."""
    typ, data = conn.uid("SEARCH", "ALL")
    if typ != "OK":
        raise imaplib.IMAP4.error(f"UID SEARCH ALL answered {typ}")
    if not data or not data[0]:
        return None
    lowest = data[0].split()[0]
    typ, data = conn.uid("FETCH", lowest.decode("ascii"), "(INTERNALDATE)")
    if typ != "OK":
        raise imaplib.IMAP4.error(f"UID FETCH INTERNALDATE answered {typ}")
    for item in data:
        chunk = item if isinstance(item, bytes) else item[0]
        date_match = _INTERNALDATE_RE.search(chunk)
        if date_match:
            parsed = parse_internaldate(date_match.group(1))
            if parsed is not None:
                return parsed.astimezone(timezone.utc).year
    return None


def _load_secret_file(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    email = (data.get("email") or "").strip()
    password = data.get("app_password") or ""
    if not email or not password:
        raise AuthError(
            f"{path}: missing 'email' or 'app_password'. Expected a JSON file "
            'of the form: {"email": "you@gmail.com", "app_password": "..."}.'
        )
    return email, password


def _prompt_for_secret(path: Path) -> tuple[str, str]:
    sys.stderr.write(
        "No credentials at "
        f"{path}.\n"
        "Make an app password with the provider (Gmail: "
        "https://myaccount.google.com/apppasswords, which needs 2-Step Verification;\n"
        "Fastmail: Settings, Privacy & Security, App passwords). Then enter it here; "
        "it will be saved owner-only (0600).\n\n"
    )
    email = input("Login address: ").strip()
    password = getpass.getpass("App password (spaces ok): ").strip()
    password = password.replace(" ", "")  # providers show it space-grouped
    if "@" not in email or len(password) < 8:
        raise AuthError("aborted: email or password looked invalid.")
    write_secret(path, json.dumps({"email": email, "app_password": password}))
    return email, password


def load_credentials(
    credentials_path: Path,
    *,
    interactive: bool,
) -> tuple[str, str]:
    """Return (email, app_password). Prompt to seed the file on first run.

    Pass ``interactive=False`` from cron/launchd: missing credentials raise
    `AuthError` instead of blocking on a prompt that would never be answered.
    """
    if credentials_path.exists():
        return _load_secret_file(credentials_path)
    if not interactive:
        raise AuthError(
            f"No credentials at {credentials_path} and running non-interactively. "
            "Run once in a terminal (without --non-interactive) to seed it."
        )
    return _prompt_for_secret(credentials_path)


def _require_capability(
    conn: imaplib.IMAP4_SSL, provider: Provider, credentials_path: Path
) -> None:
    """Refuse a server that lacks the provider's ``required_capability``, with no HALT.

    Fastmail's ids are RFC 8474's (OBJECTID); a server without it would reject every
    ``FETCH EMAILID``, and every rejected command HALTs, so its absence is said once,
    as a refusal (`AuthError`), and nothing runs. A rejected CAPABILITY is a throttle.
    """
    needed = provider.required_capability
    if needed is None:
        return
    try:
        typ, data = conn.capability()
        if typ != "OK":
            raise imaplib.IMAP4.error(f"CAPABILITY answered {typ}")
    except (imaplib.IMAP4.error, OSError) as exc:
        _logout(conn)
        raise throttle_halt(credentials_path, "capability", exc) from exc
    announced = b" ".join(d for d in data if isinstance(d, bytes)).upper().split()
    if needed.encode("ascii") not in announced:
        _logout(conn)
        raise AuthError(
            f"{provider.host} does not announce {needed}: wicket cannot key its "
            f"{provider.label} messages, so nothing ran"
        )


def _logout(conn: imaplib.IMAP4_SSL) -> None:
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def open_mailbox(
    credentials_path: Path,
    *,
    interactive: bool,
    provider: Provider = GMAIL,
    mailbox: str | None = None,
    readonly: bool = True,
) -> imaplib.IMAP4_SSL:
    """Return an IMAP connection to ``provider``'s host with ``mailbox`` selected.

    ``mailbox`` defaults to the provider's all-mail folder, else ``INBOX``; a walk
    of every folder selects each in turn with `select_folder`. A server without the
    provider's ``required_capability`` is refused (`AuthError`), no HALT.

    Readonly by design: every tool here reads, never modifies. Pass
    ``readonly=False`` explicitly if a future tool ever needs to flag
    messages — and audit that choice carefully.

    The one door to IMAP, so the HALT is enforced here: raises `Halted` when the
    account's HALT is in force; a HALT never expires. A rejected app password writes a
    HALT saying where the provider makes a new one; any other rejected login or select,
    or a connect that times out, writes a HALT naming the command; each then raises
    `Halted`. A host that cannot be reached for another reason (no network) raises
    `AuthError`, no HALT.
    """
    _refuse_past_halt(credentials_path)
    email, password = load_credentials(credentials_path, interactive=interactive)
    host = provider.host
    try:
        conn = imaplib.IMAP4_SSL(
            host,
            provider.port,
            ssl_context=ssl.create_default_context(),
            timeout=IMAP_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise throttle_halt(credentials_path, f"connect to {host}", exc) from exc
    except OSError as exc:
        raise AuthError(f"could not reach {host}: {exc}") from exc
    try:
        conn.login(email, password)
    except imaplib.IMAP4.error as exc:
        if "AUTHENTICATIONFAILED" in str(exc):
            raise write_halt(
                credentials_path,
                f"{provider.label} rejected the app password (AUTHENTICATIONFAILED): "
                f"{provider.app_password_hint} and re-seed the credential file",
            ) from exc
        raise throttle_halt(credentials_path, "login", exc) from exc
    except OSError as exc:
        raise throttle_halt(credentials_path, "login", exc) from exc
    _require_capability(conn, provider, credentials_path)
    mailbox = mailbox or provider.all_mail or "INBOX"
    try:
        typ, _ = conn.select(f'"{mailbox}"', readonly=readonly)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"SELECT {mailbox!r} answered {typ}")
    except (imaplib.IMAP4.error, OSError) as exc:
        _logout(conn)
        raise throttle_halt(credentials_path, "select", exc) from exc
    return conn
