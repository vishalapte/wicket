"""The halt worker: the computation, with no knowledge of who calls it.

No argument parsing, no printing, no default chosen on a caller's behalf. ``api`` imports
from here and reaches no deeper; the renderers under ``lib/renderer/`` import only
``lib.results``. Every read and write of a HALT goes through `wicket.auth` (`write_halt`,
`clear_halt`, `read_halt`, `list_halts`), the one door that logs it; nothing here touches
the halt dir itself.
"""

from __future__ import annotations

from pathlib import Path

import wicket.env as env  # pylint: disable=consider-using-from-import
from wicket import auth
from wicket.env import CREDENTIALS_FILENAME, normalize_account

from .results import ClearRow, HaltRow, SetRow

# The cause `halt set` logs.
SET_BY_HAND = "by hand (python -m wicket.halt set)"


def all_accounts() -> list[str]:
    """What ``--all`` means, on every verb: every account (`env.account_dirs`), sorted.

    The one home of that meaning for this noun (owner, 2026-10-28: "list of accounts are
    all directories that contain an imap.json"): each folder in
    ~/.config/wicket/accounts holding an ``imap.json``. Reads only; creates nothing.
    """
    return sorted(normalize_account(p.name) for p in env.account_dirs())


def rows() -> list[HaltRow]:
    """Every HALT in force, by account."""
    return [
        {
            "account": info.account,
            "halted": info.halted.isoformat(timespec="seconds"),
            "age_seconds": info.age_seconds,
        }
        for info in auth.list_halts()
    ]


def set_one(account: str) -> SetRow:
    """Halt one account; a HALT in force is kept.

    `auth.write_halt` reads the account off the credential path's directory name and
    never opens the path, so a relative one names the account and touches nothing.
    """
    before = auth.read_halt(account)
    auth.write_halt(Path(account) / CREDENTIALS_FILENAME, SET_BY_HAND)
    info = auth.read_halt(account)
    return {
        "account": normalize_account(account),
        "halted": info.halted.isoformat(timespec="seconds") if info else "",
        "changed": before is None,
    }


def clear_one(account: str, min_: int | None) -> ClearRow | None:
    """Clear one account's HALT; None when not halted.

    With ``min_``, a HALT whose age in whole seconds is not over ``min_`` x 60 is left,
    and None returned as for an account not halted.
    """
    if min_ is not None:
        held = auth.read_halt(account)
        if held is not None and held.age_seconds <= min_ * 60:
            return None
    info = auth.clear_halt(account)
    if info is None:
        return None
    return {
        "account": info.account,
        "halted": info.halted.isoformat(timespec="seconds"),
    }
