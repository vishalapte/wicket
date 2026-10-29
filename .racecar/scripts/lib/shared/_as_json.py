"""`--json` for a verb that prints as it works: the api result on stdout, everything else not.

Most of racecar's verbs print while they work, their own lines and those of every checker
script they run in a subprocess, and return an exit code. `run_json` sends file descriptor 1
to 2 while the verb runs, which a subprocess inherits, then prints the result on the real
stdout.

A bare number is a JSON document (RFC 8259), so an exit code is printed as itself. The exit
code the command returns is the one it returned without `--json`.

Complexity: O(1) per call, plus the result's serialisation
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

HELP = "print the result as JSON on stdout; everything else goes to stderr"

#: Params the form gives every verb, so a lexicon that lists one is not a flag to add:
#: argparse refuses a second `--json`, which fails every verb of the noun. The lexicon's
#: coverage table leaves them out of both counts.
FORM_PARAMS = frozenset({"json"})


def add_json(verb_parser: argparse.ArgumentParser) -> None:
    """Give a verb's parser `--json`, unless it already takes one of its own.

    argparse refuses a second `--json` with `ArgumentError`, and that refusal is how a verb
    whose own `--json` means something else is told apart.
    """
    try:
        verb_parser.add_argument("--json", action="store_true", help=HELP)
    except argparse.ArgumentError:
        pass


@contextlib.contextmanager
def stdout_to_stderr() -> Iterator[None]:
    """Send stdout to stderr for the block: file descriptor 1, which a subprocess inherits,
    and `sys.stdout`, which need not write to descriptor 1 at all."""
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)


def _plain(value: Any) -> Any:
    """What `json.dumps` cannot take itself: a dataclass as its fields, a path as text."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "_asdict"):
        return value._asdict()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def print_json(value: Any) -> None:
    """One JSON document on stdout."""
    print(json.dumps(value, default=_plain, indent=2))


def run_json(
    as_json: bool,
    call: Callable[[], T],
    *,
    show: Callable[[T], Any] | None = None,
    exit_code: Callable[[T], int] | None = None,
) -> int:
    """Run one verb; under `--json`, what it prints goes to stderr and its result to stdout.

    `call` is the verb as it runs without `--json`, returning its result. `show` is the
    value printed as JSON, the result itself by default; `exit_code` is the command's exit
    code, the result itself by default, for the verbs whose result is their exit code.
    """
    if not as_json:
        result = call()
        return exit_code(result) if exit_code else int(result)  # type: ignore[call-overload]
    with stdout_to_stderr():
        result = call()
    print_json(show(result) if show else result)
    return exit_code(result) if exit_code else int(result)  # type: ignore[call-overload]
