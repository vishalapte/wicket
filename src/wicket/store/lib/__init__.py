"""The store worker: the computation, with no knowledge of who calls it.

No argument parsing, no printing, no default chosen on a caller's behalf. ``api`` imports
from here and reaches no deeper; the renderers under ``lib/renderer/`` import only
``lib.results``.
"""

from __future__ import annotations

from wicket.maps import Map

from .results import StoreRow


def row(store: str, routes: Map, accounts: Map) -> StoreRow:
    """One store's sender domains (domain-routes.json) and your addresses (account-aliases.json)."""
    return {
        "store": store,
        "domains": sorted(routes.get(store, [])),
        "addresses": sorted(accounts.get(store, [])),
    }


def rows(routes: Map, accounts: Map) -> list[StoreRow]:
    """Every store named in either map, sorted."""
    return [
        row(store, routes, accounts) for store in sorted(set(routes) | set(accounts))
    ]
