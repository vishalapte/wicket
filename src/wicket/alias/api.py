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
from wicket.errors import refusing
from wicket.maps import create_entry, edit, read_map, remove_items, update_entry

from .lib import rows
from .lib.results import CreateAlias, DeleteAlias, ListAlias, UpdateAlias

# `import wicket.env as env`: `env.require_mail_root` reads `env.MAIL_ROOT` when called, so
# a test's `monkeypatch.setattr(env, "MAIL_ROOT", ...)` reaches it.


def _path() -> Path:
    """domain-aliases.json under a mail root that exists (a locked vault refuses)."""
    return domain_aliases_path(env.require_mail_root())


def create_alias(primary: str, item: list[str] | None = None) -> CreateAlias:
    """Add a primary domain and the domains that fold into it."""
    with refusing():
        after = edit(
            _path(),
            lambda m: create_entry(m, primary, item or []),
            parse_domain_aliases,
        )
    return {"primary": primary, "domains": after[primary]}


def delete_alias(primary: str, item: list[str] | None = None) -> DeleteAlias:
    """Delete a primary, or the named domains under it."""
    with refusing():
        after = edit(
            _path(), lambda m: remove_items(m, primary, item), parse_domain_aliases
        )
    return {"primary": primary, "domains": after.get(primary, [])}


def list_alias() -> ListAlias:
    """List every alias entry."""
    with refusing():
        mapping = read_map(_path())
    return {"aliases": rows(mapping)}


def update_alias(
    primary: str, add: list[str] | None = None, remove: list[str] | None = None
) -> UpdateAlias:
    """Add or remove domains under an existing primary."""
    with refusing():
        after = edit(
            _path(),
            lambda m: update_entry(m, primary, add or [], remove or []),
            parse_domain_aliases,
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
