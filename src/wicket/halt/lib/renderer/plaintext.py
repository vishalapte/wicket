"""The halt views as terminal text.

Every line this noun's cli prints is written here. Each ``print_<verb>`` takes that verb's
result and writes it; it reads no store and decides nothing, and it reads the same types
the api returns, so what it prints cannot drift from them.
"""

from __future__ import annotations

import sys
from datetime import datetime

from ..results import ClearHalt, ListHalt, SetHalt


def _age(seconds: int) -> str:
    """``2d03h``, ``3h12m`` or ``12m``: an age at the precision a HALT needs."""
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _when(iso: str) -> str:
    """An ISO timestamp as ``YYYY-MM-DD HH:MM TZ``, in local time."""
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def print_list(result: ListHalt) -> None:
    """``halt list`` as text: one row per halted account, or that none is."""
    halts = result["halts"]
    if not halts:
        print("no account is halted")
        return
    table = [("account", "halted", "age")]
    table += [(h["account"], _when(h["halted"]), _age(h["age_seconds"])) for h in halts]
    widths = [max(len(row[i]) for row in table) for i in range(2)]
    for row in table:
        cells = [cell.ljust(width) for cell, width in zip(row, widths)]
        print("  ".join([*cells, row[2]]).rstrip())


def print_set(result: SetHalt) -> None:
    """``halt set`` as text: each account halted, or already halted and kept."""
    if not result["halts"]:
        print("nothing to halt: no account in ~/.config/wicket/accounts")
        return
    for row in result["halts"]:
        if row["changed"]:
            print(f"halted {row['account']} at {_when(row['halted'])}")
        else:
            print(
                f"already halted {row['account']} since {_when(row['halted'])} (kept)"
            )


def print_clear(result: ClearHalt) -> None:
    """``halt clear`` as text: each HALT cleared, and each account that was not halted."""
    for row in result["cleared"]:
        print(f"cleared {row['account']} (halted {_when(row['halted'])})")
    for account in result["not_halted"]:
        print(f"not halted: {account}")
    if not result["cleared"] and not result["not_halted"]:
        print("no account is halted")


def error(message: str) -> None:
    """A refusal the api raised, on stderr, so stdout stays empty."""
    print(message, file=sys.stderr)
