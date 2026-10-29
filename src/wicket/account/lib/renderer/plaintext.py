"""The account results as text: what each verb prints without ``--json``.

``create`` and ``delete`` print nothing: their exit code is what matters (owner,
2026-10-27). ``list`` prints one ``<name>  <imap.json target>`` line per account.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping

from ..results import CreateAccount, DeleteAccount, ListAccount


def print_list(result: ListAccount) -> None:
    """``account list`` as text: one name and its ``imap.json`` target per line."""
    width = max((len(name) for name, _ in result["accounts"]), default=0)
    for name, target in result["accounts"]:
        print(f"{name.ljust(width)}  {target}")


def print_create(result: CreateAccount) -> None:
    """``account create`` as text: nothing."""
    del result


def print_delete(result: DeleteAccount) -> None:
    """``account delete`` as text: nothing."""
    del result


def error(message: str) -> None:
    """A refusal the api raised, on stderr, so stdout stays empty."""
    print(message, file=sys.stderr)


def error_packet(refusal: Mapping[str, object]) -> None:
    """A refusal under ``--json``: one error packet on stderr, so stdout stays empty."""
    print(json.dumps(refusal, ensure_ascii=False), file=sys.stderr)
