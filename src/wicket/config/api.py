"""Orchestration for the config verb: resolve which map, validate, dispatch to lib.

The single place the config face reaches the engine. No IMAP, no credentials.
Each function resolves the mail root (`wicket.env.require_mail_root` — an absent
root is a locked vault, never "empty", the same invariant every other verb
enforces), resolves the resource's path, validates the primary/items against
that resource's own rules, and calls the generic engine in `wicket.config.lib`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import wicket.env as env  # pylint: disable=consider-using-from-import

# `import wicket.env as env`, not `from wicket import env`: the latter is a
# forbidden upward import (check_upward_imports.py) here in a business module;
# `env.MAIL_ROOT` below needs the live module object, not a name snapshotted
# at import time, so a test's `monkeypatch.setattr(env, "MAIL_ROOT", ...)`
# actually reaches it.
from wicket.config.lib import (
    Map,
    create_entry,
    delete_entry,
    list_entries,
    update_entry,
)
from wicket.domains import (
    DOMAIN_RE,
    WILDCARD_RE,
    domain_aliases_path,
    domain_routes_path,
)
from wicket.env import (
    ADDRESS_RE,
    BUCKET_RE,
    WILDCARD_ADDRESS_RE,
    account_aliases_path,
    is_destination,
    require_mail_root,
)

__all__ = [
    "RESOURCES",
    "list_map",
    "create",
    "update",
    "delete",
]


@dataclass(frozen=True)
class Resource:
    """One of the three owner-authored maps: its path, and what a valid row is."""

    path: Callable[[], Path]
    primary_ok: Callable[[str], bool]
    item_ok: Callable[[str], bool]
    noun: str  # "address" or "domain" -- names the item kind in error messages


# The three real resources, keyed exactly as the CLI addresses them
# (`wicket config <account|domain> <aliases|routes>`). Validators reuse the
# SAME regexes `wicket.env`/`wicket.domains` validate reads with, so a row this
# tool writes is guaranteed to be one `load_account_aliases` etc. can read back.
RESOURCES: dict[str, Resource] = {
    "account-aliases": Resource(
        path=account_aliases_path,
        primary_ok=lambda p: bool(ADDRESS_RE.match(p) or BUCKET_RE.match(p)),
        item_ok=lambda a: bool(ADDRESS_RE.match(a) or WILDCARD_ADDRESS_RE.match(a)),
        noun="address",
    ),
    "domain-aliases": Resource(
        # env.MAIL_ROOT, not an imported MAIL_ROOT name: a `from wicket.env import
        # MAIL_ROOT` would snapshot the value at import time, so a test's
        # `monkeypatch.setattr(env, "MAIL_ROOT", ...)` would never reach this lambda.
        path=lambda: domain_aliases_path(env.MAIL_ROOT),
        primary_ok=lambda p: bool(DOMAIN_RE.match(p)),
        item_ok=lambda a: bool(DOMAIN_RE.match(a) or WILDCARD_RE.match(a)),
        noun="domain",
    ),
    "domain-routes": Resource(
        path=lambda: domain_routes_path(env.MAIL_ROOT),
        primary_ok=is_destination,
        item_ok=lambda a: bool(DOMAIN_RE.match(a) or WILDCARD_RE.match(a)),
        noun="domain",
    ),
}


def _resource(name: str) -> Resource:
    try:
        return RESOURCES[name]
    except KeyError:
        raise ValueError(
            f"unknown config resource {name!r}: must be one of " f"{sorted(RESOURCES)}"
        ) from None


def _validate_items(resource: Resource, items: list[str]) -> None:
    for item in items:
        if not resource.item_ok(item):
            raise ValueError(f"invalid {resource.noun} {item!r}")


def list_map(name: str) -> Map:
    """Every entry in resource ``name``, or ``{}`` when the file does not exist."""
    resource = _resource(name)
    require_mail_root()
    return list_entries(resource.path())


def create(name: str, primary: str, items: list[str]) -> Map:
    """Add a new ``primary -> items`` entry to resource ``name``."""
    resource = _resource(name)
    require_mail_root()
    if not resource.primary_ok(primary):
        raise ValueError(f"invalid primary {resource.noun} {primary!r}")
    _validate_items(resource, items)
    return create_entry(resource.path(), primary, items)


def update(name: str, primary: str, add: list[str], remove: list[str]) -> Map:
    """Add/remove items under an existing ``primary`` in resource ``name``."""
    resource = _resource(name)
    require_mail_root()
    _validate_items(resource, add)
    return update_entry(resource.path(), primary, add, remove)


def delete(name: str, primary: str, item: str | None = None) -> Map:
    """Delete ``item`` under ``primary`` (or the whole ``primary`` when ``item`` is None)."""
    resource = _resource(name)
    require_mail_root()
    return delete_entry(resource.path(), primary, item)
