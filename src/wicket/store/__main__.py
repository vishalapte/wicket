"""CLI entry: python -m wicket.store

Which store mail goes to: a sender domain (--from, domain-routes.json) or one of your
addresses (--to, account-aliases.json) → a store under the mail root.

A thin surface over ``api``: each verb is one ``_cmd_<verb>`` that calls one api function
and hands its result to a renderer. It decides nothing and prints nothing itself. Every
verb takes ``--json``: the api result as JSON, the shape ``output()`` publishes, instead of
the text view. ``main()`` is ``lib.cli.run``: an ``ApiError`` the api raises is printed on
stderr and its ``exit_code`` returned.
"""

from __future__ import annotations

import argparse

from wicket.errors import ApiError
from wicket.lib.cli import NounParser, print_commands, run
from wicket.lib.renderer import json as json_renderer
from wicket.schema import returns
from wicket.store import api
from wicket.store.lib.renderer import plaintext


def commands() -> list[tuple[str, str]]:
    """This node's sub-nouns, as (name, summary); its verbs are declared in subcommands()."""
    return []


def _print_commands() -> None:
    """This node's listing: its sub-nouns, then its verbs. The body is lib/cli.py's."""
    print_commands(__package__ or "wicket.store", commands(), subcommands())


def _cmd_create(args: argparse.Namespace) -> int:
    result = api.create_store(store=args.store, from_=args.from_, to=args.to)
    return json_renderer.show(args, result, plaintext.print_create)


def _cmd_delete(args: argparse.Namespace) -> int:
    result = api.delete_store(store=args.store, from_=args.from_, to=args.to)
    return json_renderer.show(args, result, plaintext.print_delete)


def _cmd_list(args: argparse.Namespace) -> int:
    result = api.list_store()
    return json_renderer.show(args, result, plaintext.print_list)


def subcommands() -> list[tuple[str, str]]:
    """One (verb, summary) per verb: the one home of each verb's help text."""
    return [
        ("create", "Add sender domains and your addresses to a store."),
        (
            "delete",
            (
                "Remove the named domains and addresses from a store; refuses "
                "when none are named."
            ),
        ),
        ("list", "List every store with its sender domains and owner addresses."),
    ]


def parser() -> argparse.ArgumentParser:
    """Build the parser without parsing: the audit and --help both read this."""
    p = NounParser(
        prog="python -m wicket.store",
        description=(
            "Which store mail goes to: a sender domain (--from, "
            "domain-routes.json) or one of your addresses (--to, "
            "account-aliases.json) → a store under the mail root."
        ),
        nodes=commands(),
    )
    sub = p.verbs(subcommands(), dest="phase")

    p_create = sub.add_parser("create")
    p_create.add_argument(
        "--store",
        required=True,
        help=(
            "The store the entries belong to: an account address or a "
            "bucket name, such as travel or shopping."
        ),
    )
    p_create.add_argument(
        "--from",
        dest="from_",
        action="append",
        help=(
            "A sender domain whose mail goes to --store "
            "(domain-routes.json; repeatable)."
        ),
    )
    p_create.add_argument(
        "--to",
        action="append",
        help=(
            "One of your addresses whose mail goes to --store "
            "(account-aliases.json; repeatable)."
        ),
    )
    p_create.set_defaults(func=_cmd_create)

    p_delete = sub.add_parser("delete")
    p_delete.add_argument(
        "--store",
        required=True,
        help=(
            "The store the entries belong to: an account address or a "
            "bucket name, such as travel or shopping."
        ),
    )
    p_delete.add_argument(
        "--from",
        dest="from_",
        action="append",
        help=(
            "A sender domain whose mail goes to --store "
            "(domain-routes.json; repeatable)."
        ),
    )
    p_delete.add_argument(
        "--to",
        action="append",
        help=(
            "One of your addresses whose mail goes to --store "
            "(account-aliases.json; repeatable)."
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
