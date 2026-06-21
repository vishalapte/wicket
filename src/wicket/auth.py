"""IMAP authentication for Gmail using app passwords.

No OAuth, no Google Cloud project. The user generates a 16-character
"App password" once at https://myaccount.google.com/apppasswords (requires
2-Step Verification on the account) and saves it to the state dir. From
then on every run logs in with that password — refresh tokens, browser
consent, and per-app verification don't enter the picture.
"""

from __future__ import annotations

import getpass
import imaplib
import json
import ssl
import sys
from pathlib import Path

from wicket.config import (
    ALL_MAIL_MAILBOX,
    IMAP_HOST,
    IMAP_PORT,
    IMAP_TIMEOUT_SECONDS,
    write_secret,
)


class AuthError(RuntimeError):
    """Raised when usable IMAP credentials cannot be obtained."""


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
    """
    email, password = load_credentials(credentials_path, interactive=interactive)
    try:
        conn = imaplib.IMAP4_SSL(
            IMAP_HOST,
            IMAP_PORT,
            ssl_context=ssl.create_default_context(),
            timeout=IMAP_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        raise AuthError(f"could not reach {IMAP_HOST}: {exc}") from exc
    try:
        conn.login(email, password)
    except imaplib.IMAP4.error as exc:
        raise AuthError(
            f"IMAP login rejected for {email}: {exc}. "
            "If you recently changed your Google password or revoked the app "
            "password, regenerate one at https://myaccount.google.com/apppasswords "
            f"and update {credentials_path}."
        ) from exc
    typ, _ = conn.select(f'"{mailbox}"', readonly=readonly)
    if typ != "OK":
        conn.logout()
        raise AuthError(f"could not select mailbox {mailbox!r}")
    return conn
