#!/usr/bin/env python3
"""Commit-msg gate: a bumpable conventional-commit type must bump the version home.

Enforces shared/COMMITS.md "Bump from commit type" at the `commit-msg` stage, where the
message is available. It fails a commit whose type maps to a semver bump (feat/fix/perf,
or a breaking change) when the version home is byte-identical between the index and HEAD.
It does not validate the bump magnitude.

The version home is resolved per COMMITS.md "Version home": the library pyproject's
`[project].version` where a `[project]` table exists, else a root `VERSION` file.

Non-bumpable types (docs, style, refactor, test, build, ci, chore, revert) pass
without a bump. A message that is not a conventional commit (a merge commit, a
revert summary git wrote) passes.

Usage (invoked by pre-commit at the commit-msg stage):
    python scripts/check_version_bump.py <commit-msg-file>

Exit 0 when the rule holds (or does not apply), 1 on a violation, 2 on a
configuration error (no resolvable version home).

Complexity: O(n), n = lines in the commit message
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from lib.shared import _commits


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run `git -C <root> <args>`, bounded and never raising.

    The one call site for the timeout. A timeout reports as `returncode=1`.
    """
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        print(
            f"check_version_bump: warning: git {' '.join(args)} timed out after "
            f"10s ({exc}); treating it as failed",
            file=sys.stderr,
        )
        return subprocess.CompletedProcess(exc.cmd, 1, stdout="", stderr=str(exc))


def _git_show(root: Path, spec: str) -> str | None:
    """Return `git show <spec>` content, or None when the object does not exist."""
    result = _git(root, "show", spec)
    return result.stdout if not result.returncode else None


def version_unchanged(root: Path, home_path: str) -> bool:
    """True when the version home is byte-identical between the index and HEAD.

    An initial commit (no HEAD) or a version home absent at HEAD counts as changed:
    a first commit cannot be required to bump a version that did not exist.
    """
    old = _commits.version_from(_git_show(root, f"HEAD:{home_path}"), home_path)
    if old is None:
        return False
    new = _commits.version_from(_git_show(root, f":{home_path}"), home_path)
    if new is None:
        new = _commits.version_from(
            (root / home_path).read_text(encoding="utf-8"), home_path
        )
    return old == new


def _staged_tree_matches_head(root: Path) -> bool:
    """True when the index is byte-identical to HEAD's tree — nothing new is staged."""
    return not _git(root, "diff", "--cached", "--quiet", "HEAD").returncode


def _head_carries_the_bump(root: Path, home_path: str) -> bool:
    """True when HEAD's version differs from its parent's — HEAD is itself a bump.

    No parent (HEAD is the root commit, or the version home first appears in HEAD)
    returns False.
    """
    head_version = _commits.version_from(
        _git_show(root, f"HEAD:{home_path}"), home_path
    )
    if head_version is None:
        return False
    parent_version = _commits.version_from(
        _git_show(root, f"HEAD~1:{home_path}"), home_path
    )
    if parent_version is None:
        return False
    return head_version != parent_version


def amend_preserving_bump(root: Path, home_path: str) -> bool:
    """True when this replaces a commit that already carries the bump, so it stands.

    Git tells a commit-msg hook nothing about amending, so an amend is recognised only
    when nothing is staged versus HEAD (git refuses an empty normal commit) and HEAD's
    version differs from its parent's.

    Fail-closed: an amend that also stages content still fails; ``RACECAR_AMEND=1`` is
    the explicit escape, and the failure message names it.
    """
    if os.environ.get("RACECAR_AMEND") == "1":
        return True
    if not _staged_tree_matches_head(root):
        return False
    return _head_carries_the_bump(root, home_path)


# ---------------------------------------------------------------------------
# Where a version may be decided
# ---------------------------------------------------------------------------
#
# A version number may only be decided on the trunk, because a branch cannot know its
# integration order. On the trunk a bumpable type must move the version home; off it the
# version home must not move. `trunk_branch`/`current_branch`/`on_trunk` resolve the
# trunk; `_gate_branch`/`_gate_trunk` enforce the two sides.


def trunk_branch(root: Path) -> str:
    """The branch a version number may be decided on.

    In order: an explicit `RACECAR_TRUNK`, the remote's own default via `origin/HEAD`,
    then whichever of `main` or `master` this repo has.
    """
    override = os.environ.get("RACECAR_TRUNK")
    if override:
        return override
    result = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if not result.returncode and result.stdout.strip():
        return result.stdout.strip().split("/", 1)[-1]
    for candidate in ("main", "master"):
        if not _git(root, "rev-parse", "--verify", "--quiet", candidate).returncode:
            return candidate
    return "main"


def current_branch(root: Path) -> str | None:
    """The checked-out branch, or None when HEAD is detached (which counts as off-trunk)."""
    result = _git(root, "symbolic-ref", "--short", "--quiet", "HEAD")
    name = result.stdout.strip()
    return name or None


def on_trunk(root: Path) -> bool:
    """True when a commit here is also a landing, so a version number is knowable."""
    return current_branch(root) == trunk_branch(root)


class Home(NamedTuple):
    """The version home and what the index did to it: one subject, so one argument."""

    path: str
    current: str
    unchanged: bool


class Classification(NamedTuple):
    """What the message says it is: the conventional type, and what it maps to."""

    commit_type: str | None
    breaking: bool
    bump: str


def _gate_branch(root: Path, home: Home) -> int:
    """Off the trunk: the version home must not move, whatever the commit's type."""
    if home.unchanged:
        return 0
    print(
        f"check_version_bump: {home.path} moves on '{current_branch(root)}', which is not "
        f"the trunk ('{trunk_branch(root)}'). A branch cannot know the next version -- its "
        f"inputs are the trunk's version and what lands before this, and neither is decided "
        f"yet. Record the entry under '## [Unreleased]' and let the landing assign the "
        f"number (scripts/bump_version.py --assign). Set RACECAR_TRUNK if this branch IS "
        f"the trunk. See shared/COMMITS.md.",
        file=sys.stderr,
    )
    return 1


def _gate_trunk(root: Path, home: Home, what: Classification) -> int:
    """On the trunk: a bumpable type must move the version home."""
    if what.bump == "none" or not home.unchanged:
        return 0
    if amend_preserving_bump(root, home.path):
        return 0  # amending a commit that already bumped; the bump is intact
    label = "breaking change" if what.breaking else f"'{what.commit_type}'"
    print(
        f"check_version_bump: {label} maps to a {what.bump} bump, but {home.path} is "
        f"unchanged versus HEAD (still {home.current}). Bump the version home or "
        f"reclassify the commit. Amending a commit that already bumped, with content "
        f"staged? RACECAR_AMEND=1 git commit --amend. See shared/COMMITS.md.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str]) -> int:
    """Gate a commit message against the version-home rule; return an exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("commit_msg_file", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    message = "\n".join(
        _commits.strip_noise(args.commit_msg_file.read_text(encoding="utf-8"))
    )
    commit_type, breaking = _commits.parse_type(message)
    bump = _commits.bump_for(commit_type, breaking)

    home = _commits.version_home(args.root)
    if home is None:
        if bump == "none":
            return 0
        print(
            "check_version_bump: no version home found "
            "(no [project].version and no VERSION file)",
            file=sys.stderr,
        )
        return 2

    home_path, current = home
    state = Home(home_path, current, version_unchanged(args.root, home_path))
    if not on_trunk(args.root):
        return _gate_branch(args.root, state)
    return _gate_trunk(args.root, state, Classification(commit_type, breaking, bump))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
