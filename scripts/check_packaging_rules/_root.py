"""The `.git` walk-up every delivered script uses to find the repo it is grading.

A VERBATIM COPY of `find_repo_root`, `git_common_dir` and `same_repository` from
`src/racecar/lib/_root.py`, which is their one home. The copy exists because a
delivered checker has to run from an adopter's flat `scripts/` with no racecar
installed, so it cannot `import racecar.lib` -- the same one-home, verbatim-copy
relationship `racecar.mk` and `_shape.py` already have, and
`tests/test_root_finders.py` is what holds the two identical.

It lives in THIS package rather than beside the scripts that use it for one mechanical
reason: `sync_scripts.delivered_files` ships `check_packaging_rules/*.py` alongside
`check_packaging.py` by naming convention, so a module added here lands in every
adopter's `scripts/check_packaging_rules/` with no second delivery list to maintain. In
an adopter the package is a sibling of every delivered script, so the import needs no
help; in racecar's own tree it sits in the arch-python lens, and the four invocation
boundaries listed in `tests/test_root_finders.py` put that one directory on
PYTHONPATH. Nothing outside this file re-implements the walk-up.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor of `start` (default CWD) containing `.git`.

    ONE HOME: `src/racecar/lib/_root.py`. Exactly one verbatim copy is delivered, as
    `scripts/check_packaging_rules/_root.py`, because a delivered checker has to run
    from an adopter's flat `scripts/` with no racecar installed and so cannot import
    `racecar.lib`. That is the `racecar.mk` relationship -- one authored home, one
    delivered copy -- and `tests/test_root_finders.py` holds the two identical.

    Resolved, so the root compares equal to the `--root` paths callers already
    resolve. Returns `start` when no `.git` is found rather than raising: a checker
    run outside a repo should report on what is there, not fail to start.
    """
    start = (start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def git_common_dir(path: Path) -> Path | None:
    """Return the repository `path` belongs to, or None when it is not in one.

    The COMMON dir, not the git dir: a worktree's git dir is private to it
    (`.git/worktrees/<name>`) while its common dir is the repository every worktree
    shares. That distinction is the whole value of the call -- it is what makes two
    checkouts of one repo answer the same thing.

    None on every negative: not a repo, no git on the machine, a path that does not
    exist. A caller comparing two of these must therefore treat None as "cannot say",
    never as a match, which :func:`same_repository` does.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # git is not installed
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    common = Path(proc.stdout.strip())
    return common.resolve() if common.is_absolute() else (path / common).resolve()


def same_repository(one: Path, other: Path) -> bool:
    """Whether two paths are checkouts of ONE repository -- a git worktree included.

    A path comparison cannot answer this and is what several checkers used to ask
    instead. Two worktrees of one repo share no path prefix with each other, a clone
    sits wherever it was put, and `.claude/worktrees/<branch>/` is BOTH nested inside
    the main checkout and a separate checkout of the same repository. Every checker
    that derived an identity from the directory got the wrong answer there: canon was
    taken from the other checkout, so an edit made on the branch was reported as the
    branch contradicting canon, with advice that would have deleted the work.

    One path is trivially itself: the identity case short-circuits, so a directory that
    is not a git repository at all -- a fixture, a vendored tree, an unpacked archive --
    still reads as the same checkout as itself. Only the two-checkouts question needs git.

    False when either side cannot be resolved. An unanswerable question is not a match.
    """
    if one.resolve() == other.resolve():
        return True
    here = git_common_dir(one)
    return here is not None and here == git_common_dir(other)
