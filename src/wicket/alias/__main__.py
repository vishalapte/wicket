"""CLI entry: python -m wicket.alias

A domain that is really another: their domain → the domain it files under (domain-
aliases.json).

A thin surface over ``api``: each verb is one ``_cmd_<verb>`` that calls one api function
and hands its result to a renderer. It decides nothing and prints nothing itself. Every
verb takes ``--json``: the api result as JSON, the shape ``output()`` publishes, instead of
the text view. ``main()`` is ``lib.cli.run``: an ``ApiError`` the api raises is printed on
stderr and its ``exit_code`` returned.
"""

from __future__ import annotations

import argparse

from wicket.alias import api
from wicket.alias.lib.renderer import plaintext
from wicket.errors import ApiError
from wicket.lib.cli import NounParser, print_commands, run
from wicket.lib.renderer import json as json_renderer
from wicket.schema import returns


def commands() -> list[tuple[str, str]]:
    """This node's sub-nouns, as (name, summary); its verbs are declared in subcommands()."""
    return []


def _print_commands() -> None:
    """This node's listing: its sub-nouns, then its verbs. The body is lib/cli.py's."""
    print_commands(__package__ or "wicket.alias", commands(), subcommands())


def _cmd_create(args: argparse.Namespace) -> int:
    result = api.create_alias(primary=args.primary, item=args.item)
    return json_renderer.show(args, result, plaintext.print_create)


def _cmd_delete(args: argparse.Namespace) -> int:
    result = api.delete_alias(primary=args.primary, item=args.item)
    return json_renderer.show(args, result, plaintext.print_delete)


def _cmd_list(args: argparse.Namespace) -> int:
    result = api.list_alias()
    return json_renderer.show(args, result, plaintext.print_list)


def _cmd_update(args: argparse.Namespace) -> int:
    result = api.update_alias(primary=args.primary, add=args.add, remove=args.remove)
    return json_renderer.show(args, result, plaintext.print_update)


def subcommands() -> list[tuple[str, str]]:
    """One (verb, summary) per verb: the one home of each verb's help text."""
    return [
        ("create", "Add a primary domain and the domains that fold into it."),
        ("delete", "Delete a primary, or the named domains under it."),
        ("list", "List every alias entry."),
        ("update", "Add or remove domains under an existing primary."),
    ]


def parser() -> argparse.ArgumentParser:
    """Build the parser without parsing: the audit and --help both read this."""
    p = NounParser(
        prog="python -m wicket.alias",
        description=(
            "A domain that is really another: their domain → the domain "
            "it files under (domain-aliases.json)."
        ),
        nodes=commands(),
    )
    sub = p.verbs(subcommands(), dest="phase")

    p_create = sub.add_parser("create")
    p_create.add_argument(
        "--primary",
        required=True,
        help="The entry's key: the primary domain an alias folds into.",
    )
    p_create.add_argument(
        "--item", action="append", help="A value under --primary (repeatable)."
    )
    p_create.set_defaults(func=_cmd_create)

    p_delete = sub.add_parser("delete")
    p_delete.add_argument(
        "--primary",
        required=True,
        help="The entry's key: the primary domain an alias folds into.",
    )
    p_delete.add_argument(
        "--item", action="append", help="A value under --primary (repeatable)."
    )
    p_delete.set_defaults(func=_cmd_delete)

    p_list = sub.add_parser("list")
    p_list.set_defaults(func=_cmd_list)

    p_update = sub.add_parser("update")
    p_update.add_argument(
        "--primary",
        required=True,
        help="The entry's key: the primary domain an alias folds into.",
    )
    p_update.add_argument(
        "--add", action="append", help="A value to add under --primary (repeatable)."
    )
    p_update.add_argument(
        "--remove",
        action="append",
        help="A value to remove from under --primary (repeatable).",
    )
    p_update.set_defaults(func=_cmd_update)

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
