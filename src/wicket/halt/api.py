"""The halt api: the one place a request becomes a decision.

Every surface (this noun's cli, an MCP tool, a REST route) calls these functions and
nothing below them. Each verb is one function returning one result type from
``lib.results``, plain JSON. A request the api cannot run raises ``ApiError``. No verb
opens an IMAP connection: a HALT is a file, set and cleared through `wicket.auth`, which
logs each one.
"""

from __future__ import annotations

from collections.abc import Callable

from wicket.env import normalize_account
from wicket.errors import ApiError

from .lib import all_accounts, clear_one, rows, set_one
from .lib.results import ClearHalt, ClearRow, ListHalt, SetHalt


def _accounts(mail_account: str | None, all_: bool) -> list[str] | None:
    """The one account ``mail_account`` names, normalized as text only, else None for
    ``all_``. Exactly one of the two is required. Nothing outside the state base is read:
    the noun depends on no mail root, alias file or credential."""
    if bool(mail_account) == bool(all_):
        raise ApiError("name exactly one of --mail-account or --all")
    if all_:
        return None
    account = normalize_account(mail_account or "")
    if "@" not in account:
        raise ApiError(f"not an email address: {mail_account!r}")
    return [account]


def list_halt() -> ListHalt:
    """List every halted account: when it was halted (the file's timestamp) and its age."""
    return {"halts": rows()}


def set_halt(
    mail_account: str | None = None,
    all_: bool = False,
) -> SetHalt:
    """Halt an account, or every account in ~/.config/wicket/accounts; a HALT already in
    force is kept."""
    named = _accounts(mail_account, all_)
    accounts = all_accounts() if named is None else named
    return {"halts": [set_one(a) for a in accounts]}


def clear_halt(
    mail_account: str | None = None, all_: bool = False, min_: int | None = None
) -> ClearHalt:
    """Clear the HALT of an account, or of every account in ~/.config/wicket/accounts;
    each clear is logged. With ``min_``, only a HALT over ``min_`` minutes old."""
    if min_ is not None and min_ < 0:
        raise ApiError(f"--min takes 0 or more minutes, not {min_}")
    named = _accounts(mail_account, all_)
    accounts = all_accounts() if named is None else named
    cleared: list[ClearRow] = []
    not_halted: list[str] = []
    for account in accounts:
        row = clear_one(account, min_)
        if row is None:
            not_halted.append(account)
        else:
            cleared.append(row)
    return {"cleared": cleared, "not_halted": not_halted}


# CLI spelling -> the function it binds. A verb spelled like a keyword or a builtin
# (`import`, `list`) is a key here rather than a function name; the lexicon reads this
# literal to find each verb.
VERBS: dict[str, Callable[..., object]] = {
    "list": list_halt,
    "set": set_halt,
    "clear": clear_halt,
}
