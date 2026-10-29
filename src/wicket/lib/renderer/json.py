"""Any verb's api result as JSON on stdout: what every cli prints under ``--json``.

One renderer for every noun, because nothing about it is noun-specific. A result holds
JSON types only, so rendering it is ``json.dumps`` and nothing else: no key is added,
renamed or dropped, and the shape printed is the one ``output()`` publishes. Nothing else
reaches stdout under ``--json``; a diagnostic or a refusal goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypeVar

Result = TypeVar("Result", bound=Mapping[str, Any])

FLAG_HELP = (
    "Print the api result as JSON (the shape output() publishes) instead of text."
)


class ArgumentHolder(Protocol):  # pylint: disable=too-few-public-methods
    """A parser, or a mutually exclusive group of one: whatever takes an argument."""

    def add_argument(self, *names: str, **options: Any) -> argparse.Action:
        """Declare one argument."""


def add_flag(holder: ArgumentHolder, *, help_text: str = FLAG_HELP) -> None:
    """Give a verb's parser (or a mutually exclusive group of one) the ``--json`` switch."""
    holder.add_argument("--json", action="store_true", help=help_text)


def render(result: Mapping[str, object]) -> str:
    """The result as indented JSON text, non-ASCII kept as written, one trailing newline."""
    return json.dumps(result, indent=2, ensure_ascii=False) + "\n"


def print_json(result: Mapping[str, object]) -> None:
    """Write the result to stdout as JSON, and nothing else."""
    sys.stdout.write(render(result))


def show(
    args: argparse.Namespace,
    result: Result,
    print_text: Callable[[Result], None],
    *,
    found: bool = False,
) -> int:
    """Print the result as JSON under ``--json``, else as the verb's text view.

    The exit code is 0, or 1 when ``found``: a verb that reports findings, such as a check,
    passes ``found`` from its result, and exits 1 when it ran and found something.
    """
    if args.json:
        print_json(result)
    else:
        print_text(result)
    return 1 if found else 0
