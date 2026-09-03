#!/usr/bin/env python3
"""Mechanical check: every major subsystem in an import-linter layer owns README + agent doc.

Layered architecture declared via `[tool.importlinter]` defines the subsystems
the project considers first-class. This script verifies each "major" subsystem
inside one of those layers owns a `README.md` (audience: developer landing on
the subsystem) and an agent-instruction file (audience: an AI agent working in
it) — `AGENTS.md`, or `CLAUDE.md` in a repo that has not migrated. The pair is a
role requirement, not a filename allowlist; the accepted names and their
precedence have one home in `check_docs.AGENT_DOC_NAMES`.

Discovery:
  - `REPO_ROOT` via `.git` walk-up (matches `check_docs.py`).
  - `pyproject.toml` `[tool.importlinter].contracts` is parsed to collect every
    dotted package referenced as a container, layer, importer, imported, module,
    or source/forbidden module. Each dotted package is resolved to a directory
    by checking `<root>/<pkg>`, `<root>/src/<pkg>`, `<root>/server/<pkg>`.
  - From each resolved root, walk recursively. A directory is "major" iff it
    contains at least one non-excluded subdirectory OR its direct-child source
    files sum to `loc_threshold` non-blank lines (default 1000).

Validation per major subsystem:
  - `<subsystem>/README.md` exists, non-empty after strip, contains >= 1 `## ` heading.
  - `<subsystem>/AGENTS.md` (or `CLAUDE.md`) exists, non-empty after strip, contains
    >= 1 `## ` heading.

Configuration (optional, `pyproject.toml`):

    [tool.racecar.subsystem-docs]
    loc_threshold = 1000
    exclude = ["tests", "migrations", "__pycache__"]   # added to defaults

Behavior when nothing to check:
  - No `pyproject.toml`, no `[tool.importlinter]`, or zero resolvable packages:
    one info line, exit 0. The check is silent for repos that don't use
    import-linter; nothing to validate against.

Output:
  - One line per finding: `check_subsystem_docs: <severity>: <message>`.
  - Summary: `check_subsystem_docs: OK` (exit 0) or
    `check_subsystem_docs: N errors` (exit 1).

Usage:
    python3 <path-to>/check_subsystem_docs.py [--root <path>]

Complexity: O(n), n = directories under a resolved package root; contains_source
memoizes per directory, so each directory's own entries are read exactly once
across the whole walk regardless of how many ancestors ask about it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from check_docs import AGENT_DOC_NAMES, agent_doc, load_project_pyproject
from check_packaging_rules._root import find_repo_root

DEFAULT_LOC_THRESHOLD = 1000
DEFAULT_EXCLUDE: tuple[str, ...] = ("tests", "migrations", "__pycache__")
SOURCE_EXTS: tuple[str, ...] = (".py",)
H2_RE = re.compile(r"^##\s+\S", re.MULTILINE)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def read_pyproject(repo_root: Path) -> dict[str, Any]:
    """Locate and parse the project's pyproject via the shared two-home probe.

    Delegates to :func:`check_docs.load_project_pyproject`, which reads the root
    ``pyproject.toml`` (the library pyproject in every shape). This makes the
    ``[tool.importlinter]`` contracts and ``[tool.racecar.subsystem-docs]`` config
    discoverable for all shapes.
    """
    return load_project_pyproject(repo_root)


def importlinter_packages(pyproject: dict[str, Any]) -> list[str]:
    """Collect every dotted package referenced as container / layer / module in any contract.

    Accepts the canonical `[tool.importlinter]` location plus a bare
    `[importlinter]` fallback some projects use.
    """
    candidates: list[str] = []
    for section in (
        pyproject.get("tool", {}).get("importlinter", {}),
        pyproject.get("importlinter", {}),
    ):
        if not isinstance(section, dict):
            continue
        contracts = section.get("contracts", [])
        if not isinstance(contracts, list):
            continue
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            for key in (
                "containers",
                "layers",
                "modules",
                "include",
                "source_modules",
                "forbidden_modules",
                "importer",
                "imported",
            ):
                val = contract.get(key)
                if isinstance(val, str):
                    candidates.append(val)
                elif isinstance(val, list):
                    candidates.extend(s for s in val if isinstance(s, str))
    return list(dict.fromkeys(candidates))  # dedupe, preserve order


def resolve_package_dirs(repo_root: Path, package: str) -> list[Path]:
    """Resolve a dotted package name to candidate directories.

    Returns every matching directory across common src-tree shapes. A package
    listed twice in different shapes returns both (rare, harmless).
    """
    parts = package.split(".")
    rels = [Path(*parts), Path("src", *parts), Path("server", *parts)]
    return [repo_root / rel for rel in rels if (repo_root / rel).is_dir()]


# ---------------------------------------------------------------------------
# "Major" filter
# ---------------------------------------------------------------------------


def count_direct_loc(directory: Path) -> int:
    """Sum non-blank source LOC in direct-child files of `directory` only.

    Recursion happens via `walk_major`; counting recursively here would
    double-count and is the wrong scope for the "this directory's own size"
    signal.
    """
    total = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_file() or entry.suffix not in SOURCE_EXTS:
            continue
        try:
            text = entry.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        total += sum(1 for line in text.splitlines() if line.strip())
    return total


def has_nonexcluded_subdirs(directory: Path, exclude: frozenset[str]) -> bool:
    """True if `directory` has at least one non-excluded, non-hidden subdirectory."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name in exclude or entry.name.startswith("."):
            continue
        return True
    return False


def contains_source(
    directory: Path,
    exclude: frozenset[str],
    cache: dict[Path, bool] | None = None,
) -> bool:
    """True if `directory`'s subtree holds at least one non-excluded source file.

    A subsystem is a unit of *code*: a directory whose subtree carries no source
    file (a `templates/` tree of HTML, a `static/` tree of assets, a content tree
    of markdown) is not a subsystem and owes no README/CLAUDE, however many
    subdirectories it has. This gates the `has subdirs -> major` rule so the check
    polices packages, not the asset and content directories that live beside them.

    Memoized in `cache`, keyed by directory (#72): `walk_major` asks this question
    once per directory it visits, top-down, and an uncached call rewalks the whole
    remaining subtree from every ancestor that asks -- O(n^2) on a deep tree. This
    does one iterative post-order pass per not-yet-cached directory: each
    directory's own entries are read exactly once, and its answer is derived from
    its already-computed children rather than a fresh descent.
    """
    if cache is None:
        cache = {}
    if directory in cache:
        return cache[directory]

    pending = [directory]
    order: list[Path] = []
    subdirs_of: dict[Path, list[Path]] = {}
    direct_source: dict[Path, bool] = {}
    while pending:
        d = pending.pop()
        if d in cache or d in direct_source:
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            entries = []
        has_source = False
        children: list[Path] = []
        for entry in entries:
            if entry.is_dir():
                if entry.name not in exclude and not entry.name.startswith("."):
                    children.append(entry)
            elif entry.suffix in SOURCE_EXTS:
                has_source = True
        direct_source[d] = has_source
        subdirs_of[d] = children
        order.append(d)
        pending.extend(c for c in children if c not in cache)

    # Every child is discovered (and appended to `order`) after its parent, so
    # walking `order` in reverse computes each directory from already-known
    # children -- true post-order without recursion.
    for d in reversed(order):
        cache[d] = direct_source[d] or any(cache.get(c, False) for c in subdirs_of[d])
    return cache[directory]


def is_major(
    directory: Path,
    loc_threshold: int,
    exclude: frozenset[str],
    cache: dict[Path, bool] | None = None,
) -> bool:
    """True if `directory` is a major subsystem: a code directory that either has
    subdirs or meets the LOC bar. A directory with no source anywhere in its
    subtree is never major -- it is content, not a subsystem."""
    if not contains_source(directory, exclude, cache):
        return False
    if has_nonexcluded_subdirs(directory, exclude):
        return True
    return count_direct_loc(directory) >= loc_threshold


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------


def walk_major(
    root: Path,
    loc_threshold: int,
    exclude: frozenset[str],
    seen: set[Path],
) -> list[Path]:
    """Recursively collect major directories at and under `root`, in walk order."""
    results: list[Path] = []
    source_cache: dict[Path, bool] = {}
    stack: list[Path] = [root]
    while stack:
        d = stack.pop(0)
        if d in seen:
            continue
        if d.name in exclude or d.name.startswith("."):
            continue
        if not d.is_dir():
            continue
        seen.add(d)
        if is_major(d, loc_threshold, exclude, source_cache):
            results.append(d)
        try:
            children = sorted(
                (c for c in d.iterdir() if c.is_dir()),
                key=lambda p: p.name,
            )
        except OSError:
            children = []
        stack.extend(children)
    return results


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_doc(path: Path) -> str | None:
    """Return one-line error message if invalid, None if OK."""
    if not path.is_file():
        return f"missing: {path}"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"unreadable: {path} ({exc})"
    if not text.strip():
        return f"empty: {path}"
    if not H2_RE.search(text):
        return f"no H2 heading: {path}"
    return None


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
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the subsystem-docs check."""
    parser = argparse.ArgumentParser(
        description="Check every major subsystem in an import-linter layer owns README + CLAUDE."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root to scan. Default: discovered via .git walk-up from CWD.",
    )
    return parser.parse_args(argv)


def load_config(pyproject: dict[str, Any]) -> tuple[int, frozenset[str]]:
    """Read the LOC threshold and exclude set from the subsystem-docs config."""
    cfg = pyproject.get("tool", {}).get("racecar", {}).get("subsystem-docs", {})
    threshold = cfg.get("loc_threshold", DEFAULT_LOC_THRESHOLD)
    if not isinstance(threshold, int) or threshold <= 0:
        threshold = DEFAULT_LOC_THRESHOLD
    extra = cfg.get("exclude", [])
    if not isinstance(extra, list):
        extra = []
    exclude = frozenset(DEFAULT_EXCLUDE) | frozenset(
        s for s in extra if isinstance(s, str)
    )
    return threshold, exclude


def main(argv: list[str] | None = None) -> int:
    """Verify every major subsystem owns README + CLAUDE; return an exit code."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    f = Findings()

    repo_root = args.root.resolve() if args.root else find_repo_root()
    pyproject = read_pyproject(repo_root)
    packages = importlinter_packages(pyproject)

    if not packages:
        f.info("no import-linter contracts found; nothing to validate")
        return emit(f)

    loc_threshold, exclude = load_config(pyproject)

    seen: set[Path] = set()
    subsystems: list[Path] = []
    for pkg in packages:
        for dir_path in resolve_package_dirs(repo_root, pkg):
            subsystems.extend(walk_major(dir_path, loc_threshold, exclude, seen))

    if not subsystems:
        f.info(
            "import-linter contracts present but no resolvable major subsystems "
            "found; nothing to validate"
        )
        return emit(f)

    for subsystem in subsystems:
        err = validate_doc(subsystem / "README.md")
        if err:
            f.error(err)
        # The agent doc is required by ROLE, not by filename: whichever of
        # AGENT_DOC_NAMES the subsystem carries is the one graded. A subsystem with
        # none gets the primary name in the message, so the fix is unambiguous.
        agent = agent_doc(subsystem) or subsystem / AGENT_DOC_NAMES[0]
        err = validate_doc(agent)
        if err:
            f.error(err)

    return emit(f)


def emit(f: Findings) -> int:
    """Print all findings and return 1 if any error was recorded, else 0."""
    for severity, msg in f.entries:
        print(f"check_subsystem_docs: {severity}: {msg}")
    if not f.error_count:
        print("check_subsystem_docs: OK")
        return 0
    print(f"check_subsystem_docs: {f.error_count} errors")
    return 1


if __name__ == "__main__":
    sys.exit(main())
