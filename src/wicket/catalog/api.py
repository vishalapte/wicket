"""Orchestration for the catalog verb: resolve, seed credentials, sweep.

The single place the catalog face reaches the engine. It resolves the account,
the per-account store and credential paths, seeds credentials (gated on
``interactive``), dispatches into `lib.sweep`, and returns the worker's
stats dict unchanged. ``AuthError`` is re-exported so the face can map it
without importing `wicket.auth`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from wicket.auth import AuthError, load_credentials
from wicket.catalog.lib import sweep
from wicket.env import (
    ALL_MAIL_MAILBOX,
    CREDENTIALS_FILENAME,
    DEFAULT_THREADS,
    resolve_account,
    resolve_state_dir,
    resolve_store_dir,
)
from wicket.manifest import latest_shard_year

__all__ = ["AuthError", "CatalogOptions", "catalog"]


@dataclass(frozen=True)
class CatalogOptions:
    """How to run a catalog sweep, independent of which account.

    A value object the face builds from its input (argv, JSON, a form) and hands
    to `catalog`. With neither ``years`` nor ``full``, the sweep is incremental
    (from the latest year already in the manifest through now); ``full`` forces a
    complete re-sweep from the oldest message. ``interactive`` is deliberately not
    a field: it is an ambient capability of the calling face (is a tty present),
    not part of the request.
    """

    store_dir: Path | None = None
    state_dir: Path | None = None
    mailbox: str = ALL_MAIL_MAILBOX
    years: list[int] | None = None
    dry_run: bool = False
    threads: int = DEFAULT_THREADS
    full: bool = False


def _sweep_years(store_dir: Path, options: CatalogOptions) -> list[int] | None:
    """Decide which years to sweep (the incremental-vs-full policy).

    Explicit ``options.years`` win. ``options.full`` (or a first run with an
    empty store) returns ``None``, letting `lib.sweep` derive the full range from
    the oldest message in the mailbox. Otherwise sweep incrementally: from the
    latest year already in the manifest through the current UTC year. The past
    does not change, so older shards are left untouched and no mailbox scan is
    needed to find the start.
    """
    if options.years is not None:
        return options.years
    if options.full:
        return None
    latest = latest_shard_year(store_dir)
    if latest is None:
        return None
    return list(range(latest, datetime.now(timezone.utc).year + 1))


def catalog(
    account: str | None = None,
    *,
    options: CatalogOptions | None = None,
    interactive: bool = False,
) -> dict[str, int]:
    """Sweep ``account``'s mailbox headers into the year-sharded manifest.

    ``options`` (a `CatalogOptions`) carries the run knobs. Resolves the account
    (`env.resolve_account`), the manifest store (``options.store_dir``
    override else ``<mail-root>/<account>/manifest``), and the credential file
    (``<state-dir>/<account>/imap.json``). Seeds credentials in this thread (the
    per-year workers never prompt). Sweeps incrementally by default (from the
    latest year in the manifest forward), fully when ``options.full`` is set, or
    the explicit ``options.years`` when given; returns `lib.sweep`'s stats dict.
    ``interactive=False`` is the default: a missing credential raises `AuthError`
    instead of blocking on a prompt.
    """
    opts = options if options is not None else CatalogOptions()
    resolved = resolve_account(account)
    store = opts.store_dir
    if store is None:
        store = resolve_store_dir(resolved)
    credentials_path = (
        resolve_state_dir(resolved, opts.state_dir) / CREDENTIALS_FILENAME
    )
    load_credentials(credentials_path, interactive=interactive)
    return sweep(
        store_dir=store,
        credentials_path=credentials_path,
        mailbox=opts.mailbox,
        years=_sweep_years(store, opts),
        dry_run=opts.dry_run,
        threads=opts.threads,
    )
