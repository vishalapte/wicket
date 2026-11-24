#!/usr/bin/env python3
"""Mechanical pre-pass check for markdown docs in any repo.

The mechanical pre-pass `doc-coherence/README.md` prescribes. Nothing here is
repo-specific: the root and layout are discovered at runtime.

Checks:

  1. Every `[text](path)` in a .md file resolves — path exists, and every
     `#anchor` matches a heading slug in the target .md file.
  2. Every `FILENAME.md §N` cited in a non-markdown file (scripts, Makefile,
     *.toml, *.yaml) points to a heading at that number in the target file.
     An optional directory prefix (`<dir>/FILENAME.md §N`) disambiguates
     when the same basename lives in more than one directory.
  3. Vocabulary identity — every line of the form
     ``<Class> values are literal: **<literal>**`` agrees with every other
     instance of the same ``<Class>`` across the repo's markdown.

Matches inside inline code spans (single backticks) and triple-backtick
fenced code blocks are ignored — they are literals, not links.

Discovery:
  - REPO_ROOT is the nearest ancestor of the current working directory
    containing a `.git` entry, else CWD.
  - DOC_SEARCH_DIRS is REPO_ROOT plus every top-level non-hidden directory
    under it, sorted alphabetically for deterministic first-match behavior.
  - Hidden directories (names starting with `.`) are skipped everywhere.

Exit 0 if clean, 1 if any drift is found.

Usage:
    python3 <path-to>/check_docs.py
    python3 <path-to>/check_docs.py --print-ignore-paths
    python3 <path-to>/check_docs.py --print-ignore-patterns

Complexity: O(files × size)
"""

from __future__ import annotations

import functools
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from lib.shared import _markdown
from lib.shared._constants import DELIVERED_RECORD_REL
from lib.shared._files import git_ls, is_hidden_path, repo_files
from lib.shared._root import find_repo_root

REPO_ROOT = find_repo_root()


# The library pyproject.toml is at the repo root in every shape
# (arch-python/PACKAGING.md).
PYPROJECT_CANDIDATES = ("pyproject.toml",)


def project_pyproject_path(repo_root: Path | None = None) -> Path | None:
    """Return the project's root pyproject.toml path, or None if it does not exist."""
    root = repo_root if repo_root is not None else REPO_ROOT
    for candidate in PYPROJECT_CANDIDATES:
        pyproject = root / candidate
        if pyproject.is_file():
            return pyproject
    return None


def load_project_pyproject(repo_root: Path | None = None) -> dict[str, Any]:
    """Parse and return the project's pyproject.toml as a dict.

    Returns ``{}`` when no pyproject exists or it cannot be parsed.
    """
    pyproject = project_pyproject_path(repo_root)
    if pyproject is None:
        return {}
    try:
        return tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


# Both spellings of pylint's first section; pylint honors either, and a repo carrying
# both gets the union.
PYLINT_MAIN_SECTIONS = ("MAIN", "MASTER")


def ignore_path_patterns(repo_root: Path | None = None) -> tuple[str, ...]:
    """The project's declared out-of-scope path regexes, as written.

    Read from ``ignore-paths`` under ``[tool.pylint.MAIN]`` or ``[tool.pylint.MASTER]``
    in the root ``pyproject.toml``. No pyproject or no key: empty tuple.

    Raw strings, because `racecar.mk` passes them back to pylint on the command line;
    :func:`ignore_patterns` compiles them for the checkers.
    """
    return _pylint_main_list("ignore-paths", repo_root)


def _pylint_main_list(key: str, repo_root: Path | None = None) -> tuple[str, ...]:
    """One list-valued key of pylint's first section, under either spelling, as written.

    Read for ``ignore-paths`` (path regexes) and ``ignore-patterns`` (base-name
    regexes).
    """
    data = load_project_pyproject(repo_root)
    if not data:
        return ()
    pylint = data.get("tool", {}).get("pylint", {})
    seen: dict[str, None] = {}
    for section in PYLINT_MAIN_SECTIONS:
        raw = pylint.get(section, {}).get(key, [])
        if not isinstance(raw, list):
            continue
        for pattern in raw:
            if isinstance(pattern, str):
                seen[pattern] = None
    return tuple(seen)


def ignore_name_patterns(repo_root: Path | None = None) -> tuple[str, ...]:
    """The project's declared out-of-scope BASE-NAME regexes (``ignore-patterns``).

    pylint applies this to a file rather than to the directory holding it; `racecar.mk`
    uses it to grade entry points under their own profile. Not consumed by the doc
    checkers.
    """
    return _pylint_main_list("ignore-patterns", repo_root)


def ignore_patterns(repo_root: Path | None = None) -> tuple[re.Pattern[str], ...]:
    """:func:`ignore_path_patterns`, compiled. The form every checker matches with."""
    return tuple(re.compile(p) for p in ignore_path_patterns(repo_root))


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
    return is_hidden_path(rel)


def _is_ignored(path: Path) -> bool:
    if not IGNORE_PATTERNS:
        return False
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return False
    return any(p.search(rel) for p in IGNORE_PATTERNS)


def _heading_slugs(text: str) -> set[str]:
    """Return the GitHub-style heading slugs for a markdown document.

    GitHub disambiguates a repeated heading by appending `-1`, `-2`, ... to the
    second and later occurrences. Emit those too, or a correct link to the
    second `## Usage` reads as a missing anchor.
    """
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    for heading in _markdown.parse(text).headings():
        h = heading.title.lower()
        h = re.sub(r"[`'\"]", "", h)
        h = re.sub(r"[^\w\s-]", "", h)
        base = re.sub(r"\s+", "-", h).strip("-")
        count = seen.get(base, 0)
        seen[base] = count + 1
        slugs.add(base if not count else f"{base}-{count}")
    return slugs


def _section_numbers(text: str) -> set[str]:
    """Return the top-level section numbers (e.g. {'1','2'} from '## 1. Foo')."""
    return {
        m.group(1)
        for heading in _markdown.parse(text).headings(2)
        if (m := re.match(r"(\d+)\.", heading.title))
    }


def _check_links(md_path: Path) -> list[str]:
    errors: list[str] = []
    for link in _markdown.read(md_path).links():
        target = link.target
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path_part, _, anchor = target.partition("#")
        target_file = (md_path.parent / path_part).resolve() if path_part else md_path
        if path_part and not target_file.exists():
            errors.append(f"{md_path}:{link.no}: broken link — {target}")
            continue
        if anchor and target_file.suffix == ".md":
            slugs = _heading_slugs(target_file.read_text(encoding="utf-8"))
            if anchor not in slugs:
                errors.append(f"{md_path}:{link.no}: missing anchor — {target}")
    return errors


# A citation of a `.py` or `.sh` file in this repo by path: `scripts/x.py`,
# `../src/acme/mod.py`, `$RACECAR/scripts/x.sh`. Only the repo-relative tail is
# captured.
PATH_CITATION = re.compile(
    r"(?:\.\./)*(?:\$\{?RACECAR(?:_HOME)?\}?/)?"
    r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:py|sh))"
)

# Illustrative path segments, not citations (`scripts/foo.py` meaning any script).
# Matched as stems against every path segment.
SYNTHETIC_SEGMENTS = frozenset(
    {
        "a",
        "b",
        "x",
        "y",
        "foo",
        "bar",
        "baz",
        "new",
        "old",
        "kept",
        "gone",
        "real",
        "generated",
        "gen",
        "graph",
        "script",
        "example",
        "mine",
        "theirs",
        "acme",
        "athena",
        "widget",
        "myproject",
    }
)

# Homes where a dead path is not drift: CHANGELOG.md records past state; `tests/`,
# `drafts/` and `templates/` hold fixtures and examples.
CITATION_EXEMPT = ("CHANGELOG.md", "tests/", "drafts/", "templates/")

# racecar's delivered files are exempt too (see `_is_delivered`): they cite racecar's
# own docs, which an adopter has no copy of.


@functools.lru_cache(maxsize=1)
def _tracked() -> frozenset[str] | None:
    """Every path git tracks here, or None when that cannot be answered.

    A gitignored path is out of scope. None on any failure (not a git repo, no git); the
    caller then grades everything, so a delivered checker still works in a tarball.
    """
    out = git_ls(REPO_ROOT, "-z")
    if out is None:
        return None
    return frozenset(part for part in out.split("\0") if part)


@functools.lru_cache(maxsize=1)
def _top_level() -> frozenset[str]:
    """The repo's own top-level entry names.

    A path whose first segment is not a top-level entry does not name this repo, so it
    is an illustration, not a citation.
    """
    return frozenset(p.name for p in REPO_ROOT.iterdir())


def _is_delivered(path: Path) -> bool:
    """True when racecar delivered this file, by the record sync leaves beside it.

    Walks up to the repo root: a delivered entry can bring a sibling package one or more
    levels below the record.
    """
    for parent in path.parents:
        if (parent / DELIVERED_RECORD_REL).is_file():
            return True
        if parent == REPO_ROOT:
            break
    return False


def _exempt_from_citations(path: Path) -> bool:
    """True when a dead citation in this file is not drift.

    Untracked files, delivered files, the brief's generated inventory, and the homes in
    CITATION_EXEMPT.
    """
    rel = path.relative_to(REPO_ROOT).as_posix()
    tracked = _tracked()
    if tracked is not None and rel not in tracked:
        return True
    if _is_delivered(path):
        return True
    if rel.startswith("docs/summary/") and rel.endswith("-INVENTORY.md"):
        # The brief's generated inventory: enumerated from the tree by
        # `check_brief.py --apply`, not cited by a person.
        return True
    return any(
        rel == part or rel.startswith(part) or f"/{part}" in f"/{rel}"
        for part in CITATION_EXEMPT
    )


def _check_path_citations(path: Path) -> list[str]:
    """Every path to a `.py` or `.sh` file cited here that resolves to nothing.

    `check_script_ownership` reads only `scripts:` frontmatter and `_check_links` only
    markdown links; this reads inline spans and bare paths in prose and docstrings.

    `_top_level` decides whether a path names this repo; `SYNTHETIC_SEGMENTS` decides
    whether it is an illustration. A path resolves from the citing file's directory or
    the repo root, and either passes. Fenced blocks are read: a reader copies them.
    """
    if _exempt_from_citations(path):
        return []
    errors: list[str] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        for match in PATH_CITATION.finditer(line):
            cited = match.group(1)
            if line[: match.start(1)].endswith("~/"):
                # A home-relative citation (`~/...`) is not a repo one, and a home
                # directory is not committed, so it is skipped.
                continue
            if any(Path(seg).stem in SYNTHETIC_SEGMENTS for seg in cited.split("/")):
                continue
            if cited.split("/")[0] not in _top_level():
                continue
            # Resolve from the citing file with any `../` run put back.
            climb = re.search(r"(?:\.\./)+$", line[: match.start(1)])
            relative = (climb.group(0) if climb else "") + cited
            if (path.parent / relative).exists() or (REPO_ROOT / cited).exists():
                continue
            errors.append(f"{path}:{lineno}: cited path does not exist — {cited}")
    return errors


def _find_doc(fname: str, src: Path | None = None) -> Path | None:
    """Resolve an unprefixed `FILENAME.md` citation.

    A file of that name beside the citing file wins; otherwise the first match in
    DOC_SEARCH_DIRS.
    """
    candidates = [d / fname for d in DOC_SEARCH_DIRS if (d / fname).is_file()]
    if not candidates:
        return None
    if src is not None:
        local = src.parent / fname
        if local.is_file():
            return local
    return candidates[0]


def _check_section_citations(path: Path) -> list[str]:
    if _exempt_from_citations(path):
        return []
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError, OSError):
        return errors
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in re.finditer(
            r"((?:\.{1,2}/|[\w.-]+/)*)([A-Z_]+\.md)\s*§\s*(\d+)", line
        ):
            prefix, fname, num = m.group(1).rstrip("/"), m.group(2), m.group(3)
            if prefix:
                # A prefixed citation may be repo-relative or relative to
                # the citing file (a leading ../ or ./). Accept either.
                # NB: no literal example here — the checker scans its own
                # source, so a sample citation becomes a real finding.
                target: Path | None = None
                for base in (path.parent, REPO_ROOT):
                    candidate = (base / prefix / fname).resolve()
                    if candidate.is_file():
                        target = candidate
                        break
                if target is None:
                    errors.append(
                        f"{path}:{lineno}: cites missing file — {prefix}/{fname} §{num}"
                    )
                    continue
            else:
                target = _find_doc(fname, path)
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

    Fewer than two sightings of a class means nothing to check.
    """
    sightings: dict[str, list[tuple[str, Path, int]]] = {}
    for md_path in md_paths:
        try:
            text = md_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        for lineno, line, _ in _markdown.parse(text).text():
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


def print_ignore_paths() -> int:
    """Print the declared out-of-scope regexes, comma-joined, for a non-Python caller.

    `racecar.mk` passes the list to pylint's ``--ignore-paths``, which takes one value,
    so the recipe must carry what the rcfile declared. Comma-joined: pylint's own list
    syntax.

    Refuses (exit 1, nothing on stdout) when a pyproject exists and does not parse, so
    Make never reads "no exclusions" out of "could not read the exclusions".
    """
    pyproject = project_pyproject_path()
    if pyproject is not None:
        try:
            tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            print(f"check_docs: cannot read {pyproject}: {exc}", file=sys.stderr)
            return 1
    print(",".join(ignore_path_patterns()))
    return 0


def print_ignore_patterns() -> int:
    """:func:`print_ignore_paths` for ``ignore-patterns``, and refusing the same way.

    A separate flag, because pylint takes the two as separate options.
    """
    pyproject = project_pyproject_path()
    if pyproject is not None:
        try:
            tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            print(f"check_docs: cannot read {pyproject}: {exc}", file=sys.stderr)
            return 1
    print(",".join(ignore_name_patterns()))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the mechanical doc pre-pass over the repo's markdown; return an exit code.

    `argv` defaults to empty, not ``sys.argv[1:]``, because the in-process caller is
    pytest, whose own flags would be rejected.
    """
    args = list(argv or ())
    if args == ["--print-ignore-paths"]:
        return print_ignore_paths()
    if args == ["--print-ignore-patterns"]:
        return print_ignore_patterns()
    if args:
        # Unknown arguments are refused, so a misspelled flag cannot report OK.
        print(f"check_docs: unknown argument(s): {' '.join(args)}", file=sys.stderr)
        return 2
    errors: list[str] = []
    md_paths: list[Path] = []
    for md_path in repo_files(REPO_ROOT, "*.md"):
        if _is_hidden(md_path) or _is_ignored(md_path):
            continue
        md_paths.append(md_path)
        errors.extend(_check_links(md_path))
        errors.extend(_check_path_citations(md_path))
    errors.extend(_check_vocabulary_identity(md_paths))
    for path in repo_files(REPO_ROOT, "*.py", "*.yaml", "*.toml", "Makefile"):
        if path.is_dir() or _is_hidden(path) or _is_ignored(path):
            continue
        errors.extend(_check_section_citations(path))
        if path.suffix == ".py":
            errors.extend(_check_path_citations(path))
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        print(f"\n{len(errors)} doc drift finding(s).", file=sys.stderr)
        return 1
    print("check_docs: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
