"""CLI entry: python -m wicket.halt

A HALT stops every IMAP connection to an account until it is cleared: a file under
~/.local/state/wicket/halt/ (present means halted), each set and clear logged in
log/<account>.log. No verb here opens an IMAP connection.

A thin surface over ``api``: each verb is one ``_cmd_<verb>`` that calls one api function
and hands its result to a renderer. It decides nothing and prints nothing itself. Every
verb takes ``--json``: the api result as JSON, the shape ``output()`` publishes, instead of
the text view. ``main()`` is ``lib.cli.run``: an ``ApiError`` the api raises is printed on
stderr and its ``exit_code`` returned.
"""

from __future__ import annotations

import argparse

from wicket.errors import ApiError
from wicket.halt import api
from wicket.halt.lib.renderer import plaintext
from wicket.lib.cli import NounParser, print_commands, run
from wicket.lib.renderer import json as json_renderer
from wicket.schema import returns


def commands() -> list[tuple[str, str]]:
    """This node's sub-nouns, as (name, summary); its verbs are declared in subcommands()."""
    return []


def _print_commands() -> None:
    """This node's listing: its sub-nouns, then its verbs. The body is lib/cli.py's."""
    print_commands(__package__ or "wicket.halt", commands(), subcommands())


def _cmd_list(args: argparse.Namespace) -> int:
    result = api.list_halt()
    return json_renderer.show(args, result, plaintext.print_list)


def _cmd_set(args: argparse.Namespace) -> int:
    result = api.set_halt(
        mail_account=args.mail_account,
        all_=args.all_,
    )
    return json_renderer.show(args, result, plaintext.print_set)


def _cmd_clear(args: argparse.Namespace) -> int:
    result = api.clear_halt(
        mail_account=args.mail_account, all_=args.all_, min_=args.min_
    )
    return json_renderer.show(args, result, plaintext.print_clear)


def subcommands() -> list[tuple[str, str]]:
    """One (verb, summary) per verb: the one home of each verb's help text."""
    return [
        (
            "list",
            "List every halted account: when it was halted (the file's timestamp) and its age.",
        ),
        (
            "set",
            (
                "Halt an account, or every account in ~/.config/wicket/accounts; "
                "a HALT already in force is kept."
            ),
        ),
        (
            "clear",
            (
                "Clear the HALT of an account, or of every account in "
                "~/.config/wicket/accounts; each clear is logged."
            ),
        ),
    ]


def _which(verb: argparse.ArgumentParser) -> None:
    """``--mail-account`` or ``--all``: exactly one, never both."""
    which = verb.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "--mail-account",
        help=(
            "The account (a mailbox) whose store the verb reads or writes; "
            "Gmail `+tag` aliases are normalized."
        ),
    )
    which.add_argument(
        "--all",
        dest="all_",
        action="store_true",
        help=("Every account in ~/.config/wicket/accounts."),
    )


def parser() -> argparse.ArgumentParser:
    """Build the parser without parsing: the audit and --help both read this."""
    p = NounParser(
        prog="python -m wicket.halt",
        description=(
            "A HALT stops every IMAP connection to an account until it is "
            "cleared: a file under ~/.local/state/wicket/halt/ (present means "
            "halted), each set and clear logged in log/<account>.log."
        ),
        nodes=commands(),
    )
    sub = p.verbs(subcommands(), dest="phase")

    p_list = sub.add_parser("list")
    p_list.set_defaults(func=_cmd_list)

    p_set = sub.add_parser("set")
    _which(p_set)
    p_set.set_defaults(func=_cmd_set)

    p_clear = sub.add_parser("clear")
    _which(p_clear)
    p_clear.add_argument(
        "--min",
        dest="min_",
        type=int,
        metavar="N",
        help=(
            "Clear a HALT only when its age in whole seconds is over N x 60; a "
            "younger one is left and reported as not halted."
        ),
    )
    p_clear.set_defaults(func=_cmd_clear)

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
