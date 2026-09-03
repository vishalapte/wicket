"""Which files a checker reads. Verbatim copy of `racecar.lib._files`, the authored home.

A delivered checker runs from an adopter's flat `scripts/` with no racecar installed, so it
cannot import the library and needs this copy. `tests/test_repo_files.py` holds the two equal.

`_root.py` fixed "where does this repo start". This fixes the question one layer up: **which
files should a checker open**. It was answered independently in eight places, by eight
private skip-sets, no two the same and not one of them listing `.claude` — which is where
agent worktrees live, holding a whole second copy of the repo.

## Hidden DIRECTORIES are skipped. Hidden FILES are not.

The distinction is the whole rule, and getting it wrong is invisible in both directions.

A hidden directory is where a second copy of the repository lives — `.venv`, `.git`,
`.mypy_cache`, `.claude/worktrees`. Reading one makes a verdict depend on the machine
rather than on the repo. That was not hypothetical: `check_brief` reported OK only because
`deploy-server/.collections` happened to exist, and `check_vocabulary` answered "does this
repo have a CLI?" from a `__main__.py` inside a worktree checked out on a different branch.

A hidden file is ordinary committed content. `.gitignore`, `.pre-commit-config.yaml`,
`.yamllint`, `.ansible-lint` are files this repo owns and grades, and a checker that cannot
see them is blind to nine files here.

## Why `os.walk` and not `glob`

This started as `glob`, for a reason that was true: `glob` will not match a leading dot, so
`**/*.md` never descends into `.venv` or `.claude/worktrees`, where `Path.rglob` walks
everything and leaves the caller to discard what it read. Measured on this tree for the
same 273 files, `glob` was 9ms against `rglob`'s 455ms.

But that same rule is a single switch over both meanings of "hidden", and it silently took
the files with the directories: `repo_files(root, "*")` returned **zero** dotfiles.
`glob(..., include_hidden=True)` only inverts the switch — 537ms and 53k paths here,
because it descends `.venv` again.

`os.walk` separates them, because pruning happens on `dirs` and files are never pruned:

    glob (leading-dot rule)          10.9 ms    1.2k paths   0 dotfiles
    glob include_hidden=True        537.6 ms     53k paths   descends .venv
    os.walk, hidden dirs pruned       5.9 ms     957 paths   9 dotfiles

Mutating `dirs[:]` in place is what stops the descent; assigning a new name would not.
Faster than `glob` and it states the rule instead of encoding it as a side effect.

## Every caller names its own extensions

There is no default and no "all files". A checker states what it reads — `("*.md",)` for a
doc checker, `("*.md", "*.py", "*.sh", "*.mk")` for the nomenclature scan — because a
checker that does not know which files it grades cannot say what it covers.

## Gitignored, non-dot directories: pruned when git is available, walked when it is not

A hidden directory (leading dot) is pruned above on the name alone — no git needed. A
GITIGNORED directory need not be dot-prefixed at all: `build/`, `node_modules/`,
`vendor/` are ordinary names a repo excludes on purpose, and until `#52` this function
had no way to tell one from an ordinary tracked directory. Walking `build/` sent a
packaging artifact into every one of this function's six callers' path/doc/reachability
graphs as if it were part of the repo — verified directly against racecar's own tree,
83 gitignored files swept in from `build/` and `src/racecar.egg-info/` alone.

`_ignored_entries()` asks `git ls-files --others --ignored --exclude-standard
--directory`, the same source `check_content_blind.py`'s `published_files` already uses
elsewhere in this delivered set — one behavior, reused. `--directory` collapses a
directory git can prove is entirely untracked-and-ignored to one entry ending in `/`,
which is what the walk below prunes from `dirs` before descending — it never has to
enumerate what is inside. A directory git cannot collapse (something tracked survives
inside it) is walked as before, and only the individual ignored files git names within
it are skipped; a genuinely tracked file two levels under a mostly-ignored directory is
still found, because git's own answer says so, not a directory-level guess.

This is a SOFT dependency, on purpose, not the "git ls-files instead of os.walk"
replacement an earlier draft of this fix considered and rejected: `git ls-files` lists
FILES, and this function's contract returns directories too (a checker resolving
`deploy-server/example` needs the directory entry, not just the files under it), so git
can inform the prune without becoming the enumeration. Anywhere `git` is absent, or
`root` is not a git repository, `_ignored_entries()` returns empty and the walk is
byte-for-byte what it was before `#52`: dot-directories pruned, nothing gitignored-aware,
no subprocess run, no error raised. The trade this module used to describe as
deliberate — no git dependency in a delivered checker — still holds; what changed is
that a caller who DOES have git gets a truthful answer instead of a fast, wrong one.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path


def _ignored_entries(root: Path) -> tuple[set[str], set[str]]:
    """`(ignored_dirs, ignored_files)`, both relative-posix, from git's own ignore rules.

    Empty, empty when git is missing or `root` is not a git repository -- callers must
    not treat that as "nothing is ignored here" so much as "this signal is unavailable",
    which is exactly why the walk below falls back to its pre-#52 behavior rather than
    asserting cleanliness it cannot back up.
    """
    try:
        out = subprocess.run(
            [
                "git",
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "--directory",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set(), set()
    dirs: set[str] = set()
    files: set[str] = set()
    for line in out.splitlines():
        if not line:
            continue
        (dirs if line.endswith("/") else files).add(line.rstrip("/"))
    return dirs, files


def repo_files(root: Path, *patterns: str) -> list[Path]:
    """Every path under `root` matching any of `patterns`, skipping hidden directories
    and, where git can say so, gitignored ones.

    Returns files **and** directories: a checker that grades path references has to be able
    to resolve `deploy-server/example` as well as `deploy-server/example/site.yml`.

    Each pattern is matched against both the whole relative path and the basename, so
    `"*.md"` finds markdown at any depth and `"Makefile"` finds every Makefile. At least one
    pattern is required: see the module docstring on why there is no default.
    """
    if not patterns:
        raise ValueError(
            "repo_files needs at least one pattern — a checker states what it reads"
        )
    ignored_dirs, ignored_files = _ignored_entries(root)
    found: list[Path] = []
    for parent, dirs, files in os.walk(root):
        here = Path(parent)
        # In place. This is the one line (now two conditions) that separates "hidden or
        # ignored directory" from "hidden or ignored file"; `files` is filtered per-name
        # below instead, since an ignored file can sit inside an otherwise-tracked dir.
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and (here / d).relative_to(root).as_posix() not in ignored_dirs
        ]
        kept_files = [
            f
            for f in files
            if (here / f).relative_to(root).as_posix() not in ignored_files
        ]
        for name in dirs + kept_files:
            path = here / name
            relative = path.relative_to(root).as_posix()
            if any(
                fnmatch.fnmatch(relative, p) or fnmatch.fnmatch(name, p)
                for p in patterns
            ):
                found.append(path)
    return sorted(found)
