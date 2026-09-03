"""Orchestration for the report verb: resolve the account, summarize the store.

The single place the report face reaches the engine. Read-only: no IMAP, no
credentials. Each function resolves the account (`env.resolve_account`),
locates its manifest store, and returns plain data the face renders. ``manifest``
is the whole-store read for library consumers.
"""

from __future__ import annotations

from wicket.domains import (
    domain_aliases_path,
    domain_routes_path,
    load_domain_aliases,
    load_domain_routes,
)
from wicket.env import (
    MAIL_ROOT,
    known_accounts,
    resolve_account,
    resolve_store_dir,
)
from wicket.manifest import Row, load_store
from wicket.report.lib import all_addresses, bucket_rows, sender_counts, summary

__all__ = ["addresses", "bucket", "manifest", "report", "senders"]


def report(account: str | None = None) -> dict[str, int]:
    """One-screen counts for ``account``'s manifest (messages, held, addresses)."""
    return summary(resolve_store_dir(resolve_account(account)))


def senders(account: str | None = None) -> list[tuple[str, int]]:
    """``(sender, count)`` for every From address, descending by count."""
    return sender_counts(resolve_store_dir(resolve_account(account)))


def addresses(account: str | None = None) -> list[str]:
    """Every distinct address seen in From or To, sorted."""
    return all_addresses(resolve_store_dir(resolve_account(account)))


def manifest(account: str | None = None) -> dict[str, Row]:
    """The whole manifest for ``account`` (default: the sole account under the mail root)."""
    return load_store(resolve_store_dir(resolve_account(account)))


def bucket(name: str) -> dict[str, list[tuple[str, Row]]]:
    """Every message any store holds that ``name`` claims, keyed by store.

    Reads across *all* accounts, not one: a bucket is a topical view over mail
    that physically lives wherever its mailbox put it (`domains.load_domain_routes`
    decides membership; `domains.load_domain_aliases` folds the domain first).
    """
    routes = load_domain_routes(domain_routes_path(MAIL_ROOT))
    aliases = load_domain_aliases(domain_aliases_path(MAIL_ROOT))
    found: dict[str, list[tuple[str, Row]]] = {}
    for account in sorted(known_accounts()):
        rows = bucket_rows(resolve_store_dir(account), name, routes, aliases)
        if rows:
            found[account] = rows
    return found
