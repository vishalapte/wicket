#!/usr/bin/env python3
"""Mechanical check: a racecar repo owns the required repo-root doc spine.

The required-docs manifest has one home — ``docs-orchestrator/ORCHESTRATION.md``
("Required-docs manifest"). This script mechanizes the REPO-ROOT tier of it and
nothing else, so it composes with (never duplicates) the subsystem tier already
owned by ``scripts/check_subsystem_docs.py``:

  - ``check_subsystem_docs.py`` — every MAJOR SUBSYSTEM in an import-linter
    layer owns README.md + an agent-instruction file.
  - ``check_required_docs.py`` (this file) — the REPO ROOT owns the three
    top-level docs a racecar repo always has, plus the README frontmatter that
    carries the doc-graph edge and the content-blindness policy.

Required at the repo root:
  1. ``README.md`` — exists, non-empty, opens with a YAML frontmatter block
     carrying a ``pnode`` key (the doc-graph root edge; DOC_GRAPH.md).
  2. ``AGENTS.md`` — the agent baseline / resolver: exists, non-empty, carries
     >= 1 ``## `` heading. ``CLAUDE.md`` is accepted in its place, so a repo that
     has not migrated to the cross-tool name is not broken by this check.
  3. ``docs/summary/<REPO>.md`` — the racecar-llm-summary brief (SPEC.md).
     ``<REPO>`` is the repo-root basename uppercased, ``[^A-Z0-9_-]`` -> ``-``.

Advisory (info, never an error, so a repo that has not opted in stays green):
  - README frontmatter declares no ``content_blind`` policy. The docs
    orchestrator asks once and writes it (CONTENT_BLINDNESS.md, "Declaration").

Configuration (optional, ``pyproject.toml``):

    [tool.racecar.required-docs]
    brief = false          # this repo publishes no llm-summary brief

Output:
  - One line per finding: ``check_required_docs: <severity>: <message>``.
  - Summary: ``check_required_docs: OK`` (exit 0) or
    ``check_required_docs: N errors`` (exit 1). Info notes do not fail.

Usage:
    python3 <path-to>/check_required_docs.py [--root <path>]

Complexity: O(1), a fixed set of repo-root paths checked (README.md,
AGENTS.md/CLAUDE.md, docs/summary/<REPO>.md), not a treewalk over the repo's files.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from check_packaging_rules._root import find_repo_root

H2_RE = re.compile(r"^##\s+\S", re.MULTILINE)

# Precedence-ordered agent-instruction filenames. THE HOME IS
# `scripts/check_docs.py` (`AGENT_DOC_NAMES`); this is a deliberate
# mirror, for the same reason check_docs mirrors rather than imports
# check_packaging's detect_shape — the two sit in different lens directories in
# racecar's own tree, so the cross-lens import would not resolve. Keep in step with
# the home; scripts/tests/test_check_docs.py's
# `test_agent_doc_name_mirrors_agree_with_the_home` fails when they diverge.
AGENT_DOC_NAMES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def repo_slug_upper(repo_root: Path) -> str:
    """Return the brief basename: the repo-root name uppercased, non-word -> `-`."""
    base = repo_root.name.lower()
    return re.sub(r"[^a-z0-9_-]", "-", base).upper()


def load_pyproject(repo_root: Path) -> dict[str, Any]:
    """Parse the repo-root pyproject.toml (the library pyproject in every shape)."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    try:
        return tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def brief_required(pyproject: dict[str, Any]) -> bool:
    """Whether the llm-summary brief is required (default True; opt out in config)."""
    cfg = pyproject.get("tool", {}).get("racecar", {}).get("required-docs", {})
    return cfg.get("brief", True) is not False


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def frontmatter_keys(text: str) -> set[str] | None:
    """Return the top-level frontmatter key names, or None if there is no block."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return None
    keys: set[str] = set()
    for line in m.group(1).splitlines():
        key = re.match(r"^([A-Za-z_][\w-]*):", line)
        if key:
            keys.add(key.group(1))
    return keys


def frontmatter_scalar(text: str, key: str) -> str | None:
    """Return one top-level frontmatter scalar, stripped of quotes; None if absent.

    Deliberately not a YAML parse. This module reads frontmatter with a regex
    everywhere else and has no yaml dependency; a scalar on one line is all any
    caller here needs, and adding a parser for it would be a dependency bought for
    one string.
    """
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return None
    for line in m.group(1).splitlines():
        found = re.match(rf"^{re.escape(key)}:\s*(.+?)\s*$", line)
        if found:
            return found.group(1).strip("\"'")
    return None


# The positions a repo may declare in the deploy chain (docs/nomenclature/repo/README.md).
# `command` is absent on purpose: it names the origin, which supplies the chain rather
# than sitting in it, so the repo carrying the canon declares no mode at all. That makes
# the exemption structural — no checker needs to know racecar by name.
RACECAR_MODES = ("control", "mission")


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class Findings:
    """Accumulator for severity-tagged findings (errors and info notes)."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def error(self, msg: str) -> None:
        """Record an error-severity finding."""
        self.entries.append(("error", msg))

    def info(self, msg: str) -> None:
        """Record an info-severity note."""
        self.entries.append(("info", msg))

    @property
    def error_count(self) -> int:
        """Number of error-severity findings recorded."""
        return sum(1 for sev, _ in self.entries if sev == "error")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_readme(repo_root: Path, f: Findings) -> None:
    """Verify the root README exists with frontmatter, and note the CB policy."""
    readme = repo_root / "README.md"
    if not readme.is_file():
        f.error("missing: README.md (the human storefront and doc-graph root)")
        return
    text = readme.read_text(encoding="utf-8")
    if not text.strip():
        f.error("empty: README.md")
        return
    keys = frontmatter_keys(text)
    if keys is None:
        f.error(
            "README.md has no YAML frontmatter block (expected a `pnode` key; "
            "see doc-coherence/DOC_GRAPH.md)"
        )
        return
    if "pnode" not in keys:
        f.error("README.md frontmatter is missing the `pnode` key (DOC_GRAPH.md)")
    if "content_blind" not in keys:
        f.info(
            "README.md declares no `content_blind` policy; the docs orchestrator "
            "asks once and writes it (see docs-orchestrator/CONTENT_BLINDNESS.md)"
        )
    _check_racecar_mode(text, keys, f)


def _check_racecar_mode(text: str, keys: set[str], f: Findings) -> None:
    """Validate `racecar_mode` IF PRESENT. Absence is legal and says nothing.

    Validate-if-present rather than require: a repo declares its position in the deploy
    chain when it has one, and the repo that supplies the chain has none to declare. So
    absence cannot be an error without special-casing the canon repo by name, which is
    exactly the carve-out the structural exemption avoids.

    What this does catch is a typo. `racecar_mode: mision` reads as a declaration and is
    silently no declaration at all, which is worse than an empty key — a reader believes
    the position is recorded and a future reader of the fleet cannot find it.
    """
    if "racecar_mode" not in keys:
        return
    mode = frontmatter_scalar(text, "racecar_mode")
    if mode not in RACECAR_MODES:
        f.error(
            f"README.md declares `racecar_mode: {mode}`, which is not one of "
            f"{' | '.join(RACECAR_MODES)} (docs/nomenclature/repo/README.md). The key is "
            "optional, but a present one must name a real position in the chain."
        )


def check_agent_doc(repo_root: Path, f: Findings) -> None:
    """Verify the root agent-instruction file exists, is non-empty, and has an H2.

    Required by ROLE, not by filename: any of `AGENT_DOC_NAMES` satisfies it, and a
    repo holding more than one is graded on the first (the one that owns the content;
    the others point at it). A repo with none is told to write the primary name.
    """
    names = ", ".join(AGENT_DOC_NAMES)
    present = [n for n in AGENT_DOC_NAMES if (repo_root / n).is_file()]
    if not present:
        f.error(
            f"missing: {AGENT_DOC_NAMES[0]} (the agent baseline / resolver; "
            f"{names} accepted)"
        )
        return
    name = present[0]
    text = (repo_root / name).read_text(encoding="utf-8")
    if not text.strip():
        f.error(f"empty: {name}")
    elif not H2_RE.search(text):
        f.error(f"no H2 heading: {name}")


def check_brief(repo_root: Path, pyproject: dict[str, Any], f: Findings) -> None:
    """Verify the llm-summary brief exists at docs/summary/<REPO>.md when required."""
    if not brief_required(pyproject):
        f.info("llm-summary brief opted out via [tool.racecar.required-docs].brief")
        return
    brief = repo_root / "docs" / "summary" / f"{repo_slug_upper(repo_root)}.md"
    if not brief.is_file():
        f.error(
            f"missing: {brief.relative_to(repo_root).as_posix()} "
            "(the racecar-llm-summary brief; run /racecar-llm-summary)"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the required-docs check."""
    parser = argparse.ArgumentParser(
        description="Check the repo root owns README + agent doc + the llm-summary brief."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root to scan. Default: discovered via .git walk-up from CWD.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Verify the repo-root doc spine; return an exit code."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    repo_root = args.root.resolve() if args.root else find_repo_root()
    pyproject = load_pyproject(repo_root)

    f = Findings()
    check_readme(repo_root, f)
    check_agent_doc(repo_root, f)
    check_brief(repo_root, pyproject, f)
    f.info(
        "subsystem README/agent-doc coverage is check_subsystem_docs.py's; "
        "run it too (the orchestrator does)"
    )
    return emit(f)


def emit(f: Findings) -> int:
    """Print all findings and return 1 if any error was recorded, else 0."""
    for severity, msg in f.entries:
        print(f"check_required_docs: {severity}: {msg}")
    if not f.error_count:
        print("check_required_docs: OK")
        return 0
    print(f"check_required_docs: {f.error_count} errors")
    return 1


if __name__ == "__main__":
    sys.exit(main())
