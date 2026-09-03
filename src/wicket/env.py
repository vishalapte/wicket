"""Shared configuration and secret-handling helpers.

Shared across the three verbs (`catalog`, `fetch`, `report`). The two that
touch IMAP (`catalog`, `fetch`) authenticate to Gmail with an app password,
not OAuth. The credential file is JSON of the form
``{"email": "...", "app_password": "..."}`` (mode `0600`).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Secrets live OUTSIDE the repo (never committed, never on the command line).
DEFAULT_STATE_DIR = Path.home() / ".config" / "gmail"

# Single secret file for IMAP credentials (email + 16-char app password).
CREDENTIALS_FILENAME = "imap.json"

# Owner-authored map of the addresses you receive at to the destination each one
# belongs to. It lives at the MAIL_ROOT level, not under the per-account state
# dir: the map is consulted to *decide* the account, so it cannot itself be
# account-scoped. An alias never creates a store; it resolves to a primary that
# must already have one, so a typo cannot mint a directory.
#
# **The file records only the exceptions.** An address that already names a store
# (you@work.com -> the you@work.com store) maps to itself implicitly
# and must not be listed. What earns an entry is mail that arrives somewhere its
# own address does not explain: a burner you handed to one vendor, a catch-all
# subdomain, a topical bucket that is not a mailbox at all.
#
#   {"travel":   ["hyatt@xcv.org", "ba@xcv.org", "delta@xcv.org"],
#    "shopping": ["*@shopping.example.com"]}
#
# A primary is therefore either an account address OR a bucket name, and a bucket
# is a destination only once you `mkdir` its store: wicket never creates one.
ACCOUNT_ALIASES_FILENAME = "account-aliases.json"

# Overrides ACCOUNT_ALIASES_FILENAME's location entirely (a full path, not just a
# name) — the same escape hatch $WICKET_MAIL_ROOT gives the mail tree itself, for
# an owner who wants this one map to live somewhere else (a second vault, a
# dotfiles repo). Checked by `account_aliases_path`; unset means the default
# `<mail-root>/account-aliases.json`.
ACCOUNT_ALIASES_FILE_ENV_VAR = "WICKET_ACCOUNT_ALIASES_FILE"

# The counterparty map, and the mirror image of the one above: it lists domains
# that are NOT you, and the store their mail belongs in ("ikea.com" -> shopping).
# Loaded by `wicket.domains` (the domain rules live there); named here so both
# owner-authored files have one home. Keeping the two apart is load bearing: a
# counterparty listed as an alias would make the filing rule think it is *you*.
DOMAIN_ROUTES_FILENAME = "domain-routes.json"

# Path overrides for the two domain maps, same shape as ACCOUNT_ALIASES_FILE_ENV_VAR
# above. Named here (not in `wicket.domains`) so all three owner-authored maps'
# filenames AND overrides share one home; `wicket.domains`'s path functions read them.
DOMAIN_ROUTES_FILE_ENV_VAR = "WICKET_DOMAIN_ROUTES_FILE"
DOMAIN_ALIASES_FILE_ENV_VAR = "WICKET_DOMAIN_ALIASES_FILE"

# An email address. Deliberately permissive in the local part (burner services
# mint odd ones) and strict in the domain, which is the half that can reach the
# filesystem through the filing rule.
ADDRESS_RE = re.compile(r"^[^@\s]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# A catch-all alias: every address at a domain is yours ("*@shopping.you.com").
# Only the local part may be wildcarded; a bare "*@*" would swallow the world.
WILDCARD_ADDRESS_RE = re.compile(r"^\*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# A bucket: a destination that is not a mailbox ("travel", "shopping"). It is a
# path segment, so it is kept to a conservative slug — no dots, no separators,
# nothing that could climb out of MAIL_ROOT.
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

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
# .eml archive (ADR 0002): ~/.delphi/mail/<account>/{manifest,archive}/.
# Provider-neutral (credentials live separately under ~/.config/<provider>/).
#
# The PHYSICAL location is configuration, not code: $WICKET_MAIL_ROOT overrides
# it, so the tree can follow an encrypted vault to whatever path it mounts at.
# wicket is standalone and reads its OWN env var; it never imports delphi or
# reads $DELPHI_PRIVATE, even though the default path sits under ~/.delphi.
MAIL_ROOT_ENV_VAR = "WICKET_MAIL_ROOT"
MAIL_ROOT = Path(os.environ.get(MAIL_ROOT_ENV_VAR) or Path.home() / ".delphi" / "mail")

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


def _is_address(value: object) -> bool:
    return isinstance(value, str) and bool(ADDRESS_RE.match(value))


def _is_wildcard(value: object) -> bool:
    return isinstance(value, str) and bool(WILDCARD_ADDRESS_RE.match(value))


def _is_bucket(value: object) -> bool:
    return isinstance(value, str) and bool(BUCKET_RE.match(value))


def is_destination(value: object) -> bool:
    """A place mail can be filed: an account address, or a bucket name."""
    return _is_address(value) or _is_bucket(value)


def flatten_aliases(
    raw: object,
    path: Path,
    *,
    primary_ok: Callable[[str], bool],
    alias_ok: Callable[[str], bool],
    noun: str,
) -> dict[str, str]:
    """Flatten ``{primary: [alias, ...]}`` to ``{alias: primary, primary: primary}``.

    The one home for the alias-file rule, shared by the account map here and the
    domain map in `wicket.domains` (which passes its own validators). Rejects a
    key that is both a primary and an alias, and two primaries claiming the same
    alias — either would make the mapping order-dependent. ``noun`` names the
    entry kind in error messages ("address", "domain").
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level value must be an object")
    mapping: dict[str, str] = {}
    for primary, aliases in raw.items():
        if not isinstance(aliases, list):
            raise ValueError(f"{path}: value for {primary!r} must be a list")
        if not primary_ok(primary):
            raise ValueError(f"{path}: invalid primary {noun} {primary!r}")
        if primary in mapping and mapping[primary] != primary:
            raise ValueError(
                f"{path}: {primary!r} is both a primary and an alias of "
                f"{mapping[primary]!r}"
            )
        mapping[primary] = primary
        for alias in aliases:
            if not alias_ok(alias):
                raise ValueError(f"{path}: invalid alias {noun} {alias!r}")
            existing = mapping.get(alias)
            if existing is not None and existing != primary:
                raise ValueError(
                    f"{path}: {alias!r} aliased to both {existing!r} and {primary!r}"
                )
            mapping[alias] = primary
    return mapping


def account_aliases_path(mail_root: Path | None = None) -> Path:
    """The one home for the account-alias file (``<mail-root>/account-aliases.json``).

    ``$WICKET_ACCOUNT_ALIASES_FILE``, when set, names the file directly (any name,
    any location) and wins over ``mail_root``.
    """
    override = os.environ.get(ACCOUNT_ALIASES_FILE_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return (mail_root or MAIL_ROOT) / ACCOUNT_ALIASES_FILENAME


def load_account_aliases(path: Path | None = None) -> dict[str, str]:
    """Load ``address -> primary``, or ``{}`` when the file is absent.

    Keys are concrete addresses and ``*@domain`` wildcards; a primary is an
    account address or a bucket name. Only exceptions appear: an address that
    already names a store resolves to itself and is not in here. Missing file is
    silent (the map is optional); a malformed one raises.
    """
    file = path or account_aliases_path()
    if not file.exists():
        return {}
    mapping = flatten_aliases(
        json.loads(file.read_text(encoding="utf-8")),
        file,
        primary_ok=lambda p: _is_address(p) or _is_bucket(p),
        alias_ok=lambda a: _is_address(a) or _is_wildcard(a),
        noun="address",
    )
    return {normalize_account(k): normalize_account(v) for k, v in mapping.items()}


def alias_target(address: str, aliases: dict[str, str]) -> str | None:
    """The primary an address is an *exception* for, or None. Exact beats wildcard.

    An address that is not an exception returns None even when it plainly names an
    account: that "clear line" is not the alias map's business, and resolving it
    is `account_of`'s job, which knows which stores exist.
    """
    canonical = normalize_account(address)
    if canonical in aliases:
        return aliases[canonical]
    _, _, domain = canonical.partition("@")
    return aliases.get(f"*@{domain}") if domain else None


def account_of(address: str, aliases: dict[str, str], accounts: set[str]) -> str | None:
    """The account an address belongs to, or None if nothing claims it.

    Two rules, in order: the alias map (the exceptions), then the clear line — an
    address that names an existing store *is* that account. ``accounts`` is what
    exists on disk, so an alias pointing at a bucket you never created resolves to
    nothing rather than conjuring a store.
    """
    target = alias_target(address, aliases)
    if target is not None:
        return target if target in accounts else None
    canonical = normalize_account(address)
    return canonical if canonical in accounts else None


@dataclass(frozen=True)
class Identities:
    """Every address that is *you* — including whole wildcard domains.

    The filing rule asks "which side of this message is the counterparty"; knowing
    only one address, it mistakes your *other* addresses for counterparties and
    files a thread under your own burner domain (or not at all). This answers that
    question. It is a membership test rather than a set because a catch-all
    (``*@shopping.you.com``) cannot be enumerated. Owner-authored throughout: an
    address is yours because you listed it or it names your store, never because
    it was inferred.
    """

    addresses: frozenset[str]
    domains: frozenset[str]  # catch-all domains: every address here is yours

    def __contains__(self, address: object) -> bool:
        if not isinstance(address, str):
            return False
        canonical = normalize_account(address)
        if canonical in self.addresses:
            return True
        _, _, domain = canonical.partition("@")
        return bool(domain) and domain in self.domains


def identities(
    aliases: dict[str, str] | None = None, accounts: set[str] | None = None
) -> Identities:
    """Build the identity test from the alias map's keys and your account names."""
    mapping = load_account_aliases() if aliases is None else aliases
    mine = known_accounts() if accounts is None else accounts
    # Keys include the primaries, and a primary may be a bucket ("travel"), which
    # is a destination and not an address of yours. Only addresses are identities.
    addresses = {a for a in mapping if _is_address(a)}
    addresses |= {a for a in mapping.values() if _is_address(a)}
    addresses |= {a for a in mine if _is_address(a)}
    domains = {a.removeprefix("*@") for a in mapping if a.startswith("*@")}
    return Identities(frozenset(addresses), frozenset(domains))


def require_mail_root(mail_root: Path | None = None) -> Path:
    """Return the mail root, or raise if it is not there. **Writers FAIL CLOSED.**

    The mail tree is designed to live inside an encrypted (Cryptomator) vault. A
    LOCKED vault leaves its mount point as an ordinary empty directory — or no
    directory at all. If wicket treated that as "a fresh, empty store" it would
    cheerfully `mkdir` an account and write your mail **in the clear**, underneath
    the vault, where it would sit unencrypted and invisible to the vault forever.

    So the root is never created implicitly. It must already exist, which is also
    consistent with the rest of the design: creating a destination (an account, a
    bucket) is always an explicit owner `mkdir`, never something a verb infers.
    """
    root = mail_root or MAIL_ROOT
    if not root.exists():
        raise ValueError(
            f"mail root {root} does not exist. If it is an encrypted vault, it is "
            f"not mounted — unlock it and retry. wicket will not create it: writing "
            f"to an unmounted vault's mount point would leave your mail in plaintext "
            f"underneath it. Set ${MAIL_ROOT_ENV_VAR} if the tree lives elsewhere."
        )
    return root


def known_accounts(mail_root: Path | None = None) -> set[str]:
    """Accounts that exist: a store directory under the mail root. Nothing else.

    A bucket or an alias primary that has no directory is deliberately absent: the
    `mkdir` is the owner's explicit act of creating a destination, and without it
    mail for that primary stays unrouted instead of minting a store.
    """
    root = require_mail_root(mail_root)
    # Skip dotted directories: the store is often a git repo, and `.git` is not an
    # account. Nothing hidden is a destination.
    return {
        normalize_account(p.name)
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    }


def resolve_account(account: str | None) -> str:
    """Resolve the account from an explicit arg, the env var, or discovery.

    The one home for account resolution, shared by every verb's api. Order:
    the explicit ``account`` argument, then ``$WICKET_ACCOUNT``, then the sole
    directory under the mail root. The result is canonicalized with
    `normalize_account` and then mapped through the account-alias file, so an
    address you merely *receive* at (a burner, a forwarder) resolves to the
    account that owns it instead of minting a store of its own.

    The account set is closed once it exists: an unknown account is a hard error
    rather than a new directory, because a mistyped ``--account`` is otherwise
    indistinguishable from a real one and silently files mail into a store that
    should not exist. An empty mail root still bootstraps freely. Raises
    ``ValueError`` with a face-neutral message; the caller turns it into an exit.
    """
    require_mail_root()  # before anything else: an absent root is a locked vault,
    # not an empty store, and must not read as "no account" (or worse, bootstrap).
    chosen = account or os.environ.get(ACCOUNT_ENV_VAR) or discover_account()
    if not chosen:
        raise ValueError(
            f"no account: pass account=..., set ${ACCOUNT_ENV_VAR}, or keep "
            f"exactly one account directory under {MAIL_ROOT}."
        )
    resolved = normalize_account(chosen)
    existing = known_accounts()
    resolved = account_of(resolved, load_account_aliases(), existing) or resolved
    if existing and resolved not in existing:
        raise ValueError(
            f"unknown account {resolved!r}: it has no store under {MAIL_ROOT} and "
            f"is not listed in {account_aliases_path().name}. If it is an address "
            "you receive at, add it as an alias of the account that owns it; if it "
            "is genuinely a new mailbox, create its store directory first."
        )
    return resolved


def resolve_state_dir(
    account: str, override: str | os.PathLike[str] | None = None
) -> Path:
    """Return the per-account state dir, creating it owner-only (`0700`) if missing.

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
    """Per-store manifest dir (``<mail-root>/<store>/manifest``).

    The one home for this path, shared by `wicket.catalog` (writes shards) and
    `wicket.fetch` (reads + settles them, ADR 0002). The account is
    canonicalized so `+tag` aliases resolve to the same directory.
    """
    return MAIL_ROOT / normalize_account(account) / "manifest"


def resolve_archive_dir(account: str) -> Path:
    """Per-store .eml archive root (``<mail-root>/<store>/archive``).

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
    """Write owner-only (`0600`) text, replacing any existing file."""
    path.write_text(data, encoding="utf-8")
    os.chmod(path, 0o600)
