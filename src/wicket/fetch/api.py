"""Orchestration for the fetch verb: resolve, build query, seed, download.

The single place the fetch face reaches the engine. fetch only gets mail into the
store's staging dir; ``ingest --staged`` files it (plan rule 6). It resolves the
account and its per-account archive/staging/store/credential paths, builds the
Gmail search expression from ``domains`` or ``query`` (at most one), seeds
credentials (gated on ``interactive``), and dispatches into `lib.download` (a filter: one pass) or
`lib.download_years` (no filter: every year until throttled). A real run holds the
store's lock (`env.store_lock`) and appends counts and years, never subjects,
addresses or paths, to the account's log (`env.append_log`: ``log/<account>.log``
under ``$XDG_STATE_HOME/wicket``). The read-side helper
`held_messages` (no IMAP, no credentials) lives here too. ``AuthError`` and
``Halted`` are re-exported so the face can map them without importing
`wicket.auth`.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from wicket.auth import AuthError, Halted, load_credentials
from wicket.domains import domain_aliases_path, load_domain_aliases
from wicket.env import (
    CREDENTIALS_FILENAME,
    DEFAULT_THREADS,
    MAIL_ROOT,
    append_log,
    normalize_account,
    resolve_account,
    resolve_archive_dir,
    resolve_duplicates_dir,
    resolve_staging_dir,
    resolve_state_dir,
    resolve_store_dir,
    store_lock,
)
from wicket.fetch.lib import RunHooks, ThreadContext, download, download_years
from wicket.filing import build_domain_query
from wicket.manifest import Row, load_store

__all__ = ["AuthError", "FetchOptions", "Halted", "fetch", "held_messages"]

# Why ``--target`` / ``FetchOptions.dest`` is refused (the fetch/ingest split).
DEST_REFUSED = (
    "fetch no longer files mail, so it takes no destination (--target, "
    "FetchOptions.dest): it gets bodies into <mail-root>/<store>/staging/, and "
    "`wicket ingest --staged --mail-account <account>` files them into the archive."
)


@dataclass(frozen=True)
class FetchOptions:
    """What to fetch, independent of which account.

    At most one of ``domains`` (list or comma-string) or ``query`` (a raw Gmail
    expression) may be set; the invariant is enforced here so a face cannot build
    an ambiguous request. Neither means every message not got and not excluded.
    ``dest`` is refused (`DEST_REFUSED`): fetch stages and files nothing, so a
    destination would be silently ignored.
    ``limit`` bounds the candidates of the whole run. ``interactive`` is
    deliberately not a field: it is a capability of the calling face, not part of
    the request.
    """

    domains: list[str] | str | None = None
    query: str | None = None
    dest: Path | None = None
    state_dir: Path | None = None
    alias_file: Path | None = None
    limit: int | None = None
    threads: int = DEFAULT_THREADS
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.domains is not None and self.query is not None:
            raise ValueError("pass at most one of domains=... or query=...")
        if self.dest is not None:
            raise ValueError(DEST_REFUSED)


def _domain_query(
    domains: list[str] | str | None,
    query: str | None,
    domain_aliases: dict[str, str],
) -> str | None:
    """Turn the options' filter into a Gmail search expression, or None for no filter.

    At most one of ``domains`` / ``query`` is set (`FetchOptions`). A raw ``query``
    passes through; ``domains`` (list or comma-string) expands via the alias map.
    Raises ``ValueError`` if a domains filter cleans to empty.
    """
    if query is not None:
        return query
    if domains is None:
        return None
    items = domains.split(",") if isinstance(domains, str) else list(domains or [])
    names = [d.strip() for d in items if d.strip()]
    if not names:
        raise ValueError("domains was empty")
    return build_domain_query(names, domain_aliases)


def _run_log(account: str) -> Callable[[str], None]:
    """Append one timestamped line to the account's log (`env.append_log`)."""

    def log(line: str) -> None:
        append_log(account, line)

    return log


def _summary(stats: dict[str, object]) -> str:
    """The run's closing log line: counts, and whether it halted (never the HALT's text)."""
    line = (
        f"run end: {stats.get('downloaded', 0)} downloaded, "
        f"{stats.get('failed', 0)} failed, {stats.get('linked', 0)} linked"
    )
    if stats.get("halted"):
        line += "; HALT"
    return line


def fetch(
    account: str | None = None,
    *,
    options: FetchOptions,
    interactive: bool = False,
) -> dict[str, object]:
    """Get ``.eml`` for matching messages into the store's staging dir.

    ``options`` (a `FetchOptions`) carries the filter and paths; at most one of its
    ``domains`` / ``query`` is set, and neither means every year until throttled.
    Resolves the account and its per-account archive (``<mail-root>/<account>/
    archive``, which reconcile checks), staging dir (``<mail-root>/<account>/
    staging``, where bodies go), duplicates dir (``<mail-root>/.duplicates/
    <account>``, counted as got), store, and credential file
    (``<state-dir>/accounts/<account>/imap.json``). Loads the
    subdomain-fold map (``<mail-root>/domain-aliases.json`` by default — ONE home,
    shared with `ingest`, and not per-account: a fold is a fact about a domain, not
    about a mailbox, and a *bucket* has no state dir to put it in), seeds
    credentials (the login email is the account reconcile checks), then runs
    `lib.download` or `lib.download_years` and returns its stats dict, which
    carries ``halted`` (and the HALT's ``halt`` line and ``halt_file``) when a
    throttle stopped it. Raises `env.StoreBusy` when another process holds the
    store's lock; a dry run takes no lock and writes no log.
    """
    resolved = resolve_account(account)
    state = resolve_state_dir(resolved, options.state_dir)
    alias_path = options.alias_file or domain_aliases_path(MAIL_ROOT)
    domain_aliases = load_domain_aliases(alias_path)
    search = _domain_query(options.domains, options.query, domain_aliases)

    credentials_path = state / CREDENTIALS_FILENAME
    imap_email, _ = load_credentials(credentials_path, interactive=interactive)

    store = resolve_store_dir(resolved)
    ctx = ThreadContext(
        dest=resolve_archive_dir(resolved),
        imap_email=normalize_account(imap_email),
        domain_aliases=domain_aliases,
        dry_run=options.dry_run,
        store_dir=store,
        staging_dir=resolve_staging_dir(resolved),
        duplicates_dir=resolve_duplicates_dir(resolved),
    )
    log: Callable[[str], None] = (
        (lambda _line: None) if options.dry_run else _run_log(resolved)
    )
    lock: AbstractContextManager[None] = (
        nullcontext() if options.dry_run else store_lock(store)
    )
    with lock:
        if search is None:
            log("run start: every year")
            stats = download_years(
                ctx=ctx,
                credentials_path=credentials_path,
                limit=options.limit,
                threads=options.threads,
                hooks=RunHooks(log=log),
            )
        else:
            log("run start: one filtered pass")
            stats = download(
                query=search,
                ctx=ctx,
                credentials_path=credentials_path,
                limit=options.limit,
                threads=options.threads,
            )
        log(_summary(stats))
    return stats


def held_messages(
    account: str | None = None, domains: set[str] | None = None
) -> Iterator[tuple[Row, Path]]:
    """Yield ``(row, eml_path)`` for every downloaded message.

    Read-only (no IMAP, no credentials). Optionally restrict to a set of filing
    ``domains`` (e.g. the airline domains a travel parser cares about). The
    caller reads and parses the ``.eml``.
    """
    resolved = resolve_account(account)
    archive = resolve_archive_dir(resolved)
    for row in load_store(resolve_store_dir(resolved)).values():
        if not row.get("downloaded"):
            continue
        if domains is not None and row.get("domain") not in domains:
            continue
        path = row.get("path")
        if path:
            yield row, archive / str(path)
