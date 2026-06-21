"""Orchestration for the fetch verb: resolve, build query, seed, download.

The single place the fetch face reaches the engine. It resolves the account and
its per-account dest/store/credential paths, builds the Gmail search expression
from ``domains`` or ``query`` (exactly one), seeds credentials (gated on
``interactive``), and dispatches into `lib.download`. The read-side helper
`held_messages` (no IMAP, no credentials) lives here too. ``AuthError`` is
re-exported so the face can map it without importing `wicket.auth`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from wicket.auth import AuthError, load_credentials
from wicket.config import (
    ALIASES_FILENAME,
    CREDENTIALS_FILENAME,
    DEFAULT_THREADS,
    normalize_account,
    resolve_account,
    resolve_archive_dir,
    resolve_state_dir,
    resolve_store_dir,
)
from wicket.domains import load_domain_aliases
from wicket.fetch.lib import ThreadContext, download
from wicket.manifest import Row, load_store
from wicket.reconcile import build_domain_query

__all__ = ["AuthError", "FetchOptions", "fetch", "held_messages"]


@dataclass(frozen=True)
class FetchOptions:
    """What to fetch and where to file it, independent of which account.

    Exactly one of ``domains`` (list or comma-string) or ``query`` (a raw Gmail
    expression) must be set; the invariant is enforced here so a face cannot
    build an ambiguous request. ``interactive`` is deliberately not a field: it
    is a capability of the calling face, not part of the request.
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
        if (self.domains is None) == (self.query is None):
            raise ValueError("pass exactly one of domains=... or query=...")


def _domain_query(
    domains: list[str] | str | None,
    query: str | None,
    domain_aliases: dict[str, str],
) -> str:
    """Turn the options' filter into a Gmail search expression.

    Exactly one of ``domains`` / ``query`` is guaranteed set by `FetchOptions`.
    A raw ``query`` passes through; ``domains`` (list or comma-string) expands
    via the alias map. Raises ``ValueError`` if a domains filter cleans to empty.
    """
    if query is not None:
        return query
    items = domains.split(",") if isinstance(domains, str) else list(domains or [])
    names = [d.strip() for d in items if d.strip()]
    if not names:
        raise ValueError("domains was empty")
    return build_domain_query(names, domain_aliases)


def fetch(
    account: str | None = None,
    *,
    options: FetchOptions,
    interactive: bool = False,
) -> dict[str, int]:
    """Download ``.eml`` for matching threads, filed by counterparty domain.

    ``options`` (a `FetchOptions`) carries the filter and paths; exactly one of
    its ``domains`` / ``query`` is set. Resolves the account and its per-account
    dest (``options.dest`` override else ``~/mail/<account>/archive``), store,
    and credential file (``<state-dir>/<account>/imap.json``). Loads the alias
    file (``<state-dir>/<account>/domain-aliases.json`` by default), seeds
    credentials (the login email is the "me" identity for filing), then runs
    `lib.download` and returns its stats dict.
    """
    resolved = resolve_account(account)
    state = resolve_state_dir(resolved, options.state_dir)
    alias_path = options.alias_file
    if alias_path is None:
        alias_path = state / ALIASES_FILENAME
    domain_aliases = load_domain_aliases(alias_path)
    search = _domain_query(options.domains, options.query, domain_aliases)

    credentials_path = state / CREDENTIALS_FILENAME
    imap_email, _ = load_credentials(credentials_path, interactive=interactive)

    archive = options.dest
    if archive is None:
        archive = resolve_archive_dir(resolved)
    ctx = ThreadContext(
        dest=archive,
        imap_email=normalize_account(imap_email),
        domain_aliases=domain_aliases,
        dry_run=options.dry_run,
        store_dir=resolve_store_dir(resolved),
    )
    return download(
        query=search,
        ctx=ctx,
        credentials_path=credentials_path,
        limit=options.limit,
        threads=options.threads,
    )


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
