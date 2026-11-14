"""The exclude api: the one place a request becomes a decision.

Every surface (this noun's cli, an MCP tool, a REST route) calls these functions and
nothing below them. Each verb is one function returning one result type from
``lib.results``, plain JSON. A request the api cannot run raises ``ApiError``.

It edits ``excluded-lists.json`` at the mail root: no ``mail_account`` is ``"*"``, every
account (owner, 2026-10-29); an account is a folder holding ``imap.json`` in
``~/.config/wicket/accounts``. Each edit is checked with the reader's own rule
(`exclusions.parse_excluded_lists`) before it is written, so a file this writes is one
``fetch`` and ``ingest`` accept.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import wicket.env as env  # pylint: disable=consider-using-from-import
from wicket.errors import refusing
from wicket.exclusions import (
    EVERY_ACCOUNT,
    excluded_lists_path,
    normalize_list_id,
    parse_excluded_lists,
)
from wicket.maps import Map, add_items, edit, read_map, remove_items

from .lib import rows
from .lib.results import CreateExclude, DeleteExclude, ListExclude

# `import wicket.env as env`: `env.require_mail_root` reads `env.MAIL_ROOT` when called, so
# a test's `monkeypatch.setattr(env, "MAIL_ROOT", ...)` reaches it.


def _path() -> Path:
    """excluded-lists.json under a mail root that exists (a locked vault refuses)."""
    return excluded_lists_path(env.require_mail_root())


def _key(mail_account: str | None) -> str:
    """The map key a request names: ``"*"`` with no account, else the account."""
    return (
        EVERY_ACCOUNT if mail_account is None else env.normalize_account(mail_account)
    )


def _check(mapping: Map, path: Path) -> None:
    """The reader's rule, against the accounts that exist now."""
    parse_excluded_lists(mapping, path, [p.name for p in env.account_dirs()])


def create_exclude(list_: str, mail_account: str | None = None) -> CreateExclude:
    """Exclude a mailing list, for every account, or for --mail-account alone."""
    key, item = _key(mail_account), normalize_list_id(list_)
    with refusing():
        after = edit(_path(), lambda m: add_items(m, key, [item]), _check)
    return {"account": key, "lists": after[key]}


def delete_exclude(list_: str, mail_account: str | None = None) -> DeleteExclude:
    """Stop excluding a mailing list, for every account, or for --mail-account alone."""
    key, item = _key(mail_account), normalize_list_id(list_)
    with refusing():
        after = edit(_path(), lambda m: remove_items(m, key, [item]), _check)
    return {"account": key, "lists": after.get(key, [])}


def list_exclude() -> ListExclude:
    """List every excluded mailing list, by account (* is every account)."""
    with refusing():
        mapping = read_map(_path())
    return {"excluded": rows(mapping)}


# CLI spelling -> the function it binds. A verb spelled like a keyword or a builtin
# (`import`, `list`) is a key here rather than a function name; the lexicon reads this
# literal to find each verb.
VERBS: dict[str, Callable[..., object]] = {
    "create": create_exclude,
    "delete": delete_exclude,
    "list": list_exclude,
}
