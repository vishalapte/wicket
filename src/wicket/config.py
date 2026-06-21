"""Shared configuration and secret-handling helpers.

Shared across the three verbs (`catalog`, `fetch`, `report`). The two that
touch IMAP (`catalog`, `fetch`) authenticate to Gmail with an app password,
not OAuth. The credential file is JSON of the form
``{"email": "...", "app_password": "..."}`` (mode 0600).
"""

from __future__ import annotations

import os
from pathlib import Path

# Secrets live OUTSIDE the repo (never committed, never on the command line).
DEFAULT_STATE_DIR = Path.home() / ".config" / "gmail"

# Single secret file for IMAP credentials (email + 16-char app password).
CREDENTIALS_FILENAME = "imap.json"

# Optional alias file consumed by `wicket.fetch`. Shape:
#   {"primary.com": ["alias1.com", "alias2.com"], ...}
# When present, the archive tool treats aliases as the same logical entity
# as the primary (search expands; the filing domain canonicalizes to primary).
ALIASES_FILENAME = "domain-aliases.json"

# The owner's Gmail address is intentionally not baked in. Tools that need
# an account (the filing-domain rule, census's inventory scoping) read this
# env var or take --account; missing-and-not-passed is a hard error.
ACCOUNT_ENV_VAR = "WICKET_ACCOUNT"

# One per-account mail root holding both the year-sharded manifest store and the
# .eml archive (ADR 0002): ~/mail/<account>/{manifest,archive}/. Provider-neutral
# (credentials live separately under ~/.config/<provider>/).
MAIL_ROOT = Path.home() / "mail"

# Default worker-thread count for tools that parallelize IMAP work
# (census year workers, gmail body workers). One worker = one IMAP
# connection (imaplib is not thread-safe across commands); Gmail caps
# simultaneous IMAP connections (~15), so stay well under.
DEFAULT_THREADS = 4

# Gmail IMAP endpoint. The mailbox brackets are mandatory — Gmail's IMAP
# folder list literally names this "[Gmail]/All Mail" (note the space).
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
ALL_MAIL_MAILBOX = "[Gmail]/All Mail"
IMAP_TIMEOUT_SECONDS = 60


def normalize_account(address: str) -> str:
    """Lowercase and strip Gmail `+tag` aliasing.

    Gmail treats `you+anything@gmail.com` as the same mailbox as
    `you@gmail.com`. Canonicalize so an alias still counts as ACCOUNT in
    the filing-domain rule and scopes to the same dest/manifest paths
    in every tool. The one home for this rule.
    """
    address = address.strip().lower()
    if "@" not in address:
        return address
    local, _, domain = address.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def resolve_account(account: str | None) -> str:
    """Resolve the account from an explicit arg, the env var, or discovery.

    The one home for account resolution, shared by every verb's api. Order:
    the explicit ``account`` argument, then ``$WICKET_ACCOUNT``, then the sole
    directory under ``~/mail``. The result is canonicalized with
    `normalize_account`. Raises ``ValueError`` with a face-neutral message when
    none of the three yields an account; the caller turns that into its own exit.
    """
    chosen = account or os.environ.get(ACCOUNT_ENV_VAR) or discover_account()
    if not chosen:
        raise ValueError(
            f"no account: pass account=..., set ${ACCOUNT_ENV_VAR}, or keep "
            "exactly one account directory under ~/mail."
        )
    return normalize_account(chosen)


def resolve_state_dir(
    account: str, override: str | os.PathLike[str] | None = None
) -> Path:
    """Return the per-account state dir, creating it owner-only (0700) if missing.

    Credentials live at ``<base>/<account>/imap.json`` (always per-account; no
    legacy flat fallback). ``base`` defaults to `DEFAULT_STATE_DIR`
    (``~/.config/gmail``); ``override`` replaces the base, and the account
    segment is still appended under it. The account is canonicalized so `+tag`
    aliases resolve to the same directory.
    """
    base = Path(override).expanduser() if override else DEFAULT_STATE_DIR
    path = base / normalize_account(account)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)  # mkdir mode is umask-masked; enforce explicitly.
    return path


def resolve_store_dir(account: str) -> Path:
    """Per-account manifest store dir (``~/mail/<account>/manifest``).

    The one home for this path, shared by `wicket.catalog` (writes shards) and
    `wicket.fetch` (reads + settles them, ADR 0002). The account is
    canonicalized so `+tag` aliases resolve to the same directory.
    """
    return MAIL_ROOT / normalize_account(account) / "manifest"


def resolve_archive_dir(account: str) -> Path:
    """Per-account .eml archive root (``~/mail/<account>/archive``).

    Holds the ``<domain>/YYYY-MM/<msgid>.eml`` tree `wicket.fetch`
    writes; sibling to the manifest store under one mail root.
    """
    return MAIL_ROOT / normalize_account(account) / "archive"


def discover_account(mail_root: Path | None = None) -> str | None:
    """The sole account directory under the mail root, or None.

    Returns None when the root is absent, empty, or holds more than one account
    (ambiguous, so the caller must be told to be explicit). Lets a single-account
    setup skip `--account` / `$WICKET_ACCOUNT`.
    """
    root = mail_root or MAIL_ROOT
    if not root.exists():
        return None
    names = [p.name for p in root.iterdir() if p.is_dir()]
    return names[0] if len(names) == 1 else None


def write_secret(path: Path, data: str) -> None:
    """Write owner-only (0600) text, replacing any existing file."""
    path.write_text(data, encoding="utf-8")
    os.chmod(path, 0o600)
