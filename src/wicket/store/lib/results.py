"""The shapes the store api returns: one TypedDict per verb, and its rows.

The one home of every store result shape. The api returns them, the renderers read
them, and ``output()`` publishes them as JSON Schema; nothing restates them. They live in
``lib`` so the api and the renderers can both import them while nothing in ``lib``
imports the api. A result holds JSON types only: a date is an ISO string, an exact
amount a decimal string, never a float.
"""

from __future__ import annotations

from typing import TypedDict


class StoreRow(TypedDict):
    """One store and what sends mail to it: sender domains (`--from`), your addresses (`--to`)."""

    store: str
    domains: list[str]
    addresses: list[str]


class CreateStore(TypedDict):
    """What ``store create`` returns: the store's entries after the addition."""

    store: str
    domains: list[str]
    addresses: list[str]


class DeleteStore(TypedDict):
    """What ``store delete`` returns: the store's entries left after the removal."""

    store: str
    domains: list[str]
    addresses: list[str]


class ListStore(TypedDict):
    """What ``store list`` returns: every store named in either file."""

    stores: list[StoreRow]
