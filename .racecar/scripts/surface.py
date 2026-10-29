#!/usr/bin/env python3
"""Build the cli face of this repo from its lexicon, and bring an existing cli into line.

The command line over the delivered `lib/surface/` package, so any repo that syncs racecar
builds and conforms its own nouns and verbs with no racecar installed:

    python3 .racecar/scripts/surface.py create --surface cli --noun N --verb V --kind read
    python3 .racecar/scripts/surface.py check --surface cli
    python3 .racecar/scripts/surface.py update --surface cli
    python3 .racecar/scripts/surface.py upgrade --surface cli

Seven verbs, the same seven `python -m racecar.surface` offers: that route builds its parser
with `parser` below and runs `main`, handing in its own api. `rest` and `mcp` are built by
racecar's server generator, which is not delivered; naming one here says where to run it.

The implementation is `lib/surface/`, one module per verb, each with a `run` that returns the
verb's record and a `main` over the values this file's parser produced.

Exit codes: 0 nothing left, 1 findings or work left, 2 the request could not be carried out.

Complexity: O(N + V) in the lexicon's nouns and verbs plus one CLI-tree audit per run, which
imports the repo's code and dominates; `check --against` adds two runs of every command line
the edit reaches, and `check-json` one run of every read command.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib import surface
from lib.shared import _cli
from lib.shared._root import find_repo_root
from lib.surface import (
    _check,
    _check_json,
    _create,
    _generate,
    _list,
    _update,
    _upgrade,
)
from lib.surface._record import OK, UNMET
from lib.surface.renderer import text

#: The name this script's usage line gives it, when no wrapper names another.
PROG = "python scripts/surface.py"

#: `(verb, one-line summary)` per verb, the order the help lists them in.
VERBS: tuple[tuple[str, str], ...] = (
    ("create", "Declare a noun or verb, then build its surface where absent"),
    ("check", "What differs from the canonical form or the lexicon"),
    ("update", "What upgrade would change, and what it leaves; writes nothing"),
    ("upgrade", "Make the mechanical changes; report what is left"),
    ("list", "Each declared noun and verb, and whether the surface binds it"),
    ("check-json", "Run each read command with --json; did it print JSON?"),
    ("generate", "Render a face's generated docs: the README's CLI block"),
)


def _common(
    verb: argparse.ArgumentParser,
    *,
    surface_required: bool,
    noun_required: bool,
) -> None:
    """The flags every verb takes. A verb that writes names its faces, one or more
    `--surface`, or `--all` (CLI.md B7 and §7); a verb that reads defaults to `--all`, so it
    runs with no arguments at all."""
    faces = verb.add_mutually_exclusive_group(required=surface_required)
    faces.add_argument(
        "--surface",
        action="append",
        default=None,
        choices=surface.SURFACES,
        help="A face to act on (repeatable); this version builds "
        + ", ".join(surface.BUILT),
    )
    faces.add_argument(
        "--all",
        action="store_true",
        help="Every face this repo has"
        + ("" if surface_required else " (the default)"),
    )
    verb.add_argument(
        "--noun",
        required=noun_required,
        default=None,
        help="The noun to act on (dotted for a sub-noun)"
        + ("" if noun_required else "; default: every declared noun"),
    )
    verb.add_argument(
        "--root", type=Path, default=None, help="Repo to act on. Default: .git walk-up."
    )
    verb.add_argument(
        "--json", action="store_true", help="Print the result as JSON instead of text"
    )


def _add_verbs(sub: Any) -> None:
    """Add every verb to `sub` (a subparsers action), with its flags and one-line help.

    Every verb name is a literal, because the lexicon reads this file to learn what the
    delivered route offers.
    """
    helps = dict(VERBS)
    create = sub.add_parser("create", help=helps["create"])
    _common(create, surface_required=True, noun_required=True)
    create.add_argument(
        "--verb", default=None, help="A verb of --noun to declare and build"
    )
    create.add_argument(
        "--param",
        action="append",
        default=None,
        help="A param of --verb to declare, stubbed as --<param> (repeatable)",
    )
    create.add_argument(
        "--kind",
        choices=surface.KINDS,
        default=None,
        help="The spec row's kind, for a verb the repo's surface.jsonl has no row for",
    )
    check = sub.add_parser("check", help=helps["check"])
    _common(check, surface_required=False, noun_required=False)
    check.add_argument("--verb", default=None, help="Only this verb of --noun")
    check.add_argument(
        "--against",
        metavar="REF",
        default=None,
        help="Also run each command line the edit since REF can reach, on both trees, "
        "and report each whose output differs",
    )
    update = sub.add_parser("update", help=helps["update"])
    _common(update, surface_required=False, noun_required=False)
    update.add_argument("--verb", default=None, help="Only this verb of --noun")
    # `upgrade` writes, so it names the face; its noun is optional.
    upgrade = sub.add_parser("upgrade", help=helps["upgrade"])
    _common(upgrade, surface_required=True, noun_required=False)
    upgrade.add_argument("--verb", default=None, help="Only this verb of --noun")
    listing = sub.add_parser("list", help=helps["list"])
    _common(listing, surface_required=False, noun_required=False)
    check_json = sub.add_parser("check-json", help=helps["check-json"])
    _common(check_json, surface_required=False, noun_required=False)
    generate = sub.add_parser("generate", help=helps["generate"])
    faces = generate.add_mutually_exclusive_group(required=True)
    faces.add_argument(
        "--surface",
        action="append",
        default=None,
        choices=surface.SURFACES,
        help="A face whose docs to render (repeatable); cli renders the README block",
    )
    faces.add_argument("--all", action="store_true", help="Every face this repo has")
    generate.add_argument(
        "--docs", action="store_true", required=True, help="Render the face's docs"
    )
    generate.add_argument(
        "--apply", action="store_true", help="Rewrite for real; default reports only"
    )
    generate.add_argument(
        "--strict", action="store_true", help="Exit 1 when a rendering was stale"
    )
    generate.add_argument(
        "--root", type=Path, default=None, help="Repo to act on. Default: .git walk-up."
    )
    generate.add_argument(
        "--json", action="store_true", help="Print the result as JSON instead of text"
    )


def parser(prog: str = PROG) -> argparse.ArgumentParser:
    """The command line, for this script and for every route that wraps it."""
    built = _cli.NounParser(
        prog=prog,
        description="Build a face of this repo from its lexicon",
    )
    sub = built.add_subparsers(
        dest="phase", metavar="{" + ",".join(name for name, _ in VERBS) + "}"
    )
    _add_verbs(sub)
    _cli.mark_subparsers(sub)
    return built


def main(argv: list[str] | None = None, prog: str = PROG, api: Any = None) -> int:
    """Parse, then hand the verb to the module that answers it.

    `api` is what each verb calls per face: this package when none is named, and
    `racecar.surface.api` from racecar, which also builds the `rest` and `mcp` faces.
    """
    built = parser(prog)
    args = _cli.parse_args(built, argv)
    if args.phase is None:
        built.print_help()
        return OK
    root = args.root or find_repo_root(Path.cwd())
    dispatch: dict[str, Callable[[], int]] = {
        "create": lambda: _create.main(
            root,
            args.surface,
            args.noun,
            verb=args.verb,
            params=args.param,
            kind=args.kind,
            every=args.all,
            as_json=args.json,
            api=api,
        ),
        "check": lambda: _check.main(
            root,
            args.surface,
            noun=args.noun,
            verb=args.verb,
            against=args.against,
            as_json=args.json,
            api=api,
        ),
        "update": lambda: _update.main(
            root,
            args.surface,
            noun=args.noun,
            verb=args.verb,
            as_json=args.json,
            api=api,
        ),
        "upgrade": lambda: _upgrade.main(
            root,
            args.surface,
            noun=args.noun,
            verb=args.verb,
            as_json=args.json,
            api=api,
        ),
        "list": lambda: _list.main(
            root, args.surface, noun=args.noun, as_json=args.json, api=api
        ),
        "check-json": lambda: _check_json.main(
            root, args.surface, noun=args.noun, as_json=args.json, api=api
        ),
        "generate": lambda: _generate.main(
            root,
            args.surface,
            apply=args.apply,
            strict=args.strict,
            as_json=args.json,
            api=api,
        ),
    }
    try:
        return dispatch[args.phase]()
    except surface.SurfaceError as exc:
        print(text.refusal("surface", exc), file=sys.stderr)
        return UNMET


if __name__ == "__main__":
    sys.exit(main())
