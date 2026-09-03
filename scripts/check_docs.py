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
    python3 <path-to>/check_docs.py --print-ignore-paths
    python3 <path-to>/check_docs.py --print-ignore-patterns

Complexity: O(files × size)
"""

from __future__ import annotations

import functools
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from check_packaging_rules._files import repo_files
from check_packaging_rules._root import find_repo_root

REPO_ROOT = find_repo_root()


# The library pyproject.toml lives at the repo root in every shape (src,
# src+server, server) — see arch-python/PACKAGING.md §"Scope". check_docs does
# not import check_packaging's detect_shape: the two sit in different lens
# directories in racecar's own tree, so the cross-lens import would not resolve.
PYPROJECT_CANDIDATES = ("pyproject.toml",)


# Filenames that carry agent instructions for the directory they sit in. THIS IS THE
# HOME for that list; the doc-coherence checkers import it from here. `AGENTS.md` is
# the cross-tool convention and racecar's primary; `CLAUDE.md` is Claude Code's own
# auto-loaded name and stays accepted so a repo that has not migrated keeps passing.
# The tuple is precedence-ordered: when a directory holds both — racecar's own root
# does, where CLAUDE.md is a pointer at the AGENTS.md that owns the content — the
# first name present is the one that carries the content.
AGENT_DOC_NAMES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")


def agent_doc(directory: Path) -> Path | None:
    """Return `directory`'s agent-instruction file, or None when it has none.

    Preference follows `AGENT_DOC_NAMES`: a directory holding both gets the primary,
    never an arbitrary one. Callers that must grade *every* present name (placement,
    which forbids an orphan) iterate `AGENT_DOC_NAMES` themselves.
    """
    for name in AGENT_DOC_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def project_pyproject_path(repo_root: Path | None = None) -> Path | None:
    """Return the project's root pyproject.toml path, or None if it does not exist.

    The library pyproject is at the repo root in every shape. Shared, reusable
    across the doc-coherence checkers (and importable by sibling scripts).
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    for candidate in PYPROJECT_CANDIDATES:
        pyproject = root / candidate
        if pyproject.is_file():
            return pyproject
    return None


def load_project_pyproject(repo_root: Path | None = None) -> dict[str, Any]:
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


# Both spellings of pylint's first section. `MASTER` is the legacy name, `MAIN` the
# current one, and pylint honors either -- so a reader that knows only one silently
# drops the whole list for every repo that used the other. The order is MAIN first
# because that is the spelling a new repo writes; a repo carrying both gets the union.
PYLINT_MAIN_SECTIONS = ("MAIN", "MASTER")


def ignore_path_patterns(repo_root: Path | None = None) -> tuple[str, ...]:
    """The project's declared out-of-scope path regexes, as written.

    Honors ``ignore-paths`` under either spelling of pylint's first section
    (``[tool.pylint.MAIN]`` / ``[tool.pylint.MASTER]``) in the project's
    ``pyproject.toml``, so the script doesn't drown the report in vendored
    third-party drift the project has already declared out-of-scope. Reads the
    root ``pyproject.toml`` (the library pyproject in every shape). No
    pyproject / no key -> empty tuple.

    Raw strings rather than compiled patterns, because one consumer is not a
    matcher: `racecar.mk` passes the list back to pylint on the command line
    (see the LINT_IGNORE_PATHS argument there). :func:`ignore_patterns` compiles
    them for the checkers that match against them.

    Shared, reusable across the doc-coherence checkers so the ignore-paths
    reader lives in exactly one place.
    """
    return _pylint_main_list("ignore-paths", repo_root)


def _pylint_main_list(key: str, repo_root: Path | None = None) -> tuple[str, ...]:
    """One list-valued key of pylint's first section, under either spelling, as written.

    Two keys are read through this: ``ignore-paths`` (path regexes) and
    ``ignore-patterns`` (base-name regexes). pylint scopes files out by BOTH and gives
    each one value, so a recipe passing either on the command line has to carry what the
    rcfile declared or silently replace it -- the same argument, twice, which is why the
    reader is shared rather than written once per key.
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

    The companion to :func:`ignore_path_patterns`, and the one pylint applies to a file
    rather than to the directory holding it: ``ignore-paths`` prunes a tree during the
    walk, but an individual module inside a package is scoped out by base name or not at
    all. `racecar.mk` needs that distinction to grade the entry points under their own
    profile (arch-python/PYTHON.md §2). Not consumed by the doc checkers, which scope by
    path.
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
    """Return the GitHub-style heading slugs for a markdown document.

    GitHub disambiguates a repeated heading by appending `-1`, `-2`, ... to the
    second and later occurrences. Emit those too, or a correct link to the
    second `## Usage` reads as a missing anchor.
    """
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
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
        base = re.sub(r"\s+", "-", h).strip("-")
        count = seen.get(base, 0)
        seen[base] = count + 1
        slugs.add(base if not count else f"{base}-{count}")
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
        if line.lstrip().startswith("```"):
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


# A citation of a file in THIS repo by path: `scripts/x.py`, `../src/acme/mod.py`,
# `$RACECAR/scripts/x.sh`. Only the repo-relative tail is captured -- the prefix varies with
# where the citing file sits and with which env var the author happened to use.
#
# Scoped to `.py` / `.sh` on purpose. Every path shape is citable, and matching all of them
# grades URLs, shell fragments and prose about other repos; these two are what racecar's own
# tooling is written in, which is what relocates and leaves the citation behind.
PATH_CITATION = re.compile(
    r"(?:\.\./)*(?:\$\{?RACECAR(?:_HOME)?\}?/)?"
    r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:py|sh))"
)

# Names that are ILLUSTRATIONS, not citations. A doc explaining the delivery mechanism
# writes `scripts/foo.py` to mean "any script"; resolving that against the tree would
# report the example as drift and teach the reader to distrust the checker.
# Names that are ILLUSTRATIONS rather than citations, matched against EVERY path segment
# and not the filename alone. A doc explaining the src layout writes `src/acme/engine/run.py`
# to mean "your package"; keying on the filename only reported four such examples as drift
# the moment the pattern widened past `scripts/`. Stems, so an example is exempt whichever
# extension it carries -- this checker's own docstring cites `$RACECAR/scripts/x.sh`.
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

# Homes where a dead path is not drift. CHANGELOG.md is a RECORD -- it says what was true
# at 0.79.0, and editing it to match today would be falsifying history to satisfy a
# checker. The other three hold fixtures and examples by construction.
PATH_CITATION_EXEMPT = ("CHANGELOG.md", "tests/", "drafts/", "templates/")


@functools.lru_cache(maxsize=1)
def _tracked() -> frozenset[str] | None:
    """Every path git tracks here, or None when that cannot be answered.

    The gate grades what is COMMITTED. A gitignored path is out of scope by that fact
    alone, which is why this asks git instead of keeping a list of build-artifact
    directory names -- a hand-maintained third category between "tracked" and "ignored"
    is a list that goes stale, and the answer is already authoritative one command away.

    It matters in practice rather than in principle: a checkout carrying a stale `build/`
    from an earlier `pip install .` holds a COPY of every module, dead citations and all,
    and grading those reported 28 findings for one repaired tree.

    None on any failure -- not a git repo, no git -- and the caller then grades everything.
    Failing open is right here because the enumeration is a narrowing, not the check: a
    delivered checker that graded nothing outside git would be silently inert in a tarball.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return frozenset(part for part in out.split("\0") if part)


@functools.lru_cache(maxsize=1)
def _top_level() -> frozenset[str]:
    """The repo's own top-level entry names.

    The discriminator between a CITATION and an ILLUSTRATION, once the pattern is no longer
    anchored to `scripts/`. `athena/prices/loader.py` in a worked example and
    `raw.githubusercontent.com/.../sync_remote.py` in an install line are both paths that do
    not resolve, and neither is drift -- they do not name this repository at all. Requiring
    the first segment to be something that actually sits at the root separates the two
    mechanically, and took the finding count from 183 to 28 without dropping a real one.
    """
    return frozenset(p.name for p in REPO_ROOT.iterdir())


def _exempt_from_path_citations(path: Path) -> bool:
    """True when a dead `scripts/` path in this file is not drift."""
    rel = path.relative_to(REPO_ROOT).as_posix()
    tracked = _tracked()
    if tracked is not None and rel not in tracked:
        return True
    return any(
        rel == part or rel.startswith(part) or f"/{part}" in f"/{rel}"
        for part in PATH_CITATION_EXEMPT
    )


def _check_path_citations(path: Path) -> list[str]:
    """Every path to a `.py` or `.sh` file cited here that resolves to nothing.

    The gap this closes, and why it is a checker rather than a review note. `fafba9c`
    moved racecar's own tooling from `scripts/` into `src/racecar/lib/**` and updated the
    `scripts:` frontmatter blocks -- so `check_script_ownership`, whose input is exactly
    those blocks, stayed green while dozens of prose and docstring citations went dead.
    `_check_links` could not see them either: they are inline code spans and bare paths in
    `Usage:` blocks, not markdown links.

    It was reported once (#4), fixed in the one file the report named, and recurred --
    which is the argument for enforcing it. A rule professed in a reviewer checklist is a
    rule that holds until the reviewer is busy (PRINCIPLES.md R-02).

    Scoped to `scripts/` when it first landed, which retired one instance rather than the
    class: the same relocation inside `src/` produced dead citations nothing graded, and the
    repair that closed #35 introduced eight more by rewriting dead `scripts/` paths into
    `src/` module paths. Widening it needed two discriminators, both mechanical --
    `_top_level` for "does this name THIS repo" and `SYNTHETIC_SEGMENTS` for "is this an
    illustration" -- and together they took 183 raw matches to 24 real findings.

    **Resolved from the citing file's directory OR the repo root, and either hit passes.**
    A lens README writing `../scripts/check_docs.py` is correct, and grading it against
    only one of the two roots reports the correct form as broken -- which inflates the
    finding count several-fold and buries the real ones.

    Fenced blocks are NOT skipped, unlike `_check_links`. A dead path inside a runnable
    snippet is the worst instance of this defect, not an exempt one: the reader copies it.
    """
    if _exempt_from_path_citations(path):
        return []
    errors: list[str] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        for match in PATH_CITATION.finditer(line):
            cited = match.group(1)
            if any(Path(seg).stem in SYNTHETIC_SEGMENTS for seg in cited.split("/")):
                continue
            if cited.split("/")[0] not in _top_level():
                continue
            if (path.parent / cited).exists() or (REPO_ROOT / cited).exists():
                continue
            errors.append(f"{path}:{lineno}: cited path does not exist — {cited}")
    return errors


def _find_doc(fname: str, src: Path | None = None) -> Path | None:
    """Resolve an unprefixed `FILENAME.md` citation.

    Two basenames can collide across directories (`architecture/R09-surfaces/README.md`
    and `arch-python/SURFACES.md`). Resolving by search order alone picks one
    arbitrarily and validates the citation against the wrong document, so when
    several candidates exist prefer the one that actually holds the cited
    section. Falls back to search order when no candidate does.
    """
    candidates = [d / fname for d in DOC_SEARCH_DIRS if (d / fname).is_file()]
    if not candidates:
        return None
    if src is not None:
        # Locality wins: a citation in arch-python/SURFACES.md to `PYTHON.md §4`
        # means its own directory's PYTHON.md. Disambiguating by *which candidate
        # happens to contain the section* would make a stale citation pass
        # whenever any same-named doc anywhere has that number, which is not a
        # check at all.
        local = src.parent / fname
        if local.is_file():
            return local
    return candidates[0]


def _check_section_citations(path: Path) -> list[str]:
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
            if line.lstrip().startswith("```"):
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


def print_ignore_paths() -> int:
    """Print the declared out-of-scope regexes, comma-joined, for a non-Python caller.

    `racecar.mk` needs the same list the checkers read: pylint gives
    ``--ignore-paths`` ONE value, so anything a recipe passes on the command line
    has to already carry what the rcfile declared, or the flag silently replaces
    it. A Make recipe cannot import this module, so the reader prints instead --
    one home, four consumers, rather than a second parser in the Makefile.

    Comma-joined because that is pylint's own list syntax, on the command line and
    in the rcfile alike: it splits every value of this option on commas, so a
    pattern carrying one is already not expressible upstream and nothing is lost
    by joining here.

    Refuses (exit 1, nothing on stdout) when a pyproject exists and does not parse.
    :func:`load_project_pyproject` deliberately swallows that -- a doc checker must
    not die because an unrelated table is malformed -- but the Makefile consumer
    must not read "no exclusions" out of "could not read the exclusions" and lint a
    tree the repo scoped out.
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

    Separate flags rather than one that prints both, because the two are separate pylint
    options with separate values: joining them would hand pylint a base-name regex where
    it expects a path one, and neither would match.
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

    `argv` defaults to EMPTY, not to ``sys.argv[1:]``: the in-process caller is a test
    suite, and under pytest ``sys.argv`` carries pytest's own flags, which this would
    then reject as unknown. The script entry point below passes the real argv.
    """
    args = list(argv or ())
    if args == ["--print-ignore-paths"]:
        return print_ignore_paths()
    if args == ["--print-ignore-patterns"]:
        return print_ignore_patterns()
    if args:
        # Unknown arguments used to be ignored, which meant a caller that misspelled
        # the flag got a full doc run reporting OK and an empty exclusion list.
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
    # The patterns ARE the filter. This walked every file and discarded all but these
    # four kinds; naming them is what lets the enumeration skip the rest instead of
    # reading and rejecting it.
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
