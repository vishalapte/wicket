"""The alias api: the one place a request becomes a decision.

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
from wicket.domains import domain_aliases_path, parse_domain_aliases
from wicket.errors import ApiError
from wicket.maps import (
    Map,
    create_entry,
    read_map,
    remove_items,
    update_entry,
    write_maps,
)

from .lib import rows
from .lib.results import CreateAlias, DeleteAlias, ListAlias, UpdateAlias

# `import wicket.env as env`: `env.require_mail_root` reads `env.MAIL_ROOT` when called, so
# a test's `monkeypatch.setattr(env, "MAIL_ROOT", ...)` reaches it.


def _path() -> Path:
    """domain-aliases.json under a mail root that exists (a locked vault refuses)."""
    try:
        return domain_aliases_path(env.require_mail_root())
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _read(path: Path) -> Map:
    try:
        return read_map(path)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _commit(path: Path, before: Map, edit: Callable[[Map], Map]) -> Map:
    """Apply ``edit`` in memory, check the result as the reader would, then write it."""
    try:
        after = edit(before)
        parse_domain_aliases(after, path)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    write_maps({path: after})
    return after


def create_alias(primary: str, item: list[str] | None = None) -> CreateAlias:
    """Add a primary domain and the domains that fold into it."""
    path = _path()
    after = _commit(path, _read(path), lambda m: create_entry(m, primary, item or []))
    return {"primary": primary, "domains": after[primary]}


def delete_alias(primary: str, item: list[str] | None = None) -> DeleteAlias:
    """Delete a primary, or the named domains under it."""
    path = _path()
    after = _commit(path, _read(path), lambda m: remove_items(m, primary, item))
    return {"primary": primary, "domains": after.get(primary, [])}


def list_alias() -> ListAlias:
    """List every alias entry."""
    return {"aliases": rows(_read(_path()))}


def update_alias(
    primary: str, add: list[str] | None = None, remove: list[str] | None = None
) -> UpdateAlias:
    """Add or remove domains under an existing primary."""
    path = _path()
    after = _commit(
        path, _read(path), lambda m: update_entry(m, primary, add or [], remove or [])
    )
    return {"primary": primary, "domains": after[primary]}


# CLI spelling -> the function it binds. A verb spelled like a keyword or a builtin
# (`import`, `list`) is a key here rather than a function name; the lexicon reads this
# literal to find each verb.
VERBS: dict[str, Callable[..., object]] = {
    "create": create_alias,
    "delete": delete_alias,
    "list": list_alias,
    "update": update_alias,
}
