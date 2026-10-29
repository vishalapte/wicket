"""The shapes the exclude api returns: one TypedDict per verb, and its rows.

The one home of every exclude result shape. The api returns them, the renderers read
them, and ``output()`` publishes them as JSON Schema; nothing restates them. They live in
``lib`` so the api and the renderers can both import them while nothing in ``lib``
imports the api. A result holds JSON types only: a date is an ISO string, an exact
amount a decimal string, never a float.
"""

from __future__ import annotations

from typing import TypedDict


class ExcludeRow(TypedDict):
    """One entry: ``*`` (every account) or an account, and the lists it never fetches."""

    account: str
    lists: list[str]


class CreateExclude(TypedDict):
    """What ``exclude create`` returns: the entry after the list was added."""

    account: str
    lists: list[str]


class DeleteExclude(TypedDict):
    """What ``exclude delete`` returns: what is left in the entry (empty when it is gone)."""

    account: str
    lists: list[str]


class ListExclude(TypedDict):
    """What ``exclude list`` returns: every entry, ``*`` first, then by account."""

    excluded: list[ExcludeRow]
