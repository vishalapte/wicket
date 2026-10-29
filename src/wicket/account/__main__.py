"""CLI entry: python -m wicket.account

An account is a folder, ``~/.config/wicket/accounts/<name>/``, holding an ``imap.json``
link to its credential file, made here or by hand with ``mkdir`` and ``ln -s``; ``fetch``,
``catalog`` and ``halt --all`` read the same folders. No verb here opens an IMAP
connection or reads the mail root.

A thin surface over ``api``: each verb is one ``_cmd_<verb>`` that calls one api function
and hands its result to a renderer. Every verb takes ``--json``: the api result as JSON on
stdout, and a refusal as one error packet on stderr. ``create`` and ``delete`` print
nothing without ``--json``; the exit code says what happened.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from wicket.account import api
from wicket.account.lib import LinkRefused
from wicket.account.lib.renderer import plaintext
from wicket.errors import ApiError
from wicket.lib.cli import NounParser, print_commands, run
from wicket.lib.error._packet import packet
from wicket.lib.renderer import json as json_renderer
from wicket.schema import returns


def commands() -> list[tuple[str, str]]:
    """This node's sub-nouns, as (name, summary); its verbs are declared in subcommands()."""
    return []


def _print_commands() -> None:
    """This node's listing: its sub-nouns, then its verbs. The body is lib/cli.py's."""
    print_commands(__package__ or "wicket.account", commands(), subcommands())


def _show(
    args: argparse.Namespace,
    call: Callable[[], Mapping[str, Any]],
    text: Callable[[Any], None],
) -> int:
    """Run ``call``; under ``--json`` a refusal is one error packet on stderr."""
    try:
        result = call()
    except LinkRefused as exc:
        if not args.json:
            raise
        plaintext.error_packet(
            packet(exc.code, noun="account", verb=args.phase, message=str(exc))
        )
        return exc.exit_code
    return json_renderer.show(args, result, text)


def _cmd_list(args: argparse.Namespace) -> int:
    return _show(args, api.list_account, plaintext.print_list)


def _cmd_create(args: argparse.Namespace) -> int:
    return _show(
        args,
        lambda: api.create_account(args.name, args.config),
        plaintext.print_create,
    )


def _cmd_delete(args: argparse.Namespace) -> int:
    return _show(args, lambda: api.delete_account(args.name), plaintext.print_delete)


def subcommands() -> list[tuple[str, str]]:
    """One (verb, summary) per verb: the one home of each verb's help text."""
    return [
        ("list", "List each account: its name and the target of its imap.json link."),
        (
            "create",
            "Make ~/.config/wicket/accounts/<name>/ and link its imap.json to "
            "<config>/imap.json.",
        ),
        (
            "delete",
            (
                "Remove the imap.json link of ~/.config/wicket/accounts/<name>/; "
                "a real file is renamed imap_YYYYMMDD_HHMM.json."
            ),
        ),
    ]


def parser() -> argparse.ArgumentParser:
    """Build the parser without parsing: the audit and --help both read this."""
    p = NounParser(
        prog="python -m wicket.account",
        description=(
            "An account is a folder, ~/.config/wicket/accounts/<name>/, holding an "
            "imap.json link to its credential file."
        ),
        nodes=commands(),
    )
    sub = p.verbs(subcommands(), dest="phase")

    p_list = sub.add_parser("list")
    p_list.set_defaults(func=_cmd_list)

    p_create = sub.add_parser("create")
    p_create.add_argument("name", help="The account's name: its folder's name.")
    p_create.add_argument(
        "--config",
        type=Path,
        required=True,
        help="The folder holding imap.json; the account's imap.json links to it.",
    )
    p_create.set_defaults(func=_cmd_create)

    p_delete = sub.add_parser("delete")
    p_delete.add_argument("name", help="The account's name: its folder's name.")
    p_delete.set_defaults(func=_cmd_delete)

    for verb_parser in sub.choices.values():
        json_renderer.add_flag(verb_parser)
    return p


def main(argv: list[str] | None = None) -> int:
    """Run one verb; with none, list the sub-nouns, or print the help when there are none."""
    listing = _print_commands if commands() else None
    return run(parser(), argv, refusal=ApiError, error=plaintext.error, listing=listing)


def output() -> list[tuple[dict[str, object], dict[str, object]]]:
    """(params, schema) per verb, generated from each api function's return type."""
    return [({"phase": verb}, returns(fn)) for verb, fn in sorted(api.VERBS.items())]


if __name__ == "__main__":
    raise SystemExit(main())
