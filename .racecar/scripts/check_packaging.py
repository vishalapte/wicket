"""Validate project files against the racecar packaging canon.

See arch-python/PACKAGING.md for the canon this script enforces.

Shape detection (per PACKAGING.md §"Scope"):

    src           root pyproject.toml + src/<pkg>/ (no server/)
    src+server   root pyproject.toml + src/<pkg>/ + server/manage.py
    server         root pyproject.toml + server/manage.py (standalone Django, no src/)

Each shape has a "library pyproject" (the one with [project], canonical
[tool.*] configs, [dependency-groups].dev) and -- for src+server -- a
"server pyproject" (PEP 735 [dependency-groups].runtime only, no [project]).

Findings have two severities:

  Blocker  -- the file or rule is broken in a way that violates the canon
  Finding  -- a recommendation; passes by default, fails with --strict or
              RACECAR_STRICT=true

Exit code: 0 on no Blockers; 1 if any Blocker is found (or any Finding with
--strict or RACECAR_STRICT=true). Output is line-oriented and machine-greppable.

Stdlib only (tomllib + re + pathlib + dataclasses).

Usage:
    python check_packaging.py                  # validate current directory
    python check_packaging.py --root <path>    # validate elsewhere
    python check_packaging.py --strict         # treat Findings as Blockers

Complexity: O(k)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The audits live in `lib.packaging`; this file is the runnable entry and re-exports the
# public names (`from check_packaging import detect_shape` in check_upward_imports.py,
# check_surface_orchestration.py, and the tests).
from lib.packaging import Finding, Shape, detect_shape, run_all
from lib.shared._switches import strict

# `__all__` marks the re-exports as intentional.
__all__ = [
    "Finding",
    "Shape",
    "blocking",
    "detect_shape",
    "main",
    "parser",
    "render",
    "run_all",
]


def parser() -> argparse.ArgumentParser:
    """Build the argument parser for the packaging checker CLI."""
    p = argparse.ArgumentParser(
        description=(
            "Validate project files against the racecar packaging canon. "
            "See arch-python/PACKAGING.md."
        )
    )
    p.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root to validate (default: cwd).",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Treat Findings as Blockers (non-zero exit on any issue).",
    )
    return p


def render(records: list[Finding]) -> str:
    """The findings report `main` prints, built from `run_all`; empty when there is none."""
    if not records:
        return ""
    blockers = sum(1 for f in records if f.severity == "Blocker")
    other = len(records) - blockers
    lines = [
        f"packaging: {blockers} blocker(s), {other} finding(s)",
        f"  {'SEVERITY':7s}  {'FILE':32s}  {'RULE':42s}  MESSAGE",
    ]
    lines += [f.render() for f in records]
    return "\n".join(lines)


def blocking(records: list[Finding]) -> int:
    """How many of `records` are Blockers. A Blocker fails the check; a Finding does not."""
    return sum(1 for f in records if f.severity == "Blocker")


def main() -> int:
    """Run every packaging rule and print the audit; exit non-zero on blockers."""
    args = parser().parse_args()
    records = run_all(args.root.resolve())
    if not records:
        print("packaging: OK")
        return 0
    print(render(records))
    if blocking(records) or args.strict or strict():
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
