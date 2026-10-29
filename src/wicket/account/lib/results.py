"""The shapes the account api returns: one TypedDict per verb.

The one home of every account result shape. The api returns them, the renderers read
them, and ``output()`` publishes them as JSON Schema; nothing restates them. A result
holds JSON types only.
"""

from __future__ import annotations

from typing import TypedDict


class ListAccount(TypedDict):
    """What ``account list`` returns: each ``[name, imap.json target]``, sorted.

    The target is what the ``imap.json`` link points to, or a real file's own path.
    """

    accounts: list[list[str]]


class CreateAccount(TypedDict):
    """What ``account create`` returns: the account, its ``imap.json`` link's target, and
    whether this call made it."""

    name: str
    config: str
    changed: bool


class DeleteAccount(TypedDict):
    """What ``account delete`` returns: the name, and whether ``imap.json`` was taken out
    (a link removed, or a real file renamed)."""

    name: str
    changed: bool
