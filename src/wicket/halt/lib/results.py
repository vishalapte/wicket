"""The shapes the halt api returns: one TypedDict per verb, and its rows.

The one home of every halt result shape. The api returns them, the renderers read
them, and ``output()`` publishes them as JSON Schema; nothing restates them. They live in
``lib`` so the api and the renderers can both import them while nothing in ``lib``
imports the api. A result holds JSON types only: a date is an ISO string, an exact
amount a decimal string, never a float.
"""

from __future__ import annotations

from typing import TypedDict


class HaltRow(TypedDict):
    """One HALT in force: the account, when it was set (ISO, local), and its age."""

    account: str
    halted: str
    age_seconds: int


class SetRow(TypedDict):
    """One account ``halt set`` reached: the HALT in force, and whether this call set it.

    ``changed`` is false when a HALT was already in force: the first cause wins, so it
    is kept and ``halted`` is when that HALT was set.
    """

    account: str
    halted: str
    changed: bool


class ClearRow(TypedDict):
    """One HALT ``halt clear`` removed: its account, and when it had been set."""

    account: str
    halted: str


class ListHalt(TypedDict):
    """What ``halt list`` returns: every halted account, sorted."""

    halts: list[HaltRow]


class SetHalt(TypedDict):
    """What ``halt set`` returns: one row per account, in account order."""

    halts: list[SetRow]


class ClearHalt(TypedDict):
    """What ``halt clear`` returns: the HALTs removed, and each named account not halted."""

    cleared: list[ClearRow]
    not_halted: list[str]
