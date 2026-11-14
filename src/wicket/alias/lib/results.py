"""The shapes the alias api returns: one TypedDict per verb, and its rows.

The one home of every alias result shape. The api returns them, the renderers read
them, and ``output()`` publishes them as JSON Schema; nothing restates them. They live in
``lib`` so the api and the renderers can both import them while nothing in ``lib``
imports the api. A result holds JSON types only: a date is an ISO string, an exact
amount a decimal string, never a float.
"""

from __future__ import annotations

from typing import TypedDict


class AliasRow(TypedDict):
    """One entry: a primary domain and the domains that fold into it."""

    primary: str
    domains: list[str]


class CreateAlias(AliasRow):
    """What ``alias create`` returns: the entry it added."""


class DeleteAlias(AliasRow):
    """What ``alias delete`` returns: what is left under the primary (empty when it is gone)."""


class ListAlias(TypedDict):
    """What ``alias list`` returns: every entry, by primary."""

    aliases: list[AliasRow]


class UpdateAlias(AliasRow):
    """What ``alias update`` returns: the entry after the change."""
