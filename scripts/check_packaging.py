"""Validate project files against the racecar packaging canon.

See arch-coherence/PACKAGING.md for the canon this script enforces.

Shape detection (per PACKAGING.md §"Scope"):

    src           root pyproject.toml + src/<pkg>/
    pypkg         pypkg/src/pyproject.toml (no djapp/)
    pypkg+djapp   pypkg/src/pyproject.toml + djapp/pyproject.toml
    djapp         root pyproject.toml (no pypkg/), djapp/manage.py present

Each shape has a "library pyproject" (the one with [project], canonical
[tool.*] configs, [dependency-groups].dev) and -- for pypkg+djapp -- a
"djapp pyproject" (PEP 735 [dependency-groups].runtime only, no [project]).

Findings have two severities:

  Blocker  -- the file or rule is broken in a way that violates the canon
  Finding  -- a recommendation; passes by default, fails with --strict

Exit code: 0 on no Blockers; 1 if any Blocker is found (or any Finding with
--strict). Output is line-oriented and machine-greppable.

This script is pure-stdlib by design (tomllib + re + pathlib + dataclasses).

Usage:
    python check_packaging.py                  # validate current directory
    python check_packaging.py --root <path>    # validate elsewhere
    python check_packaging.py --strict         # treat Findings as Blockers
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The audits live in the sibling check_packaging_rules package; this file is the
# thin runnable entry. The public names are re-exported here (see __all__) so
# importers that do `from check_packaging import detect_shape`
# (check_upward_imports.py, check_face_orchestration.py, and the tests) keep working.
from check_packaging_rules import Finding, Shape, detect_shape, run_all

# detect_shape / Shape / Finding are imported solely to re-export them; run_all is
# both used by main() and re-exported. __all__ names the public surface and marks
# the re-exports as intentional.
__all__ = ["Finding", "Shape", "detect_shape", "main", "parser", "run_all"]


def parser() -> argparse.ArgumentParser:
    """Build the argument parser for the packaging checker CLI."""
    p = argparse.ArgumentParser(
        description=(
            "Validate project files against the racecar packaging canon. "
            "See arch-coherence/PACKAGING.md."
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


def main() -> int:
    """Run every packaging rule and print the audit; exit non-zero on blockers."""
    args = parser().parse_args()
    findings = run_all(args.root.resolve())
    if not findings:
        print("packaging: OK")
        return 0
    blockers = sum(1 for f in findings if f.severity == "Blocker")
    other = len(findings) - blockers
    print(f"packaging: {blockers} blocker(s), {other} finding(s)")
    print(f"  {'SEVERITY':7s}  {'FILE':32s}  {'RULE':42s}  MESSAGE")
    for f in findings:
        print(f.render())
    if blockers or args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
