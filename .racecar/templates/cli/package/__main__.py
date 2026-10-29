"""CLI entry: python -m __PKG__

The discovery root. It lists the nouns this package composes and runs nothing. Register
a new noun by adding its row to commands().
"""

from __future__ import annotations

import sys

from __PKG__.lib.cli import print_commands


def commands() -> list[tuple[str, str]]:
    """The immediate `python -m __PKG__.<noun>` children, as (name, description)."""
    return []


def _print_commands() -> None:
    """Print the listing. The symbol is per node; the body is shared in lib/cli.py."""
    print_commands(__package__ or "__PKG__", commands())


if __name__ == "__main__":
    _print_commands()
    sys.exit(0)
