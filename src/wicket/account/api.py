"""The account api: the one place a request becomes a decision.

Every surface calls these functions and nothing below them. Each verb is one function
returning one result type from ``lib.results``, plain JSON. A request the api cannot run
raises ``ApiError``. No verb opens an IMAP connection or reads the mail root.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .lib import accounts, create, delete
from .lib.results import CreateAccount, DeleteAccount, ListAccount


def list_account() -> ListAccount:
    """List each account: its name and the target of its imap.json link."""
    return accounts()


def create_account(name: str, config: Path) -> CreateAccount:
    """Make ~/.config/wicket/accounts/<name>/ and link its imap.json to
    <config>/imap.json."""
    return create(name, config)


def delete_account(name: str) -> DeleteAccount:
    """Remove the imap.json link of ~/.config/wicket/accounts/<name>/; a real file is
    renamed imap_YYYYMMDD_HHMM.json."""
    return delete(name)


# CLI spelling -> the function it binds. A verb spelled like a builtin (`list`) is a key
# here rather than a function name; the lexicon reads this literal to find each verb.
VERBS: dict[str, Callable[..., object]] = {
    "list": list_account,
    "create": create_account,
    "delete": delete_account,
}
