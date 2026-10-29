"""Shared configuration and secret-handling helpers.

Shared across the verbs. The two that touch IMAP (`catalog`, `fetch`)
authenticate with an app password, not OAuth, to the provider `wicket.provider`
names. The credential file is JSON of the form
``{"email": "...", "app_password": "..."}`` (mode `0600`).
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Secrets live OUTSIDE the repo (never committed, never on the command line).
# One folder per account, ``accounts/<account>/``, holding ``imap.json``: a link to the
# provider's file (``~/.config/<gmail|fastmail>/<login>/imap.json``), made by hand with ``ln -s``
# or by a noun. The one home of an account.
DEFAULT_STATE_DIR = Path.home() / ".config" / "wicket"
ACCOUNTS_DIRNAME = "accounts"

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
# above. Named here (not in `wicket.domains`) so every owner-authored map's
# filenames AND overrides share one home; `wicket.domains`'s path functions read them.
DOMAIN_ROUTES_FILE_ENV_VAR = "WICKET_DOMAIN_ROUTES_FILE"
DOMAIN_ALIASES_FILE_ENV_VAR = "WICKET_DOMAIN_ALIASES_FILE"

# The fourth owner-authored map: mailing lists whose mail is never fetched or filed,
# ``{"*": [list-id], "<account>": [list-id]}`` ("*" is every account). Read by
# `wicket.exclusions`; named here so every owner map's filename and override share one home.
EXCLUDED_LISTS_FILENAME = "excluded-lists.json"
EXCLUDED_LISTS_FILE_ENV_VAR = "WICKET_EXCLUDED_LISTS_FILE"

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

# Optional alias file consumed by fetch (`wicket.root.api`). Shape:
#   {"primary.com": ["alias1.com", "alias2.com"], ...}
# When present, the archive tool treats aliases as the same logical entity
# as the primary (search expands; the filing domain canonicalizes to primary).
ALIASES_FILENAME = "domain-aliases.json"

# The owner's Gmail address is intentionally not baked in. Tools that need
# an account (the filing-domain rule, census's inventory scoping) read this
# env var or take --mail-account; missing-and-not-passed is a hard error.
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

# wicket's state base, ``$XDG_STATE_HOME/wicket``: what a run leaves for the next, never
# mail and never a secret. XDG's state home, so it sits outside both the vault and
# ~/.config, which is for configuration. Read at call time, so a test can point it at a
# temp dir. Two dirs under it, never one: ``halt/<account>`` (present means halted) and
# ``log/<account>.log`` (counts, years and HALT causes).
STATE_HOME_ENV_VAR = "XDG_STATE_HOME"
DEFAULT_STATE_HOME = Path.home() / ".local" / "state"
HALT_DIRNAME = "halt"
LOG_DIRNAME = "log"

# The store lock's file, inside the manifest dir it guards (the lock follows the data).
LOCK_FILENAME = ".lock"

# Where `fetch` gets a body before `ingest --staged` files it: inside the store, so
# a staged body sits in the vault beside the archive it is bound for.
STAGING_DIRNAME = "staging"

# The one home for duplicate copies, at the mail root: <mail root>/.duplicates/<store>/
# <path>. Dotted, so it is no store (`known_accounts` skips it), and never unlinked.
DUPLICATES_DIRNAME = ".duplicates"

# Default worker-thread count for tools that parallelize IMAP work
# (census year workers, gmail body workers). One worker = one IMAP
# connection (imaplib is not thread-safe across commands); Gmail caps
# simultaneous IMAP connections (~15), so stay well under.
DEFAULT_THREADS = 4

# How long one IMAP connect or command may take. Each provider's host and folders
# live in `wicket.provider`.
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
    return parse_account_aliases(json.loads(file.read_text(encoding="utf-8")), file)


def parse_account_aliases(raw: object, path: Path) -> dict[str, str]:
    """The account-alias rule over a map already in memory; ``path`` names it in errors.

    `load_account_aliases` reads the file through this, and `wicket.store` checks a map it
    is about to write through it, so a file the writer saves is one the reader accepts.
    """
    mapping = flatten_aliases(
        raw,
        path,
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
    """Resolve the account from an explicit arg, the env var, or the only account folder.

    The one home for account resolution, shared by every verb's api. Order:
    the explicit ``account`` argument, then ``$WICKET_ACCOUNT``, then the one folder in
    ``~/.config/wicket/accounts`` holding ``imap.json`` (`account_dirs`): with one to
    choose from, naming it is unnecessary (owner, 2026-10-28). No folder, or several,
    raises, naming how many. The result is canonicalized with
    `normalize_account` and then mapped through the account-alias file, so an
    address you merely *receive* at (a burner, a forwarder) resolves to the
    account that owns it instead of minting a store of its own.

    The account set is closed once it exists: an unknown account is a hard error
    rather than a new directory, because a mistyped ``--mail-account`` is otherwise
    indistinguishable from a real one and silently files mail into a store that
    should not exist. An empty mail root still bootstraps freely. Raises
    ``ValueError`` with a face-neutral message; the caller turns it into an exit.
    """
    require_mail_root()  # before anything else: an absent root is a locked vault,
    # not an empty store, and must not read as "no account" (or worse, bootstrap).
    chosen = account or os.environ.get(ACCOUNT_ENV_VAR)
    if not chosen:
        found = account_dirs()
        if len(found) != 1:
            raise ValueError(
                f"no account: {len(found)} folders in {resolve_accounts_dir()} hold "
                f"an {CREDENTIALS_FILENAME}, not one; pass --mail-account (or set "
                f"${ACCOUNT_ENV_VAR})."
            )
        chosen = found[0].name
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


class NotLinked(Exception):
    """The account has no folder under the state dir, so a verb does nothing.

    A face maps this to a silent exit 0 (owner, 2026-10-27: a missing link is a no-op).
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"no account folder at {path}")
        self.path = path


def resolve_accounts_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """The accounts dir, ``<base>/accounts``; never created here.

    ``base`` defaults to `DEFAULT_STATE_DIR` (``~/.config/wicket``); ``override``
    replaces it.
    """
    base = Path(override).expanduser() if override else DEFAULT_STATE_DIR
    return base / ACCOUNTS_DIRNAME


def resolve_state_dir(
    account: str, override: str | os.PathLike[str] | None = None
) -> Path:
    """Return the account's folder, ``<base>/accounts/<account>``; never create it.

    Credentials live at ``<base>/accounts/<account>/imap.json``, a link to the provider's
    file. ``base`` defaults to `DEFAULT_STATE_DIR` (``~/.config/wicket``); ``override``
    replaces it. The account is canonicalized so `+tag` aliases resolve to the same
    folder. Raises `NotLinked` when the folder is absent; a folder without ``imap.json``
    is returned, so the credential is prompted for or refused, never skipped.
    """
    path = resolve_accounts_dir(override) / normalize_account(account)
    if not path.is_dir():
        raise NotLinked(path)
    return path


def account_dirs(override: str | os.PathLike[str] | None = None) -> list[Path]:
    """Every account: each folder in ``~/.config/wicket/accounts`` holding ``imap.json``.

    The one home of the list (owner, 2026-10-28: "list of accounts are all directories
    that contain an imap.json"). A link counts even when its target does not exist yet;
    a folder without one is no account. Sorted by name. Reads only; creates nothing.
    ``override`` replaces the base, as `resolve_accounts_dir` takes it.
    """
    root = resolve_accounts_dir(override)
    if not root.is_dir():
        return []
    return [
        p
        for p in sorted(root.iterdir())
        if p.is_dir()
        and (
            (p / CREDENTIALS_FILENAME).is_symlink()
            or (p / CREDENTIALS_FILENAME).exists()
        )
    ]


def resolve_store_dir(account: str) -> Path:
    """Per-store manifest dir (``<mail-root>/<store>/manifest``).

    The one home for this path, shared by `wicket.root.lib.catalog` (writes
    shards) and `wicket.root.lib.fetch` (reads + settles them, ADR 0002). The
    account is canonicalized so `+tag` aliases resolve to the same directory.
    """
    return MAIL_ROOT / normalize_account(account) / "manifest"


def resolve_archive_dir(account: str) -> Path:
    """Per-store .eml archive root (``<mail-root>/<store>/archive``).

    Holds the ``<domain>/YYYY-MM/<msgid>.eml`` tree `wicket.root.lib.ingest`
    writes; sibling to the manifest store under one mail root.
    """
    return MAIL_ROOT / normalize_account(account) / "archive"


def resolve_staging_dir(account: str) -> Path:
    """Per-store staging dir (``<mail-root>/<store>/staging``), where `fetch` gets bodies.

    The one home for this path: `wicket.root.lib.fetch` writes
    its provider's staged name here (`Provider.staged_name`) and
    `wicket.root.lib.ingest` files them from here into the archive. Never created
    here.
    """
    return MAIL_ROOT / normalize_account(account) / STAGING_DIRNAME


def resolve_duplicates_dir(account: str) -> Path:
    """Per-store duplicates dir (``<mail-root>/.duplicates/<store>``).

    The one home for a body set aside as a copy of mail the store already archives,
    kept at the path it would have had (``<folder>/YYYY-MM/<msgid>.eml``), never
    unlinked. `wicket.root.lib.fetch` counts what is here as got. Never created
    here.
    """
    return MAIL_ROOT / DUPLICATES_DIRNAME / normalize_account(account)


def _state_base() -> Path:
    """``$XDG_STATE_HOME/wicket`` (default ``~/.local/state/wicket``), read at call time."""
    base = Path(os.environ.get(STATE_HOME_ENV_VAR) or DEFAULT_STATE_HOME).expanduser()
    return base / "wicket"


def _owner_only_dir(path: Path, create: bool) -> Path:
    """``path``, made owner-only (``0700``) when ``create``; never made otherwise."""
    if create:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)  # mkdir mode is umask-masked; enforce explicitly.
    return path


def resolve_halt_dir(*, create: bool = False) -> Path:
    """The halt dir (``<state base>/halt``): one file per halted account, nothing else.

    The one home for this path. ``wicket.auth`` is the only code that reads or writes
    inside it. Not created unless ``create``: only `auth.write_halt` makes it.
    """
    return _owner_only_dir(_state_base() / HALT_DIRNAME, create)


def resolve_halt_path(account: str) -> Path:
    """The account's HALT file (``<state base>/halt/<account>``). Present means halted.

    The account is canonicalized so ``+tag`` aliases resolve to the same file.
    """
    return resolve_halt_dir() / normalize_account(account)


def resolve_log_path(account: str) -> Path:
    """The account's log (``<state base>/log/<account>.log``). Never created here.

    The account is canonicalized so ``+tag`` aliases resolve to the same file.
    """
    return _state_base() / LOG_DIRNAME / f"{normalize_account(account)}.log"


def append_log(account: str, line: str) -> None:
    """Append one locally timestamped line to the account's log: the one log writer.

    The log dir is made owner-only (``0700``) and the file ``0600`` on first write. A line
    carries counts, years and HALT causes only, never a subject, an address other
    than the account's own, a path or a secret: the state dir sits outside the vault. The
    caller owns what ``line`` says; a newline in it becomes a space, so one call is one line.
    """
    path = resolve_log_path(account)
    _owner_only_dir(path.parent, True)
    text = " ".join(line.splitlines())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")


class StoreBusy(RuntimeError):
    """Another process holds the store's lock. A face reports it as a refusal, exit 2."""

    def __init__(self, manifest_dir: Path, pid: str) -> None:
        super().__init__(
            f"store {manifest_dir.parent.name} is busy: pid {pid or '?'} holds "
            f"{manifest_dir / LOCK_FILENAME}; re-run when it finishes."
        )
        self.manifest_dir = manifest_dir
        self.pid = pid


@contextmanager
def store_lock(manifest_dir: Path) -> Iterator[None]:
    """Hold ``<manifest_dir>/.lock`` for the block: one writer per store at a time.

    ``fcntl.flock``, non-blocking: a held lock raises `StoreBusy` naming the pid the
    holder wrote into the file. The kernel drops the lock when the holder dies, so a
    crash leaves nothing to clean up. Not re-entrant: a second take in one process
    fails like any other (flock locks the open file, and this opens a new one), so an
    api takes each store's lock once and nothing nests. The manifest dir is made as
    ``write_shard`` makes it; the mail root itself is checked by `resolve_account`
    before any verb gets here.
    """
    manifest_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(manifest_dir / LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise
            holder = os.pread(fd, 32, 0).decode("ascii", "replace").strip()
            raise StoreBusy(manifest_dir, holder) from exc
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode("ascii"), 0)
        try:
            yield
        finally:
            os.ftruncate(fd, 0)  # still held: no other writer can see a stale pid
    finally:
        os.close(fd)  # closing the descriptor releases the flock


def write_secret(path: Path, data: str) -> None:
    """Write owner-only (`0600`) text, replacing any existing file."""
    path.write_text(data, encoding="utf-8")
    os.chmod(path, 0o600)
