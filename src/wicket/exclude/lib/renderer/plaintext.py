"""The exclude views as terminal text.

Every line this noun's cli prints is written here. Each ``print_<verb>`` takes that verb's
result and writes it; it reads no store and decides nothing, and it reads the same types
the api returns, so what it prints cannot drift from them.
"""

from __future__ import annotations

import sys

from ..results import CreateExclude, DeleteExclude, ListExclude


def _entry(account: str, lists: list[str]) -> None:
    print(f"{account}: {', '.join(lists) if lists else '(none)'}")


def print_create(result: CreateExclude) -> None:
    """``exclude create`` as text: the entry after the list was added."""
    _entry(result["account"], result["lists"])


def print_delete(result: DeleteExclude) -> None:
    """``exclude delete`` as text: what is left in the entry."""
    _entry(result["account"], result["lists"])


def print_list(result: ListExclude) -> None:
    """``exclude list`` as text: one ``account: lists`` line per entry, ``*`` first."""
    for row in result["excluded"]:
        _entry(row["account"], row["lists"])


def error(message: str) -> None:
    """A refusal the api raised, on stderr, so stdout stays empty."""
    print(message, file=sys.stderr)
