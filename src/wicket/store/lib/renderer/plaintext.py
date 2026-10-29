"""The store views as terminal text.

Every line this noun's cli prints is written here. Each ``print_<verb>`` takes that verb's
result and writes it; it reads no store and decides nothing, and it reads the same types
the api returns, so what it prints cannot drift from them.
"""

from __future__ import annotations

import sys

from ..results import CreateStore, DeleteStore, ListStore


def _store(store: str, domains: list[str], addresses: list[str]) -> None:
    print(store)
    print(f"  from: {', '.join(domains) if domains else '(none)'}")
    print(f"  to:   {', '.join(addresses) if addresses else '(none)'}")


def print_create(result: CreateStore) -> None:
    """``store create`` as text: the store's entries after the addition."""
    _store(result["store"], result["domains"], result["addresses"])


def print_delete(result: DeleteStore) -> None:
    """``store delete`` as text: the store's entries left after the removal."""
    _store(result["store"], result["domains"], result["addresses"])


def print_list(result: ListStore) -> None:
    """``store list`` as text: each store, its sender domains and your addresses."""
    for entry in result["stores"]:
        _store(entry["store"], entry["domains"], entry["addresses"])


def error(message: str) -> None:
    """A refusal the api raised, on stderr, so stdout stays empty."""
    print(message, file=sys.stderr)
