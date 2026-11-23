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
import logging
import os
import re
import socket
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import TracebackType

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
# name) — the same escape hatch $WICKET_DATA_DIR gives the mail tree itself, for
# an owner who wants this one map to live somewhere else (a second vault, a
# dotfiles repo). Checked by `account_aliases_path`; unset means the default
# `<mail-root>/account-aliases.json`.
ACCOUNT_ALIASES_ENV_VAR = "WICKET_ACCOUNT_ALIASES"

# The counterparty map, and the mirror image of the one above: it lists domains
# that are NOT you, and the store their mail belongs in ("ikea.com" -> shopping).
# Loaded by `wicket.domains` (the domain rules live there); named here so both
# owner-authored files have one home. Keeping the two apart is load bearing: a
# counterparty listed as an alias would make the filing rule think it is *you*.
DOMAIN_ROUTES_FILENAME = "domain-routes.json"

# Path overrides for the two domain maps, same shape as ACCOUNT_ALIASES_ENV_VAR
# above. Named here (not in `wicket.domains`) so every owner-authored map's
# filenames AND overrides share one home; `wicket.domains`'s path functions read them.
DOMAIN_ROUTES_ENV_VAR = "WICKET_DOMAIN_ROUTES"
DOMAIN_ALIASES_ENV_VAR = "WICKET_DOMAIN_ALIASES"

# The fourth owner-authored map: mailing lists whose mail is never fetched or filed,
# ``{"*": [list-id], "<account>": [list-id]}`` ("*" is every account). Read by
# `wicket.exclusions`; named here so every owner map's filename and override share one home.
EXCLUDED_LISTS_FILENAME = "excluded-lists.json"
EXCLUDED_LISTS_ENV_VAR = "WICKET_EXCLUDED_LISTS"

# A fifth map's names, reserved: sender exclusion is not built yet, and nothing reads these.
EXCLUDED_SENDERS_FILENAME = "excluded_senders.json"
EXCLUDED_SENDERS_ENV_VAR = "WICKET_EXCLUDED_SENDERS"

# An email address. Deliberately permissive in the local part (burner services
# mint odd ones) and strict in the domain.
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
# When present, fetch treats aliases as the same logical entity
# as the primary (search expands; a domain filter folds to the primary).
ALIASES_FILENAME = "domain-aliases.json"

# The owner's Gmail address is intentionally not baked in. A verb that needs
# an account reads this env var or takes --mail-account; missing-and-not-passed
# is a hard error.
ACCOUNT_ENV_VAR = "WICKET_ACCOUNT"

# One per-account mail root holding both the year-sharded manifest store and the
# message files (ADR 0002): $WICKET_DATA_DIR/<account>/{manifest,messages,staging}/.
# Provider-neutral (credentials live separately under ~/.config/<provider>/).
#
# The PHYSICAL location is configuration, not code: $WICKET_DATA_DIR overrides
# it (a leading ~ expanded), so the tree can follow an encrypted vault to whatever
# path it mounts at. wicket is standalone and reads its OWN env var; it never
# imports delphi.
DATA_DIR_ENV_VAR = "WICKET_DATA_DIR"
MAIL_ROOT = Path(
    os.environ.get(DATA_DIR_ENV_VAR) or Path.home() / ".data" / "mail"
).expanduser()

# wicket's state base, ``$XDG_STATE_HOME/wicket``: what a run leaves for the next, never
# mail and never a secret. XDG's state home, so it sits outside both the vault and
# ~/.config, which is for configuration. Read at call time, so a test can point it at a
# temp dir. Two dirs under it, never one: ``halt/<account>`` (present means halted) and
# ``log/``, which holds ``<account>.log`` (counts, years and HALT causes) and
# ``wicket.log`` (every event of every run). An account is an address, so no account's
# log is named ``wicket.log``.
STATE_HOME_ENV_VAR = "XDG_STATE_HOME"
DEFAULT_STATE_HOME = Path.home() / ".local" / "state"
HALT_DIRNAME = "halt"
LOG_DIRNAME = "log"
WICKET_LOG_FILENAME = "wicket.log"

# The logger every module of the package logs under (``logging.getLogger(__name__)`` is a
# child of it), and the program name in each log line's header.
LOGGER_NAME = "wicket"
PROGRAM = "wicket"

# The store lock's file, inside the manifest dir it guards (the lock follows the data).
LOCK_FILENAME = ".lock"

# Where `fetch` gets a body before `ingest --staged` files it: inside the store, so
# a staged body sits in the vault beside the archive it is bound for.
STAGING_DIRNAME = "staging"

# The one home for duplicate copies, at the mail root: <mail root>/.duplicates/<store>/
# <path>. Dotted, so it is no store (`known_accounts` skips it), and never unlinked.
DUPLICATES_DIRNAME = ".duplicates"

# Default worker-thread count for tools that parallelize IMAP work
# (catalog year workers, fetch body workers). One worker = one IMAP
# connection (imaplib is not thread-safe across commands); Gmail caps
# simultaneous IMAP connections (~15), so stay well under.
DEFAULT_THREADS = 4

# How long one IMAP connect or command may take. Each provider's host and folders
# live in `wicket.provider`.
IMAP_TIMEOUT_SECONDS = 60


def normalize_account(address: str) -> str:
    """Lowercase and strip Gmail `+tag` aliasing.

    Gmail treats `you+anything@gmail.com` as the same mailbox as
    `you@gmail.com`. Canonicalize so an alias still counts as the account and
    scopes to the same store paths in every tool. The one home for this rule.
    """
    address = address.strip().lower()
    if "@" not in address:
        return address
    local, _, domain = address.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


class NoMailRoot(ValueError):
    """The mail root does not exist: a locked vault, never an empty store."""

    reason = "no_mail_root"


class NoAccount(ValueError):
    """No account was named, and the accounts dir does not hold exactly one."""

    reason = "no_account"


class UnknownAccount(ValueError):
    """The named account has no store under the mail root."""

    reason = "unknown_account"


class BadMap(ValueError):
    """An owner-authored map file is malformed."""

    reason = "bad_map"


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
        raise BadMap(f"{path}: top-level value must be an object")
    mapping: dict[str, str] = {}
    for primary, aliases in raw.items():
        if not isinstance(aliases, list):
            raise BadMap(f"{path}: value for {primary!r} must be a list")
        if not primary_ok(primary):
            raise BadMap(f"{path}: invalid primary {noun} {primary!r}")
        if primary in mapping and mapping[primary] != primary:
            raise BadMap(
                f"{path}: {primary!r} is both a primary and an alias of "
                f"{mapping[primary]!r}"
            )
        mapping[primary] = primary
        for alias in aliases:
            if not alias_ok(alias):
                raise BadMap(f"{path}: invalid alias {noun} {alias!r}")
            existing = mapping.get(alias)
            if existing is not None and existing != primary:
                raise BadMap(
                    f"{path}: {alias!r} aliased to both {existing!r} and {primary!r}"
                )
            mapping[alias] = primary
    return mapping


def map_path(env_var: str, filename: str, mail_root: Path | None = None) -> Path:
    """Where an owner-authored map is: ``$<env_var>`` when set, else the mail root's file.

    The variable names the file directly (any name, any location, a leading ``~``
    expanded) and wins over ``mail_root``, which defaults to `MAIL_ROOT`. The one home of
    that rule for all four maps.
    """
    override = os.environ.get(env_var)
    if override:
        return Path(override).expanduser()
    return (mail_root or MAIL_ROOT) / filename


def read_json_map(path: Path) -> object:
    """The JSON at ``path``, or ``{}`` when the file is absent: every map loader's read.

    The caller's own rule checks what comes back; a file that is not JSON raises
    `BadMap`, a ``ValueError``, with the decoder's text.
    """
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BadMap(str(exc)) from exc


def account_aliases_path(mail_root: Path | None = None) -> Path:
    """The account-alias file (``<mail-root>/account-aliases.json``; `map_path`)."""
    return map_path(ACCOUNT_ALIASES_ENV_VAR, ACCOUNT_ALIASES_FILENAME, mail_root)


def load_account_aliases(path: Path | None = None) -> dict[str, str]:
    """Load ``address -> primary``, or ``{}`` when the file is absent.

    Keys are concrete addresses and ``*@domain`` wildcards; a primary is an
    account address or a bucket name. Only exceptions appear: an address that
    already names a store resolves to itself and is not in here. Missing file is
    silent (the map is optional); a malformed one raises.
    """
    file = path or account_aliases_path()
    return parse_account_aliases(read_json_map(file), file)


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
        raise NoMailRoot(
            f"mail root {root} does not exist. If it is an encrypted vault, it is "
            f"not mounted. wicket will not create it: writing to an unmounted "
            f"vault's mount point would leave your mail in plaintext underneath it."
        )
    return root


def known_accounts() -> set[str]:
    """Accounts that exist: a store directory under the mail root. Nothing else.

    A bucket or an alias primary that has no directory is deliberately absent: the
    `mkdir` is the owner's explicit act of creating a destination, and without it
    mail for that primary stays unrouted instead of minting a store.
    """
    root = require_mail_root()
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
    should not exist. An empty mail root still bootstraps freely. Raises `NoMailRoot`,
    `NoAccount` or `UnknownAccount` (each a ``ValueError``) with a face-neutral message;
    the caller turns it into an exit.
    """
    require_mail_root()  # before anything else: an absent root is a locked vault,
    # not an empty store, and must not read as "no account" (or worse, bootstrap).
    chosen = account or os.environ.get(ACCOUNT_ENV_VAR)
    if not chosen:
        found = account_dirs()
        if len(found) != 1:
            raise NoAccount(
                f"no account: {len(found)} folders in {resolve_accounts_dir()} hold "
                f"an {CREDENTIALS_FILENAME}, not one."
            )
        chosen = found[0].name
    resolved = normalize_account(chosen)
    existing = known_accounts()
    resolved = account_of(resolved, load_account_aliases(), existing) or resolved
    if existing and resolved not in existing:
        raise UnknownAccount(
            f"unknown account {resolved!r}: it has no store under {MAIL_ROOT} and "
            f"is not listed in {account_aliases_path().name}."
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


def account_folders(override: str | os.PathLike[str] | None = None) -> list[Path]:
    """Every folder in ``~/.config/wicket/accounts``, with or without ``imap.json``.

    The one home of the folder listing, and what `report` lists (owner, 2026-11-22:
    "report should look at any thing in ~/.config/wicket/accounts/ whether or not it
    has an imap.json"), so an account whose mail is only ever ingested gets a row.
    Dotted folders are skipped, as `known_accounts` skips them. Sorted by name. Reads
    only; creates nothing. ``override`` replaces the base, as `resolve_accounts_dir`
    takes it.
    """
    root = resolve_accounts_dir(override)
    if not root.is_dir():
        return []
    return [
        p for p in sorted(root.iterdir()) if p.is_dir() and not p.name.startswith(".")
    ]


def account_dirs(override: str | os.PathLike[str] | None = None) -> list[Path]:
    """Every IMAP account: each of `account_folders` holding ``imap.json``.

    The one home of the IMAP account list (owner, 2026-10-28: "list of accounts are all
    directories that contain an imap.json"), which every verb but `report` reads. A
    link counts even when its target does not exist yet. Sorted by name. Reads only;
    creates nothing. ``override`` replaces the base, as `resolve_accounts_dir` takes it.
    """
    return [
        p
        for p in account_folders(override)
        if (p / CREDENTIALS_FILENAME).is_symlink()
        or (p / CREDENTIALS_FILENAME).exists()
    ]


def resolve_store_dir(account: str) -> Path:
    """Per-store manifest dir (``<mail-root>/<store>/manifest``).

    The one home for this path, shared by `wicket.root.lib.catalog` (writes
    shards) and `wicket.root.lib.fetch` (reads + settles them, ADR 0002). The
    account is canonicalized so `+tag` aliases resolve to the same directory.
    """
    return MAIL_ROOT / normalize_account(account) / "manifest"


def resolve_messages_dir(account: str) -> Path:
    """Per-store message files (``<mail-root>/<store>/messages``).

    Holds the ``YYYY/MM/<stem>.eml`` tree `wicket.root.lib.ingest` writes, one
    file per message (`manifest.message_file`; ADR-MAIL, 2026-11-14); sibling to
    the manifest store under one mail root. Never created here.
    """
    return MAIL_ROOT / normalize_account(account) / "messages"


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
    kept as ``YYYY-MM/<name>.eml`` under it (the provider's name for the message,
    `Provider.file_stem`), never unlinked. `wicket.root.lib.fetch` counts what is here
    as got. Never created here.
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


def resolve_wicket_log_path() -> Path:
    """``wicket.log`` (``<state base>/log/wicket.log``): every event of every run.

    Beside the account logs, in the same owner-only dir. Never created here.
    """
    return _state_base() / LOG_DIRNAME / WICKET_LOG_FILENAME


# --- The log line: one formatter, two logs ------------------------------------------
#
# A record carries a human message, an event with its fields (``extra=event(...)``), or
# both. The stderr handler prints the message, as the terminal always showed it; the
# ``wicket.log`` handler writes the event as one line in the syslog file shape with a
# logfmt body:
#
#   2026-11-15T08:00:03-05:00 vulcan wicket[41205]: account=you@gmail.com level=info
#   event=fetch.search matched=40 got=23 candidates=17
#
# (one line). The events and their fields:
#
#   fetch.start      pass=filtered|all|years years=           api, account log
#   fetch.search     matched got candidates beyond_max         root.lib.fetch
#   fetch.plan       pending months                            root.lib.fetch
#   fetch.month      done total saved failed skipped           root.lib.fetch
#   fetch.year       year matched candidates downloaded failed linked got   account log
#   fetch.end        downloaded failed linked halted=true      api, account log
#   catalog.plan     years first last threads                  root.lib.catalog
#   catalog.year     year observed in_shard dry_run=true       root.lib.catalog
#   catalog.discover empty=true                                root.lib.catalog
#   catalog.end      parse_skips label_misses (warning)        renderer
#   ingest.end       added copies excluded excluded_lists refused unrecognized
#                    (staged); added skipped (src)             api, account log
#   ingest.file      file outcome=refused|unrecognized (warning)            renderer
#   ingest.route     forced=true reason=account_mismatch (warning)          renderer
#   ingest.busy      lock_pid left (warning, account= the busy store)       api
#   halt.set         cause                                     auth, account log
#   halt.clear       (none)                                    auth, account log
#   wicket.end       outcome=refused exit reason cause since lock_pid (error)
#                                                              errors.refusals
#   wicket.end       outcome=crashed error at (critical)       excepthook
#   cron.run, cron.exit  verb args, verb code                  cronjob.sh
#
# A dry run's events (fetch, catalog, ingest) also carry ``dry_run=true``, and reach
# ``wicket.log`` alone: a dry run writes no account log. A new log statement adds a row.
# An event is ``<verb>.<step>``: a step is a place in the run, never an outcome; an
# outcome is a field.

# The account a run is for, read by the formatter when a record names none. A module
# global, not a context variable: worker threads must see the value the api set. One
# slot, ``"account"``; `errors.refusals` clears it when each request starts.
_PROCESS: dict[str, str | None] = {"account": None}


def set_process_account(account: str | None) -> None:
    """Name the account this process runs for (the api sets it; None clears it)."""
    _PROCESS["account"] = account


def process_account() -> str | None:
    """The account `set_process_account` named, or None."""
    return _PROCESS["account"]


def event(name: str, account: str | None = None, **fields: object) -> dict[str, object]:
    """A record's ``extra=``: its event, the account it is about, and its fields, in order.

    ``account`` names the record's own account where a process touches several (a
    routed ingest, ``halt clear --all``, a worker's HALT); without it the line takes the
    process account. A field with no value (None, empty or false) is left off the line.
    """
    return {"event": name, "account": account, "fields": fields}


def _present(value: object) -> bool:
    """Whether a field has a value: None, False and an empty string or collection have none."""
    if value is None or value is False:
        return False
    if isinstance(value, (str, list, tuple, dict, set, frozenset)):
        return bool(value)
    return True


def _needs_quotes(c: str) -> bool:
    """A character that makes a value quoted: a space, ``=``, ``"``, or any character
    ``str.isprintable`` rejects (controls, C1, U+2028 and every other character
    ``str.splitlines`` breaks a line on)."""
    return c in ' ="' or not c.isprintable()


def _escaped(c: str) -> str:
    """``c`` inside a quoted value: ``\\"``, ``\\\\``, ``\\n``, ``\\t``, ``\\uXXXX`` for any
    other non-printable character (a UTF-16 surrogate pair above U+FFFF), else itself.
    """
    if c in '"\\':
        return "\\" + c
    if c == "\n":
        return "\\n"
    if c == "\t":
        return "\\t"
    if c.isprintable():
        return c
    code = ord(c)
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    code -= 0x10000
    return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"


def _logfmt_value(value: object) -> str:
    """One logfmt value: bare, or double-quoted when it holds a space, ``=``, ``"`` or a
    character that is not printable, escaped (`_escaped`) so a value never breaks the
    line, whoever splits it."""
    text = "true" if value is True else str(value)
    if not any(_needs_quotes(c) for c in text):
        return text
    return '"' + "".join(_escaped(c) for c in text) + '"'


class LogfmtFormatter(logging.Formatter):
    """A record as one log line: ``TIME HOST wicket[PID]:``, then logfmt fields.

    The header is rsyslog's file form: the record's time to the second, in local time
    with its offset (RFC 3339), the host name up to its first dot, and ``wicket[pid]``.
    The body is ``account=``, ``level=`` and ``event=``, then the event's fields in the
    order given; a field with no value is left out. The one home of the line's shape.
    """

    def format(self, record: logging.LogRecord) -> str:
        when = datetime.fromtimestamp(record.created).astimezone()
        host = socket.gethostname().split(".", 1)[0]
        head = (
            f"{when.isoformat(timespec='seconds')} {host} {PROGRAM}[{record.process}]:"
        )
        pairs: list[tuple[str, object]] = [
            ("account", getattr(record, "account", None) or _PROCESS["account"]),
            ("level", record.levelname.lower()),
            ("event", getattr(record, "event", None)),
        ]
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            pairs += list(fields.items())
        body = " ".join(f"{k}={_logfmt_value(v)}" for k, v in pairs if _present(v))
        return f"{head} {body}"


def _append_line(path: Path, line: str) -> None:
    """Append ``line`` to ``path`` in one ``O_APPEND`` write: dir ``0700``, file ``0600``.

    Opened at each write, never held: a file another tool rotated away is made anew.
    Raises on failure.
    """
    _owner_only_dir(path.parent, True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, f"{line}\n".encode("utf-8"))
    finally:
        os.close(fd)


def append_log(account: str, name: str, **fields: object) -> None:
    """Append event ``name`` to the account's log, then hand it on: the one account-log writer.

    The line is `LogfmtFormatter`'s, written here directly, owner-only, and a failed
    write raises: the account log is the durable record (`auth.clear_halt` logs, then
    unlinks). Then the same record goes to the ``wicket`` logger, so ``wicket.log`` holds
    the same line and stderr prints nothing (the record has no message). A line carries
    counts, years and HALT causes only, never a subject, an address other than the
    account's own (``account=``), a path or a secret: the state dir sits outside the vault.
    """
    logger = logging.getLogger(LOGGER_NAME)
    record = logger.makeRecord(
        LOGGER_NAME,
        logging.INFO,
        __file__,
        0,
        "",
        (),
        None,
        extra=event(name, account=account, **fields),
    )
    _append_line(resolve_log_path(account), LogfmtFormatter().format(record))
    logger.handle(record)


class WicketLogHandler(logging.Handler):
    """Append each record that carries an event to ``wicket.log``; skip the rest.

    Best effort: a write that fails goes to ``handleError`` and the run goes on. The file
    is opened at each write with mode ``0600`` (`_append_line`), never by
    ``logging.FileHandler``, which would make it at the umask's mode.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if not getattr(record, "event", None):
            return
        try:
            _append_line(resolve_wicket_log_path(), self.format(record))
        except Exception:  # pylint: disable=broad-exception-caught
            self.handleError(record)


class StderrHandler(logging.Handler):
    """Print each record that carries a human message on stderr, as the terminal shows it.

    ``sys.stderr`` is read at each write, so a redirect made after setup is followed. A
    record with no message (an event only) prints nothing.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not record.getMessage():
                return
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:  # pylint: disable=broad-exception-caught
            self.handleError(record)


_PACKAGE_DIR = Path(__file__).resolve().parent


def _where(tb: TracebackType | None) -> str | None:
    """``wicket/<file>.py:<line>`` of the innermost frame inside the package, or None."""
    found = None
    for frame, lineno in traceback.walk_tb(tb):
        path = Path(frame.f_code.co_filename).resolve()
        if _PACKAGE_DIR in path.parents:
            found = f"{path.relative_to(_PACKAGE_DIR.parent)}:{lineno}"
    return found


def _log_crash(exc: BaseException, tb: TracebackType | None) -> None:
    """``wicket.end outcome=crashed`` for an exception nothing caught: event only."""
    logging.getLogger(LOGGER_NAME).critical(
        "",
        extra=event(
            "wicket.end",
            outcome="crashed",
            error=f"{type(exc).__name__}: {exc}",
            at=_where(tb),
        ),
    )


def configure_logging() -> None:
    """Set up wicket's logging for a face's run: every face's ``main`` calls it first.

    Sets the ``wicket`` logger to INFO and adds the two handlers, once: `StderrHandler`
    (human messages, as today) and `WicketLogHandler` (events, to ``wicket.log``, in every
    run, whatever started it). Records also propagate to the root logger. A crash on the
    main thread or a worker thread logs ``wicket.end outcome=crashed`` before the
    hook that was in place prints its traceback; KeyboardInterrupt (and SystemExit in a
    thread) passes to that hook untouched. Idempotent: the handlers present means set up.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if any(isinstance(h, (StderrHandler, WicketLogHandler)) for h in logger.handlers):
        return
    stderr = StderrHandler()
    stderr.setFormatter(logging.Formatter("%(message)s"))
    wicket_log = WicketLogHandler()
    wicket_log.setFormatter(LogfmtFormatter())
    logger.addHandler(stderr)
    logger.addHandler(wicket_log)

    previous = sys.excepthook

    def crash(
        kind: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        if not issubclass(kind, KeyboardInterrupt):
            _log_crash(exc, tb)
        previous(kind, exc, tb)

    previous_thread = threading.excepthook

    def thread_crash(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None and not issubclass(
            args.exc_type, (KeyboardInterrupt, SystemExit)
        ):
            _log_crash(args.exc_value, args.exc_traceback)
        previous_thread(args)

    sys.excepthook = crash
    threading.excepthook = thread_crash


# A library's logger: with no face's `configure_logging`, records go nowhere of wicket's
# own (a consumer's root handlers still see them), and Python's last-resort handler never
# prints an event-only record as an empty line.
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())


class StoreBusy(RuntimeError):
    """Another process holds the store's lock. A face reports it as a refusal, exit 2.

    ``facts`` are the refusal's: the store (``account``) and the holder's pid
    (``lock_pid``, absent when the lock file names none).
    """

    reason = "busy"

    def __init__(self, manifest_dir: Path, pid: str) -> None:
        super().__init__(
            f"store {manifest_dir.parent.name} is busy: pid {pid or '?'} holds "
            f"{manifest_dir / LOCK_FILENAME}."
        )
        self.manifest_dir = manifest_dir
        self.pid = pid
        self.facts: dict[str, object] = {
            "account": manifest_dir.parent.name,
            "lock_pid": pid or None,
        }


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
