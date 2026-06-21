"""Orchestration for the report verb: resolve the account, summarize the store.

The single place the report face reaches the engine. Read-only: no IMAP, no
credentials. Each function resolves the account (`config.resolve_account`),
locates its manifest store, and returns plain data the face renders. ``manifest``
is the whole-store read for library consumers.
"""

from __future__ import annotations

from wicket.config import resolve_account, resolve_store_dir
from wicket.manifest import Row, load_store
from wicket.report.lib import all_addresses, sender_counts, summary

__all__ = ["addresses", "manifest", "report", "senders"]


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
    """The whole manifest for ``account`` (default: the sole ``~/mail`` account)."""
    return load_store(resolve_store_dir(resolve_account(account)))
