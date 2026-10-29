#!/usr/bin/env python3
"""Check a corpus's tier shape against the topology it declares (`TOPOLOGY.md`).

One verb, `check`. A topology is authored by hand, so there is no `identify` or `derive`.

    topology check [--data D] [--meta M] [--domain X ...] [--json]

The command line over `lib/topology/`: `_walk` holds the declaration, the walk and the grade;
`_check` is the verb. `racecar.graph.topology` runs this file's `parser` and `main` under its
own name. Delivered to adopters, so it imports no `racecar` package.

Complexity: O(N), N = nodes walked (one frontmatter parse each) x C corpora joined
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.shared import _cli
from lib.shared._as_json import add_json
from lib.topology import _check


def parser(prog: str = "topology.py") -> argparse.ArgumentParser:
    """The command line, for this script and for every route that wraps it."""
    built = _cli.NounParser(
        prog=prog,
        description="racecar topology lib (is the corpus's tier shape well-formed?)",
    )
    verbs = built.add_subparsers(dest="phase")

    check = verbs.add_parser(
        "check", help="Gate --data's tier shape against --meta's declared topology"
    )
    check.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Corpus to gate (default: architecture/)",
    )
    check.add_argument(
        "--meta", type=Path, default=None, help="Declared topology (default: --data)"
    )
    check.add_argument(
        "--domain",
        action="append",
        default=None,
        help="Project onto one domain before grading (repeatable). Requires the corpus to "
        "declare `partition:`; a corpus without one is abstract and has no projections.",
    )
    for verb_parser in verbs.choices.values():
        add_json(verb_parser)
    _cli.mark_subparsers(verbs)
    return built


def main(argv: list[str] | None = None, prog: str = "topology.py") -> int:
    """Print help when called bare; otherwise run the requested verb."""
    parser_ = parser(prog)
    args = _cli.parse_args(parser_, argv)
    if args.phase is None:
        parser_.print_help()
        return 0
    return _check.main(args.data, args.meta, args.domain, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
