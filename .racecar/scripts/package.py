#!/usr/bin/env python3
"""The package: the artifact racecar builds, and the rail a commit to it goes through.

    package.py commit    make commits, through a runbook
    package.py audit     check the commits already made
    package.py create    scaffold the library and/or the server (racecar's route only)

Delivered to adopters, so it imports no `racecar` package. `create` needs racecar's templates
and generator, so here it says so and exits 2; `python -m racecar.package create` runs it.

The command line over `lib/package/`, one module per verb (`_commit`, `_audit`, `_create`),
each with a `run` that returns the verb's record and a `main`. `python -m racecar.package`
builds its parser with `parser()` and hands its argv to `main()`.

Complexity: O(C) in the commits `audit` reads; otherwise O(1) plus a runbook's run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.package import _audit, _commit, _create
from lib.package._error import OK
from lib.shared import _cli
from lib.shared._as_json import add_json, run_json
from lib.shared._root import find_repo_root

#: `(verb, one-line summary)` per verb, the order the help lists them in.
VERBS: tuple[tuple[str, str], ...] = (
    ("create", "Scaffold the library and/or the server, composed by flags"),
    ("commit", "Make commits, through a runbook"),
    ("audit", "Check the commits already made against COMMITS.md"),
)


def parser(prog: str = "package.py") -> argparse.ArgumentParser:
    """The command line, for this script and for every route that wraps it.

    The surface flags mean nothing without `--django`; `create` reports what it would need
    and skips rather than the parser refusing. The REST face is `--rest`, never `--api`.
    There is no `--web`, because nothing scaffolds one.
    """
    helps = dict(VERBS)
    built = _cli.NounParser(
        prog=prog,
        description="racecar package lib (the artifact: library, server, surfaces)",
    )
    built.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo to act on. Default: discovered via .git walk-up from CWD.",
    )
    phases = built.add_subparsers(dest="phase")

    c = phases.add_parser("create", help=helps["create"])
    c.add_argument("--python", action="store_true", help="The src/<pkg> library")
    c.add_argument("--django", action="store_true", help="The server shell")
    c.add_argument(
        "--rest", action="store_true", help="…the REST surface (with --django)"
    )
    c.add_argument(
        "--mcp", action="store_true", help="…the MCP surface (with --django)"
    )
    c.add_argument("--auth", action="store_true", help="…the Authorization Server")
    c.add_argument(
        "--name", default=None, help="Distribution name (required by --python)"
    )
    c.add_argument("--pkg", default=None, help="Import package name")
    c.add_argument("--issuer", default=None, help="Public AS issuer URL (with --auth)")
    c.add_argument("--out", type=Path, default=None, help="Output root")
    add_json(c)

    m = phases.add_parser("commit", help=helps["commit"])
    what = m.add_mutually_exclusive_group(required=True)
    what.add_argument("--plan", type=Path, help="The commits to make, as JSON")
    what.add_argument(
        "-m", "--message", help="Commit what is staged, with this message"
    )
    m.add_argument(
        "--dry-run", action="store_true", help="Try every commit, land nothing"
    )
    m.add_argument(
        "--no-edit", action="store_true", help="Commit without opening $EDITOR"
    )
    add_json(m)

    a = phases.add_parser("audit", help=helps["audit"])
    # The default range is history since the repo began enforcing its conventions.
    rng = a.add_mutually_exclusive_group()
    rng.add_argument(
        "--all", dest="audit_all", action="store_true", help="The whole log"
    )
    rng.add_argument("--since", default=None, help="Commits after this ref")
    a.add_argument("--strict", action="store_true", help="Exit 1 on any finding")
    a.add_argument("--json", action="store_true", help="Print the findings as JSON")
    _cli.mark_subparsers(phases)
    return built


def main(argv: list[str] | None = None, prog: str = "package.py") -> int:
    """Print help when called bare (CLI.md B1); otherwise run the requested verb."""
    parser_ = parser(prog)
    args = _cli.parse_args(parser_, argv)
    if args.phase is None:
        parser_.print_help()
        return OK
    root = args.root.resolve() if args.root else find_repo_root()
    if args.phase == "create":
        return _create.main()
    if args.phase == "audit":
        return _audit.main(
            root,
            audit_all=args.audit_all,
            since=args.since,
            strict=args.strict,
            as_json=args.json,
        )
    return run_json(
        args.json,
        lambda: _commit.main(
            root,
            plan_file=args.plan,
            message=args.message,
            dry_run=args.dry_run,
            no_edit=args.no_edit,
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
