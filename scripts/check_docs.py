#!/usr/bin/env python3
"""Mechanical pre-pass check for markdown docs in any repo.

Implements the checks `doc-coherence/README.md` prescribes as its mechanical
pre-pass. Nothing in this script is repo-specific — it discovers the repo
root and layout at runtime, so the same file works for racecar and for any
consumer that adopts the doc-coherence standard.

Checks:

  1. Every `[text](path)` in a .md file resolves — path exists, and every
     `#anchor` matches a heading slug in the target .md file.
  2. Every `FILENAME.md §N` cited in a non-markdown file (scripts, Makefile,
     *.toml, *.yaml) points to a heading at that number in the target file.
     An optional directory prefix (`<dir>/FILENAME.md §N`) disambiguates
     when the same basename lives in more than one directory.
  3. Vocabulary identity — every line of the form
     ``<Class> values are literal: **<literal>**`` agrees with every other
     instance of the same ``<Class>`` across the repo's markdown. Catches
     drift between sibling READMEs that each repeat the same output
     vocabulary inline (e.g. severity / verdict literals).

Matches inside inline code spans (single backticks) and triple-backtick
fenced code blocks are ignored — they are literals, not links.

Discovery:
  - REPO_ROOT is the nearest ancestor of the current working directory
    containing a `.git` entry (same rule `git` itself uses). Falls back to
    CWD if no `.git` is found — so the script also runs against plain
    directories that aren't git repos.
  - DOC_SEARCH_DIRS is REPO_ROOT plus every top-level non-hidden directory
    under it, sorted alphabetically for deterministic first-match behavior.
  - Hidden directories (names starting with `.`) are skipped everywhere.

Exit 0 if clean, 1 if any drift is found.

Usage:
    python3 <path-to>/check_docs.py
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


def _find_repo_root() -> Path:
    start = Path.cwd()
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return start


REPO_ROOT = _find_repo_root()


# Where the project's pyproject.toml lives, in lookup order. Shapes `src` and
# `djapp` keep it at the repo root; shapes `pypkg` and `pypkg+djapp` have no
# root pyproject and put the library pyproject at `pypkg/src/pyproject.toml`.
# First existing file wins. This is a minimal, self-contained probe of the two
# known homes — not full shape detection (that lives in arch-coherence's
# check_packaging.py, which check_docs deliberately does not import: the two
# scripts sit in different lens directories in racecar's own tree, so the
# cross-lens import would not resolve when check_docs runs there).
PYPROJECT_CANDIDATES = ("pyproject.toml", "pypkg/src/pyproject.toml")


def project_pyproject_path(repo_root: Path | None = None) -> Path | None:
    """Return the project's pyproject.toml path, or None if neither home exists.

    Probes the two known homes in :data:`PYPROJECT_CANDIDATES` order: the repo
    root (shapes ``src`` / ``djapp``), else ``pypkg/src/pyproject.toml`` (shapes
    ``pypkg`` / ``pypkg+djapp``). First existing file wins.

    Shared, reusable across the doc-coherence checkers (and importable by
    sibling scripts) so the two-home probe lives in exactly one place.
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    for candidate in PYPROJECT_CANDIDATES:
        pyproject = root / candidate
        if pyproject.is_file():
            return pyproject
    return None


def load_project_pyproject(repo_root: Path | None = None) -> dict:
    """Parse and return the project's pyproject.toml as a dict.

    Locates the file via :func:`project_pyproject_path` (the shared two-home
    probe). Returns ``{}`` when no pyproject exists or it cannot be parsed.
    """
    pyproject = project_pyproject_path(repo_root)
    if pyproject is None:
        return {}
    try:
        return tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def ignore_patterns(repo_root: Path | None = None) -> tuple[re.Pattern[str], ...]:
    """Repo-relative regex patterns to skip.

    Honors ``[tool.pylint.MASTER].ignore-paths`` in the project's
    ``pyproject.toml`` so the script doesn't drown the report in vendored
    third-party drift the project has already declared out-of-scope. Reads the
    root ``pyproject.toml`` if present, else falls back to the library pyproject
    at ``pypkg/src/pyproject.toml`` (shapes ``pypkg`` / ``pypkg+djapp``) via the
    shared two-home probe. No pyproject / no key -> empty tuple.

    Shared, reusable across the doc-coherence checkers so the ignore-paths
    reader lives in exactly one place.
    """
    data = load_project_pyproject(repo_root)
    if not data:
        return ()
    raw = (
        data.get("tool", {}).get("pylint", {}).get("MASTER", {}).get("ignore-paths", [])
    )
    return tuple(re.compile(p) for p in raw if isinstance(p, str))


IGNORE_PATTERNS = ignore_patterns()

# Search order when a `FILENAME.md §N` citation carries no directory prefix.
# First match wins; cite with a prefix (`<dir>/FILENAME.md §N`) to target a
# specific directory when the basename is not unique.
DOC_SEARCH_DIRS = tuple(
    [REPO_ROOT]
    + sorted(
        (d for d in REPO_ROOT.iterdir() if d.is_dir() and not d.name.startswith(".")),
        key=lambda p: p.name,
    )
)


def _is_hidden(path: Path) -> bool:
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.parts)


def _is_ignored(path: Path) -> bool:
    if not IGNORE_PATTERNS:
        return False
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return False
    return any(p.search(rel) for p in IGNORE_PATTERNS)


def _heading_slugs(text: str) -> set[str]:
    """Return the GitHub-style heading slugs for a markdown document."""
    slugs: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^#+\s+(.+?)\s*$", line)
        if not m:
            continue
        h = m.group(1).lower()
        h = re.sub(r"[`'\"]", "", h)
        h = re.sub(r"[^\w\s-]", "", h)
        slugs.add(re.sub(r"\s+", "-", h).strip("-"))
    return slugs


def _section_numbers(text: str) -> set[str]:
    """Return the top-level section numbers (e.g. {'1','2'} from '## 1. Foo')."""
    return {
        m.group(1)
        for line in text.splitlines()
        if (m := re.match(r"^##\s+(\d+)\.", line))
    }


def _check_links(md_path: Path) -> list[str]:
    errors: list[str] = []
    text = md_path.read_text(encoding="utf-8")
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", line):
            target = m.group(2)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            # skip matches inside inline code spans (odd backtick parity before match)
            if line[: m.start()].count("`") % 2 == 1:
                continue
            path_part, _, anchor = target.partition("#")
            target_file = (
                (md_path.parent / path_part).resolve() if path_part else md_path
            )
            if path_part and not target_file.exists():
                errors.append(f"{md_path}:{lineno}: broken link — {target}")
                continue
            if anchor and target_file.suffix == ".md":
                slugs = _heading_slugs(target_file.read_text(encoding="utf-8"))
                if anchor not in slugs:
                    errors.append(f"{md_path}:{lineno}: missing anchor — {target}")
    return errors


def _find_doc(fname: str) -> Path | None:
    for d in DOC_SEARCH_DIRS:
        candidate = d / fname
        if candidate.is_file():
            return candidate
    return None


def _check_section_citations(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError, OSError):
        return errors
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in re.finditer(r"(?:([\w-]+)/)?([A-Z_]+\.md)\s*§\s*(\d+)", line):
            prefix, fname, num = m.group(1), m.group(2), m.group(3)
            if prefix:
                target: Path | None = REPO_ROOT / prefix / fname
                if not target.is_file():
                    errors.append(
                        f"{path}:{lineno}: cites missing file — {prefix}/{fname} §{num}"
                    )
                    continue
            else:
                target = _find_doc(fname)
                if target is None:
                    errors.append(
                        f"{path}:{lineno}: cites missing file — {fname} §{num}"
                    )
                    continue
            nums = _section_numbers(target.read_text(encoding="utf-8"))
            if num not in nums:
                label = f"{prefix}/{fname}" if prefix else fname
                errors.append(
                    f"{path}:{lineno}: {label} §{num} stale — target has sections {sorted(nums)}"
                )
    return errors


VOCAB_LINE = re.compile(r"(\b[A-Z][a-z]+)\s+values\s+are\s+literal:\s*\*\*([^*]+)\*\*")


def _check_vocabulary_identity(md_paths: list[Path]) -> list[str]:
    """Every `<Class> values are literal: **<literal>**` must agree across the repo.

    Catches drift between sibling READMEs that each repeat the same output
    vocabulary inline. If only zero or one source declares a class, nothing
    to check — the rule is identity, not existence.
    """
    sightings: dict[str, list[tuple[str, Path, int]]] = {}
    for md_path in md_paths:
        try:
            text = md_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        in_fence = False
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for m in VOCAB_LINE.finditer(line):
                klass, literal = m.group(1), m.group(2).strip()
                sightings.setdefault(klass, []).append((literal, md_path, lineno))

    errors: list[str] = []
    for klass, occurrences in sightings.items():
        literals = {lit for lit, _, _ in occurrences}
        if len(literals) <= 1:
            continue
        errors.append(
            f"vocabulary drift: {klass} declared with {len(literals)} different literals:"
        )
        for lit, path, lineno in occurrences:
            errors.append(f"  {path}:{lineno}: **{lit}**")
    return errors


def main() -> int:
    """Run the mechanical doc pre-pass over the repo's markdown; return an exit code."""
    errors: list[str] = []
    md_paths: list[Path] = []
    for md_path in REPO_ROOT.rglob("*.md"):
        if _is_hidden(md_path) or _is_ignored(md_path):
            continue
        md_paths.append(md_path)
        errors.extend(_check_links(md_path))
    errors.extend(_check_vocabulary_identity(md_paths))
    for path in REPO_ROOT.rglob("*"):
        if path.is_dir() or _is_hidden(path) or _is_ignored(path):
            continue
        if path.suffix == ".md":
            continue
        if path.suffix in (".py", ".yaml", ".toml") or path.name == "Makefile":
            errors.extend(_check_section_citations(path))
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        print(f"\n{len(errors)} doc drift finding(s).", file=sys.stderr)
        return 1
    print("check_docs: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
