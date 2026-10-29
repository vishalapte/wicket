"""The alias views as terminal text.

Every line this noun's cli prints is written here. Each ``print_<verb>`` takes that verb's
result and writes it; it reads no store and decides nothing, and it reads the same types
the api returns, so what it prints cannot drift from them.
"""

from __future__ import annotations

import sys

from ..results import CreateAlias, DeleteAlias, ListAlias, UpdateAlias


def _entry(primary: str, domains: list[str]) -> None:
    print(f"{primary}: {', '.join(domains) if domains else '(none)'}")


def print_create(result: CreateAlias) -> None:
    """``alias create`` as text: the entry it added."""
    _entry(result["primary"], result["domains"])


def print_delete(result: DeleteAlias) -> None:
    """``alias delete`` as text: what is left under the primary."""
    _entry(result["primary"], result["domains"])


def print_list(result: ListAlias) -> None:
    """``alias list`` as text: one ``primary: domains`` line per entry."""
    for row in result["aliases"]:
        _entry(row["primary"], row["domains"])


def print_update(result: UpdateAlias) -> None:
    """``alias update`` as text: the entry after the change."""
    _entry(result["primary"], result["domains"])


def error(message: str) -> None:
    """A refusal the api raised, on stderr, so stdout stays empty."""
    print(message, file=sys.stderr)
