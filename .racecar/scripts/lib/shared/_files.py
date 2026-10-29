"""Which files a checker reads, beside `_root.py`'s answer for where the repo is.

`src/racecar/lib/_files.py` is a symlink to this file, so the library and the delivered
checkers read the same code.

## Hidden DIRECTORIES are skipped. Hidden FILES are not.

A hidden directory (`.venv`, `.git`, `.claude/worktrees`) holds a second copy of the
repository, so reading one makes a verdict depend on the machine. A hidden file
(`.gitignore`, `.pre-commit-config.yaml`) is committed content and is graded.

## Why `os.walk` and not `glob`

`glob` treats hidden files and hidden directories with one switch. `os.walk` prunes `dirs`
and never prunes files. Mutating `dirs[:]` in place is what stops the descent.

## Every caller names its own extensions

There is no default and no "all files": a checker states what it reads, so it can say what
it covers.

## Gitignored, non-dot directories: pruned when git is available, walked when it is not

`_ignored_entries()` asks `git ls-files --others --ignored --exclude-standard --directory`.
A directory git collapses to one ignored entry is pruned before descending. A directory it
cannot collapse is walked, and only the ignored files git names inside it are skipped.

Where `git` is absent or `root` is not a repository, the walk prunes dot-directories only and
runs no subprocess.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path


# `__pycache__` is pruned by name, always. Anything else a project excludes goes through
# `ignore-paths` in `pyproject.toml` or `.gitignore`; `build/` is real source in some repos,
# so no other name is hardcoded.
class _LsState:
    """Holds the open scope's memo, or None when no scope is open.

    An attribute rather than a module-level name, so `git_scope` rebinds it without
    `global`.
    """

    def __init__(self) -> None:
        self.memo: dict[tuple[str, tuple[str, ...]], str | None] | None = None


_LS_STATE = _LsState()


@contextlib.contextmanager
def git_scope() -> Iterator[None]:
    """Memoise `git_ls` for the duration of ONE operation, then forget it.

    Outside a scope nothing is cached, because a repo's file list changes between
    operations. Open a scope only around work that does not write the tree.

    Re-entrant: the outermost scope owns the lifetime and an inner one is a no-op.
    """
    if _LS_STATE.memo is not None:
        yield  # already inside a scope; the outer one owns the lifetime
        return
    _LS_STATE.memo = {}
    try:
        yield
    finally:
        _LS_STATE.memo = None


def git_ls(root: Path, *args: str) -> str | None:
    """`git ls-files <args>` in `root`, or None when git cannot answer.

    None means git cannot answer (git missing, or `root` not a repository); it is not the
    empty string, which would read as "this repo has no such files".

    Cached per `(root, args)` while a `git_scope` is open, and not at all otherwise.
    """
    cache = _LS_STATE.memo
    key = (str(root), args)
    if cache is not None:
        if key in cache:
            return cache[key]
    try:
        out: str | None = subprocess.run(
            ["git", "ls-files", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        out = None
    if cache is not None:
        cache[key] = out
    return out


_NEVER_SOURCE = frozenset({"__pycache__"})

# Hidden directories that are repo content, carved out of the rule above by name.
# `.racecar/` is committed canon racecar delivers into a governed repo, and it is graded.
# api-fix run reports go to `.rclocal/`, which is gitignored, never under `.racecar/`.
CONTENT_DIRS = frozenset({".racecar"})


def is_hidden_path(relative: Path | str) -> bool:
    """Whether a repo-relative path is hidden, for a checker that filters paths it was
    already handed. Any dot-prefixed segment counts, except a `CONTENT_DIRS` name.

    Blunter than `repo_files`, which keeps hidden files. The doc checkers ask it only of
    `*.md`, where the two readings cannot differ.
    """
    return any(
        part.startswith(".") and part not in CONTENT_DIRS
        for part in Path(relative).parts
    )


def _ignored_entries(root: Path) -> tuple[set[str], set[str]]:
    """`(ignored_dirs, ignored_files)`, both relative-posix, from git's own ignore rules.

    Empty, empty when git is missing or `root` is not a git repository: the signal is
    unavailable, which is not the claim that nothing is ignored.
    """
    out = git_ls(root, "--others", "--ignored", "--exclude-standard", "--directory")
    if out is None:
        return set(), set()
    dirs: set[str] = set()
    files: set[str] = set()
    for line in out.splitlines():
        if not line:
            continue
        (dirs if line.endswith("/") else files).add(line.rstrip("/"))
    return dirs, files


# The mode git records for a gitlink (a submodule reference) in a tree entry.
_GITLINK_MODE = "160000 "


def _submodule_dirs(root: Path) -> set[str]:
    """Every checked-out git submodule path under `root`, relative-posix.

    A submodule is tracked as a gitlink (mode `160000`), so neither the hidden-name test nor
    `git ls-files --others --ignored` reaches it. Its contents are not the adopter's to fix.

    Empty on any failure, matching `_ignored_entries`.
    """
    out = git_ls(root, "--stage")
    if out is None:
        return set()
    found: set[str] = set()
    for line in out.splitlines():
        # Each line is `<mode> <sha> <stage>\t<path>`; only the mode marks a submodule.
        meta, _, path = line.partition("\t")
        if path and meta.startswith(_GITLINK_MODE):
            found.add(path)
    return found


def repo_files(root: Path, *patterns: str) -> list[Path]:
    """Every path under `root` matching any of `patterns`, skipping hidden directories,
    `__pycache__`, and -- where git can say so -- gitignored ones and checked-out
    submodules.

    It does not read the project's `ignore-paths`; the caller applies that
    (`check_doc_graph.in_scope`).

    Returns files and directories: a checker that grades path references has to resolve
    `deploy-server/example` as well as `deploy-server/example/site.yml`.

    Each pattern is matched against both the whole relative path and the basename, so
    `"*.md"` finds markdown at any depth and `"Makefile"` finds every Makefile. At least one
    pattern is required.
    """
    if not patterns:
        raise ValueError(
            "repo_files needs at least one pattern — a checker states what it reads"
        )
    # One scope around both git questions, unless the caller opened one.
    with git_scope():
        ignored_dirs, ignored_files = _ignored_entries(root)
        submodules = _submodule_dirs(root)
    found: list[Path] = []
    for parent, dirs, files in os.walk(root):
        here = Path(parent)
        # In place, so the walk does not descend. Files are filtered per name below, since
        # an ignored file can sit inside a tracked directory.
        dirs[:] = [
            d
            for d in dirs
            if (not d.startswith(".") or d in CONTENT_DIRS)
            and d not in _NEVER_SOURCE
            and (here / d).relative_to(root).as_posix() not in ignored_dirs
            and (here / d).relative_to(root).as_posix() not in submodules
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
