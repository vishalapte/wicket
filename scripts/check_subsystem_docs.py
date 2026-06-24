#!/usr/bin/env python3
"""Mechanical check: every major subsystem in an import-linter layer owns README + CLAUDE.

Layered architecture declared via `[tool.importlinter]` defines the subsystems
the project considers first-class. This script verifies each "major" subsystem
inside one of those layers owns a `README.md` (audience: developer landing on
the subsystem) and a `CLAUDE.md` (audience: an AI agent working in it).

Discovery:
  - `REPO_ROOT` via `.git` walk-up (matches `check_docs.py`).
  - `pyproject.toml` `[tool.importlinter].contracts` is parsed to collect every
    dotted package referenced as a container, layer, importer, imported, module,
    or source/forbidden module. Each dotted package is resolved to a directory
    by checking `<root>/<pkg>`, `<root>/src/<pkg>`, `<root>/pypkg/src/<pkg>`.
  - From each resolved root, walk recursively. A directory is "major" iff it
    contains at least one non-excluded subdirectory OR its direct-child source
    files sum to `loc_threshold` non-blank lines (default 1000).

Validation per major subsystem:
  - `<subsystem>/README.md` exists, non-empty after strip, contains >= 1 `## ` heading.
  - `<subsystem>/CLAUDE.md` exists, non-empty after strip, contains >= 1 `## ` heading.

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
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from check_docs import load_project_pyproject

DEFAULT_LOC_THRESHOLD = 1000
DEFAULT_EXCLUDE: tuple[str, ...] = ("tests", "migrations", "__pycache__")
SOURCE_EXTS: tuple[str, ...] = (".py",)
H2_RE = re.compile(r"^##\s+\S", re.MULTILINE)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_repo_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor of `start` containing a `.git` directory."""
    start = start or Path.cwd()
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return start


def read_pyproject(repo_root: Path) -> dict:
    """Locate and parse the project's pyproject via the shared two-home probe.

    Delegates to :func:`check_docs.load_project_pyproject`, which reads the root
    ``pyproject.toml`` (shapes ``src`` / ``djapp``) or, failing that, the library
    pyproject at ``pypkg/src/pyproject.toml`` (shapes ``pypkg`` /
    ``pypkg+djapp``). This makes the ``[tool.importlinter]`` contracts and
    ``[tool.racecar.subsystem-docs]`` config discoverable for all shapes, not
    just the single-root ones.
    """
    return load_project_pyproject(repo_root)


def importlinter_packages(pyproject: dict) -> list[str]:
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
    rels = [Path(*parts), Path("src", *parts), Path("pypkg", "src", *parts)]
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


def is_major(directory: Path, loc_threshold: int, exclude: frozenset[str]) -> bool:
    """True if `directory` is a major subsystem: has subdirs or meets the LOC bar."""
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
        if is_major(d, loc_threshold, exclude):
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
    except OSError as exc:
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


def load_config(pyproject: dict) -> tuple[int, frozenset[str]]:
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
        for doc_name in ("README.md", "CLAUDE.md"):
            err = validate_doc(subsystem / doc_name)
            if err:
                f.error(err)

    return emit(f)


def emit(f: Findings) -> int:
    """Print all findings and return 1 if any error was recorded, else 0."""
    for severity, msg in f.entries:
        print(f"check_subsystem_docs: {severity}: {msg}")
    if f.error_count == 0:
        print("check_subsystem_docs: OK")
        return 0
    print(f"check_subsystem_docs: {f.error_count} errors")
    return 1


if __name__ == "__main__":
    sys.exit(main())
