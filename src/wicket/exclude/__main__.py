"""CLI entry: python -m wicket.exclude

A mailing list whose mail is never fetched or filed (excluded-lists.json), for every
account or one.

A thin surface over ``api``: each verb is one ``_cmd_<verb>`` that calls one api function
and hands its result to a renderer. It decides nothing and prints nothing itself. Every
verb takes ``--json``: the api result as JSON, the shape ``output()`` publishes, instead of
the text view. ``main()`` is ``lib.cli.run``: an ``ApiError`` the api raises is printed on
stderr and its ``exit_code`` returned.
"""

from __future__ import annotations

import argparse

from wicket.errors import ApiError
from wicket.exclude import api
from wicket.exclude.lib.renderer import plaintext
from wicket.lib.cli import NounParser, print_commands, run
from wicket.lib.renderer import json as json_renderer
from wicket.schema import returns


def commands() -> list[tuple[str, str]]:
    """This node's sub-nouns, as (name, summary); its verbs are declared in subcommands()."""
    return []


def _print_commands() -> None:
    """This node's listing: its sub-nouns, then its verbs. The body is lib/cli.py's."""
    print_commands(__package__ or "wicket.exclude", commands(), subcommands())


def _cmd_create(args: argparse.Namespace) -> int:
    result = api.create_exclude(list_=args.list_, mail_account=args.mail_account)
    return json_renderer.show(args, result, plaintext.print_create)


def _cmd_delete(args: argparse.Namespace) -> int:
    result = api.delete_exclude(list_=args.list_, mail_account=args.mail_account)
    return json_renderer.show(args, result, plaintext.print_delete)


def _cmd_list(args: argparse.Namespace) -> int:
    result = api.list_exclude()
    return json_renderer.show(args, result, plaintext.print_list)


def subcommands() -> list[tuple[str, str]]:
    """One (verb, summary) per verb: the one home of each verb's help text."""
    return [
        (
            "create",
            (
                "Exclude a mailing list, for every account, or for "
                "--mail-account alone."
            ),
        ),
        (
            "delete",
            (
                "Stop excluding a mailing list, for every account, or for "
                "--mail-account alone."
            ),
        ),
        (
            "list",
            "List every excluded mailing list, by account (* is every account).",
        ),
    ]


def parser() -> argparse.ArgumentParser:
    """Build the parser without parsing: the audit and --help both read this."""
    p = NounParser(
        prog="python -m wicket.exclude",
        description=(
            "A mailing list whose mail is never fetched or filed "
            "(excluded-lists.json), for every account or one."
        ),
        nodes=commands(),
    )
    sub = p.verbs(subcommands(), dest="phase")

    p_create = sub.add_parser("create")
    p_create.add_argument(
        "--list",
        dest="list_",
        required=True,
        help=(
            "A mailing list's List-Id (made-of-meat.googlegroups.com): "
            "its mail is never fetched or filed."
        ),
    )
    p_create.add_argument(
        "--mail-account",
        help=(
            "The account (a folder holding imap.json in "
            "~/.config/wicket/accounts) the list is excluded for; without it, "
            "every account (*)."
        ),
    )
    p_create.set_defaults(func=_cmd_create)

    p_delete = sub.add_parser("delete")
    p_delete.add_argument(
        "--list",
        dest="list_",
        required=True,
        help=(
            "A mailing list's List-Id (made-of-meat.googlegroups.com): "
            "its mail is never fetched or filed."
        ),
    )
    p_delete.add_argument(
        "--mail-account",
        help=(
            "The account (a folder holding imap.json in "
            "~/.config/wicket/accounts) the list is excluded for; without it, "
            "every account (*)."
        ),
    )
    p_delete.set_defaults(func=_cmd_delete)

    p_list = sub.add_parser("list")
    p_list.set_defaults(func=_cmd_list)

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
