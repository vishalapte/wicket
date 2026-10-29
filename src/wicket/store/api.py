"""The store api: the one place a request becomes a decision.

Every surface (this noun's cli, an MCP tool, a REST route) calls these functions and
nothing below them. Each verb is one function returning one result type from
``lib.results``, plain JSON. A request the api cannot run raises ``ApiError``. A verb
``racecar.surface`` adds arrives as a stub: its signature is the params the lexicon
declares for it, and what it computes, and the fields its result carries, are the
author's.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import wicket.env as env  # pylint: disable=consider-using-from-import
from wicket.domains import domain_routes_path, parse_domain_routes
from wicket.env import account_aliases_path, parse_account_aliases
from wicket.errors import ApiError
from wicket.maps import Map, add_items, read_map, remove_items, write_maps

from .lib import row, rows
from .lib.results import CreateStore, DeleteStore, ListStore

# `import wicket.env as env`: `env.require_mail_root` and `env.MAIL_ROOT` are read when
# called, so a test's `monkeypatch.setattr(env, "MAIL_ROOT", ...)` reaches them.

Edit = Callable[[Map, str, list[str]], Map]


def _paths() -> tuple[Path, Path]:
    """(domain-routes.json, account-aliases.json) under a mail root that exists."""
    try:
        root = env.require_mail_root()
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return domain_routes_path(root), account_aliases_path(root)


def _read(path: Path) -> Map:
    try:
        return read_map(path)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _change(
    store: str, from_: list[str] | None, to: list[str] | None, edit: Edit
) -> tuple[Map, Map]:
    """Apply ``edit`` to each file a flag names, check both, then write both or neither.

    `--from` edits domain-routes.json and `--to` account-aliases.json. Each result is
    checked with its reader's own rule, so a domain or address already sent to another
    store, a bad domain or a bad store refuses the whole command before any file is
    written. Naming neither flag refuses too.
    """
    if not from_ and not to:
        raise ApiError("name at least one --from domain or --to address")
    routes_path, accounts_path = _paths()
    routes, accounts = _read(routes_path), _read(accounts_path)
    changes: dict[Path, Map] = {}
    try:
        if from_:
            routes = edit(routes, store, from_)
            parse_domain_routes(routes, routes_path)
            changes[routes_path] = routes
        if to:
            accounts = edit(accounts, store, to)
            parse_account_aliases(accounts, accounts_path)
            changes[accounts_path] = accounts
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    write_maps(changes)
    return routes, accounts


def create_store(
    store: str, from_: list[str] | None = None, to: list[str] | None = None
) -> CreateStore:
    """Add sender domains and your addresses to a store."""
    routes, accounts = _change(store, from_, to, add_items)
    result = row(store, routes, accounts)
    return {
        "store": store,
        "domains": result["domains"],
        "addresses": result["addresses"],
    }


def delete_store(
    store: str, from_: list[str] | None = None, to: list[str] | None = None
) -> DeleteStore:
    """Remove the named domains and addresses from a store; refuses when none are named."""
    routes, accounts = _change(store, from_, to, remove_items)
    result = row(store, routes, accounts)
    return {
        "store": store,
        "domains": result["domains"],
        "addresses": result["addresses"],
    }


def list_store() -> ListStore:
    """List every store with its sender domains and owner addresses."""
    routes_path, accounts_path = _paths()
    return {"stores": rows(_read(routes_path), _read(accounts_path))}


# CLI spelling -> the function it binds. A verb spelled like a keyword or a builtin
# (`import`, `list`) is a key here rather than a function name; the lexicon reads this
# literal to find each verb.
VERBS: dict[str, Callable[..., object]] = {
    "create": create_store,
    "delete": delete_store,
    "list": list_store,
}
