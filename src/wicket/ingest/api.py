"""Orchestration for the ingest verb: resolve paths, dispatch to the worker.

The single place the ingest face reaches the engine. Read-only on any mailbox:
ingest reads ``.eml`` already on disk and files them additively into the manifest
+ archive, so there is no IMAP and no credentials to seed (like `report.api`).

Two modes, and the default is the safe one:

- **Routed** (no account named): each *thread* goes to the account it was
  addressed to, resolved through the account-alias map and the stores that
  actually exist. Mail nobody claims is left alone. This is the mode that makes a
  mixed folder safe, because no single wrong destination exists to choose.
- **Single-target** (an explicit account): everything lands in that one store.
  ``--mail-account`` is a destination, not a filter, so the worker refuses when the
  folder is plainly addressed to a different account (``force`` overrides).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wicket.domains import (
    DOMAIN_RE,
    domain_aliases_path,
    domain_routes_path,
    load_domain_aliases,
    load_domain_routes,
    route_of,
)
from wicket.env import (
    MAIL_ROOT,
    account_of,
    identities,
    known_accounts,
    load_account_aliases,
    resolve_account,
    resolve_archive_dir,
    resolve_store_dir,
)
from wicket.ingest.lib import AccountFor, Target, ingest_folder, ingest_routed

__all__ = ["IngestOptions", "ingest"]


@dataclass(frozen=True)
class IngestOptions:
    """How to run an ingest, independent of which account.

    ``src`` is a flat folder of ``.eml``, or a single ``.eml`` (one message is a
    folder of one; the worker treats them alike). ``source`` names the export
    profile ("local" is the only one today: an Outlook / Apple Mail drag-export). With
    ``dry_run`` the worker parses and reports what it would do but writes nothing
    and trashes nothing. ``force`` overrides the account-mismatch refusal (single-
    target only). ``to_trash`` moves the source files of messages that are durably
    archived to the Trash; an unrouted message always keeps its file.

    ``domain`` (the ``--domain`` flag) forces the filing domain for every
    newly-added message, standing in for the computed counterparty; a bare domain
    like ``acme.com``, validated in `ingest`. ``tags`` (the ``--tag`` flag) are
    recorded as each new row's ``labels``. Both touch only mail filed this run.
    """

    src: Path
    source: str = "local"
    dry_run: bool = False
    force: bool = False
    to_trash: bool = False
    domain: str | None = None
    tags: tuple[str, ...] = ()


def _resolver() -> tuple[AccountFor, set[str]]:
    """Bind `env.account_of` to the alias map and the stores that exist."""
    aliases = load_account_aliases()
    accounts = known_accounts()
    return (lambda address: account_of(address, aliases, accounts)), accounts


def _router(accounts: set[str]) -> AccountFor:
    """Bind `domains.route_of` to the counterparty map, filtered to real stores.

    A route to a bucket you never created resolves to nothing, so the thread
    stays unrouted instead of conjuring a directory.
    """
    routes = load_domain_routes(domain_routes_path(MAIL_ROOT))

    def route(address: str) -> str | None:
        destination = route_of(address, routes)
        return destination if destination in accounts else None

    return route


def _target(account: str, accounts: set[str], options: IngestOptions) -> Target:
    return Target(
        store_dir=resolve_store_dir(account),
        archive_dir=resolve_archive_dir(account),
        account=account,
        identities=identities(load_account_aliases(), accounts),
        domain_aliases=load_domain_aliases(domain_aliases_path(MAIL_ROOT)),
        filing_domain=options.domain,
        labels=list(options.tags) or None,
    )


def ingest(account: str | None = None, *, options: IngestOptions) -> dict[str, object]:
    """File the new ``.eml`` under ``options.src``, routed or into one account.

    Raises ``ValueError`` when ``options.domain`` is not a bare domain: it becomes
    an archive path segment, so a malformed one (or a path-traversal attempt) is
    refused here, at the boundary that accepts owner input, before the worker.
    """
    if options.domain is not None and not DOMAIN_RE.match(options.domain):
        raise ValueError(
            f"invalid --domain {options.domain!r}: expected a bare domain like acme.com"
        )
    account_for, accounts = _resolver()

    if account is None:
        return ingest_routed(
            src=options.src,
            targets={a: _target(a, accounts, options) for a in sorted(accounts)},
            account_for=account_for,
            route_for=_router(accounts),
            dry_run=options.dry_run,
            to_trash=options.to_trash,
        )

    resolved = resolve_account(account)
    return ingest_folder(
        src=options.src,
        target=_target(resolved, accounts, options),
        account_for=account_for,
        dry_run=options.dry_run,
        force=options.force,
        to_trash=options.to_trash,
    )
