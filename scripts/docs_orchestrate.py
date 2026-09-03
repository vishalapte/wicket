#!/usr/bin/env python3
"""The docs orchestrator: run the deterministic doc pipeline, in dependency order.

Single entry point for the mechanical backbone of ``/racecar-docs``
(``docs-orchestrator/ORCHESTRATION.md``). It COMPOSES the existing checkers —
it re-implements none of them — and runs them as staged gates in the order the
orchestration sequence prescribes:

    1. manifest        check_required_docs.py, check_subsystem_docs.py
    2. content-blind   check_content_blind.py
    3. coherence       check_docs.py, check_doc_graph.py, check_file_placement.py,
                       check_nomenclature.py
    4. brief           check_brief.py

The GENERATIVE steps of the sequence (stub a missing doc, regenerate the
machine spine via the llm-summary and surface-doc generators) require judgment
and are the agent's to drive per ORCHESTRATION.md; this backbone is the
deterministic report-and-gate half (R-03: the gate is a script; the model
authors). It collects every stage, prints one consolidated report, and returns
a single exit code (1 if any gate failed).

Checker resolution is location-agnostic so the orchestrator runs both in
racecar's own tree (checkers under the lens dirs) and in an adopter repo
(checkers synced flat into ``<root>/scripts``).

Output:
  - A per-stage report; each checker's own output is passed through.
  - Summary: ``docs_orchestrate: OK`` (exit 0) or
    ``docs_orchestrate: N gate(s) failed`` (exit 1).

Usage:
    python3 <path-to>/docs_orchestrate.py [--root <path>] [--list]

Complexity: O(k) where k = total checkers across PIPELINE stages; each stage's own cost
is that checker script's, not this driver's.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from check_packaging_rules._root import find_repo_root

# (stage, [checker filenames]) in dependency order. Each name is resolved
# against the search path; a name that resolves nowhere is reported skipped.
PIPELINE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("manifest", ("check_required_docs.py", "check_subsystem_docs.py")),
    ("content-blind", ("check_content_blind.py",)),
    (
        "coherence",
        (
            "check_docs.py",
            "check_doc_graph.py",
            "check_file_placement.py",
            "check_nomenclature.py",
        ),
    ),
    ("brief", ("check_brief.py",)),
)


def search_dirs(repo_root: Path) -> list[Path]:
    """Directories to resolve a checker filename against, most-specific first."""
    here = Path(__file__).resolve().parent
    candidates = [here, repo_root / "scripts"]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for d in candidates:
        if d not in seen and d.is_dir():
            seen.add(d)
            ordered.append(d)
    return ordered


def resolve_checker(name: str, dirs: list[Path]) -> Path | None:
    """Return the first existing `name` across `dirs`, or None."""
    for d in dirs:
        candidate = d / name
        if candidate.is_file():
            return candidate
    return None


def run_checker(script: Path, repo_root: Path) -> tuple[int, str]:
    """Run a checker from `repo_root` (it self-discovers via .git); return (rc, out)."""
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the orchestrator."""
    parser = argparse.ArgumentParser(
        description="Run the deterministic docs pipeline (compose the racecar checkers)."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root. Default: discovered via .git walk-up from CWD.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the pipeline stages and the checker each composes, then exit.",
    )
    return parser.parse_args(argv)


def print_pipeline() -> int:
    """Print the pipeline stages and their composed checkers; return 0."""
    print("docs_orchestrate pipeline (dependency order):")
    for stage, checkers in PIPELINE:
        print(f"  {stage}: {', '.join(checkers)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the composed docs pipeline and return a single exit code."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.list:
        return print_pipeline()

    repo_root = args.root.resolve() if args.root else find_repo_root()
    dirs = search_dirs(repo_root)
    failures = 0

    for stage, checkers in PIPELINE:
        print(f"\n=== stage: {stage} ===")
        for name in checkers:
            script = resolve_checker(name, dirs)
            if script is None:
                print(f"  {name}: skipped — not found on the search path")
                continue
            rc, out = run_checker(script, repo_root)
            print(out.rstrip("\n"))
            if rc:
                failures += 1

    print()
    if not failures:
        print("docs_orchestrate: OK")
        return 0
    print(f"docs_orchestrate: {failures} gate(s) failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
