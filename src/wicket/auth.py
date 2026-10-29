"""IMAP authentication for Gmail using app passwords.

No OAuth, no Google Cloud project. The user generates a 16-character
"App password" once at https://myaccount.google.com/apppasswords (requires
2-Step Verification on the account) and saves it to the state dir. From
then on every run logs in with that password — refresh tokens, browser
consent, and per-app verification don't enter the picture.

It is also the home of the **HALT**: a fact about the Gmail credential (Gmail is
throttling it, or has rejected it), so it lives beside the code that opens every
connection. ``open_mailbox`` is the one door to IMAP; it refuses past a HALT.
`write_halt` is the one way a HALT is made and `clear_halt` the one way one is removed;
each writes its line to the account's log, so every HALT set and cleared is recorded.
They, `read_halt` and `list_halts` are the only code that touches the halt dir.
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
    ALL_MAIL_MAILBOX,
    IMAP_HOST,
    IMAP_PORT,
    IMAP_TIMEOUT_SECONDS,
    append_log,
    normalize_account,
    resolve_halt_dir,
    resolve_halt_path,
    write_secret,
)

# The page an owner makes a new app password on; the HALT for a rejected one names it.
APP_PASSWORDS_URL = "https://myaccount.google.com/apppasswords"


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
    """Parse Gmail's INTERNALDATE (e.g. ``22-Mar-2024 14:53:21 +0000``)."""
    try:
        return datetime.strptime(value.decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


def first_year(conn: imaplib.IMAP4_SSL) -> int | None:
    """UTC year of the oldest message in the selected mailbox (None if empty).

    The INTERNALDATE of the lowest UID: one SEARCH and one FETCH. The one rule for
    where a whole-mailbox pass starts, shared by `catalog` and `fetch`. A rejected
    command raises ``imaplib.IMAP4.error``, so the caller maps it to a HALT.
    """
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
        "No Gmail credentials at "
        f"{path}.\n"
        "Generate an app password at https://myaccount.google.com/apppasswords\n"
        "(requires 2-Step Verification). Then enter it here; it will be saved "
        "owner-only (0600).\n\n"
    )
    email = input("Gmail address: ").strip()
    password = getpass.getpass("App password (16 chars, spaces ok): ").strip()
    password = password.replace(" ", "")  # Google shows it space-grouped
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


def open_mailbox(
    credentials_path: Path,
    *,
    interactive: bool,
    mailbox: str = ALL_MAIL_MAILBOX,
    readonly: bool = True,
) -> imaplib.IMAP4_SSL:
    """Return an IMAP connection with the given mailbox selected.

    Readonly by design: every tool here reads, never modifies. Pass
    ``readonly=False`` explicitly if a future tool ever needs to flag
    messages — and audit that choice carefully.

    The one door to IMAP, so the HALT is enforced here: raises `Halted` when the
    account's HALT is in force; a HALT never expires. A rejected app password writes a HALT naming
    the app-password page; any other rejected login or select, or a connect that times
    out, writes a HALT naming the command; each then raises `Halted`. A host that cannot
    be reached for another reason (no network) raises `AuthError`, no HALT.
    """
    _refuse_past_halt(credentials_path)
    email, password = load_credentials(credentials_path, interactive=interactive)
    try:
        conn = imaplib.IMAP4_SSL(
            IMAP_HOST,
            IMAP_PORT,
            ssl_context=ssl.create_default_context(),
            timeout=IMAP_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise throttle_halt(credentials_path, f"connect to {IMAP_HOST}", exc) from exc
    except OSError as exc:
        raise AuthError(f"could not reach {IMAP_HOST}: {exc}") from exc
    try:
        conn.login(email, password)
    except imaplib.IMAP4.error as exc:
        if "AUTHENTICATIONFAILED" in str(exc):
            raise write_halt(
                credentials_path,
                f"Gmail rejected the app password (AUTHENTICATIONFAILED): make a "
                f"new one at {APP_PASSWORDS_URL} and re-seed the credential file",
            ) from exc
        raise throttle_halt(credentials_path, "login", exc) from exc
    except OSError as exc:
        raise throttle_halt(credentials_path, "login", exc) from exc
    try:
        typ, _ = conn.select(f'"{mailbox}"', readonly=readonly)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"SELECT {mailbox!r} answered {typ}")
    except (imaplib.IMAP4.error, OSError) as exc:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
        raise throttle_halt(credentials_path, "select", exc) from exc
    return conn
