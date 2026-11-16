"""The account api: the one place a request becomes a decision.

Every surface calls these functions and nothing below them. Each verb is one function
returning one result type from ``lib.results``, plain JSON. A request the api cannot run
raises ``ApiError`` (`LinkRefused`, which carries the error packet's code). No verb
opens an IMAP connection or reads the mail root.

An account is a folder ``~/.config/wicket/accounts/<name>/`` (``env.account_dirs``)
holding ``imap.json``, a link to the provider's file (``~/.config/gmail/<name>/imap.json``;
a Fastmail one, made by hand, links into ``~/.config/fastmail/<login>/``). Nothing else is
stored, so a folder and link made by hand work the same.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import wicket.env as env  # pylint: disable=consider-using-from-import
from wicket.env import CREDENTIALS_FILENAME
from wicket.errors import refusals

from .lib import LinkRefused
from .lib.results import CreateAccount, DeleteAccount, ListAccount


def _link(name: str) -> Path:
    """The account's ``imap.json``: ``~/.config/wicket/accounts/<name>/imap.json``."""
    return env.resolve_accounts_dir() / name / CREDENTIALS_FILENAME


def _target(link: Path) -> str:
    """What ``imap.json`` names: a link's target, or a real file's own path."""
    return os.readlink(link) if link.is_symlink() else str(link)


@refusals
def list_account() -> ListAccount:
    """List each account: its name and the target of its imap.json link."""
    return {
        "accounts": [
            [p.name, _target(p / CREDENTIALS_FILENAME)] for p in env.account_dirs()
        ]
    }


@refusals
def create_account(name: str, config: Path) -> CreateAccount:
    """Make ~/.config/wicket/accounts/<name>/ and link its imap.json to
    <config>/imap.json.

    The same link already there changes nothing. A missing ``config`` folder and an
    ``imap.json`` naming another target are refused, and nothing is written. The target
    file need not exist yet: the first interactive fetch writes it through the link.
    """
    env.set_process_account(name)
    folder = config.expanduser()
    target = folder / CREDENTIALS_FILENAME
    link = _link(name)
    if link.is_symlink() or link.exists():
        current = _target(link)
        if current == str(target):
            return {"name": name, "config": current, "changed": False}
        raise LinkRefused(f"link exists with different target ({current})", "conflict")
    if not folder.exists():
        raise LinkRefused("--config path does not exist", "not_found")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return {"name": name, "config": str(target), "changed": True}


@refusals
def delete_account(name: str) -> DeleteAccount:
    """Remove the imap.json link of ~/.config/wicket/accounts/<name>/; a real file is
    renamed imap_YYYYMMDD_HHMM.json.

    A link is removed, never what it points to. A real file is renamed (the local time
    of the delete) in the same folder, so the credential survives and the folder is no
    account; that name already there is refused, and nothing is moved. No ``imap.json``
    changes nothing; the folder itself is kept.
    """
    env.set_process_account(name)
    link = _link(name)
    if link.is_symlink():
        link.unlink()
        return {"name": name, "changed": True}
    if not link.exists():
        return {"name": name, "changed": False}
    kept = link.with_name(f"imap_{datetime.now():%Y%m%d_%H%M}.json")
    try:
        os.link(link, kept)  # never replaces a file already there, unlike a rename
    except FileExistsError as exc:
        raise LinkRefused(f"{kept.name} exists; nothing moved", "conflict") from exc
    link.unlink()
    return {"name": name, "changed": True}


# CLI spelling -> the function it binds. A verb spelled like a builtin (`list`) is a key
# here rather than a function name; the lexicon reads this literal to find each verb.
VERBS: dict[str, Callable[..., object]] = {
    "list": list_account,
    "create": create_account,
    "delete": delete_account,
}
