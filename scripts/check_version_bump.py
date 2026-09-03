#!/usr/bin/env python3
"""Commit-msg gate: a bumpable conventional-commit type must bump the version home.

Enforces shared/COMMITS.md "Bump from commit type". The type is parsed from the
commit message, so this is a `commit-msg`-stage hook (not a pre-commit-stage one):
only at commit-msg time is the message available. The rule is one-directional. It
fails a commit whose type maps to a semver bump (feat/fix/perf, or a breaking
change) when the version home is byte-identical between the index and HEAD. It does
NOT validate the bump magnitude (that COMMITS.md "Valid version increments" concern
belongs to racecar-commit); it only asserts that some bump happened.

The version home is resolved per COMMITS.md "Version home": the library pyproject's
`[project].version` where a `[project]` table exists, else a root `VERSION` file.

Non-bumpable types (docs, style, refactor, test, build, ci, chore, revert) pass
without a bump. A message that is not a conventional commit (a merge commit, a
revert summary git wrote) is not this gate's concern and passes.

Usage (invoked by pre-commit at the commit-msg stage):
    python scripts/check_version_bump.py <commit-msg-file>

Exit 0 when the rule holds (or does not apply), 1 on a violation, 2 on a
configuration error (no resolvable version home).

Complexity: O(n), n = lines in the commit message
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

# Type -> semver bump, straight from COMMITS.md "Bump from commit type". "none" is a
# real, common outcome. Breaking is handled separately (the `!` marker or the footer),
# not by a type name.
_BUMP_BY_TYPE = {
    "feat": "minor",
    "fix": "patch",
    "perf": "patch",
    "docs": "none",
    "style": "none",
    "refactor": "none",
    "test": "none",
    "build": "none",
    "ci": "none",
    "chore": "none",
    "revert": "none",
}

_SUBJECT_RE = re.compile(r"^(?P<type>[a-z]+)(?P<scope>\([^)]*\))?(?P<bang>!)?:\s")
_BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)


def message_body(commit_msg_file: Path) -> str:
    """Return the commit message with git's comment lines and scissors trailer removed."""
    lines: list[str] = []
    for line in commit_msg_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("# ------------------------ >8"):
            break
        if line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def parse_type(message: str) -> tuple[str | None, bool]:
    """Return (conventional type, is_breaking) parsed from a commit message.

    The type is None when the subject is not a conventional commit. is_breaking is
    True when the subject carries `!` after the type/scope or a `BREAKING CHANGE:`
    footer is present.
    """
    subject = next((ln for ln in message.splitlines() if ln.strip()), "")
    match = _SUBJECT_RE.match(subject)
    breaking = bool(_BREAKING_FOOTER_RE.search(message))
    if match is None:
        return None, breaking
    return match.group("type"), breaking or bool(match.group("bang"))


def bump_for(commit_type: str | None, breaking: bool) -> str:
    """Map a conventional type + breaking flag to a semver bump per COMMITS.md.

    Returns "major", "minor", "patch", or "none". A breaking change is "major"
    (the pre-1.0 downgrade to "minor" is COMMITS.md's, but is immaterial here: the
    gate only asks whether the bump is non-"none", which "major" and "minor" both are).
    """
    if breaking:
        return "major"
    if commit_type is None:
        return "none"
    return _BUMP_BY_TYPE.get(commit_type, "none")


def version_home(root: Path) -> tuple[str, str] | None:
    """Resolve the version home per COMMITS.md, as (repo-relative path, current value).

    `[project].version` in the root pyproject when a `[project]` table exists, else a
    root `VERSION` file. Returns None when neither is present (nothing to gate on).
    """
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data.get("project", {}).get("version")
        if isinstance(version, str):
            return "pyproject.toml", version
    version_file = root / "VERSION"
    if version_file.is_file():
        return "VERSION", version_file.read_text(encoding="utf-8").strip()
    return None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run `git -C <root> <args>`, bounded and never raising.

    ONE call site for the timeout every git invocation in this file needs: a wedged git
    (a lock held by another process, a blocked network prompt from a misconfigured
    credential helper) must not hang a commit-msg hook forever. Every caller here only
    reads `.returncode` and/or `.stdout`, both of which a failed/timed-out run can answer
    the same way a real nonzero exit does -- so timing out reports as `returncode=1`
    rather than forcing five call sites to each decide what a `TimeoutExpired` means to
    them.
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


def _version_from(content: str | None, home_path: str) -> str | None:
    """Extract the version value from a version-home file's content."""
    if content is None:
        return None
    if home_path == "VERSION":
        return content.strip()
    data = tomllib.loads(content)
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


def version_unchanged(root: Path, home_path: str) -> bool:
    """True when the version home is byte-identical between the index and HEAD.

    An initial commit (no HEAD) or a version home absent at HEAD counts as changed:
    a first commit cannot be required to bump a version that did not exist.
    """
    old = _version_from(_git_show(root, f"HEAD:{home_path}"), home_path)
    if old is None:
        return False
    new = _version_from(_git_show(root, f":{home_path}"), home_path)
    if new is None:
        new = _version_from((root / home_path).read_text(encoding="utf-8"), home_path)
    return old == new


def _staged_tree_matches_head(root: Path) -> bool:
    """True when the index is byte-identical to HEAD's tree — nothing new is staged."""
    return not _git(root, "diff", "--cached", "--quiet", "HEAD").returncode


def _head_carries_the_bump(root: Path, home_path: str) -> bool:
    """True when HEAD's version differs from its parent's — HEAD is itself a bump.

    No parent (HEAD is the root commit, or the version home first appears in HEAD)
    returns False, not True. There is no prior value to have bumped FROM, so nothing
    here is evidence that HEAD carries a bump, and inventing the benefit of the doubt
    exempts the one commit in a repo that most needs the version decided deliberately.
    """
    head_version = _version_from(_git_show(root, f"HEAD:{home_path}"), home_path)
    if head_version is None:
        return False
    parent_version = _version_from(_git_show(root, f"HEAD~1:{home_path}"), home_path)
    if parent_version is None:
        return False
    return head_version != parent_version


def amend_preserving_bump(root: Path, home_path: str) -> bool:
    """True when this replaces a commit that already carries the bump, so it stands.

    Amending is the one legitimate way for a bumpable commit to leave the version home
    identical to HEAD: the bump is already IN the commit being replaced. Comparing the
    index against HEAD then measures the amended commit against itself, and the gate
    fires on a commit that is in fact correct.

    Git tells a commit-msg hook nothing about amending — verified empirically: the hook
    environment is byte-identical between a normal commit and an amend. So the amend is
    recognised only where the evidence is EXACT, never inferred:

      * ``GIT_AUTHOR_DATE`` matching HEAD's was tried and rejected. Amend preserves the
        author date, so it looks decisive, but any commit created in the same second as
        the one before it matches too — and a scripted series (racecar's own commit
        runbook) creates exactly that. It let a genuinely new unbumped `feat` through on
        the first end-to-end test. A gate with a timing-dependent hole is not a gate.

      * Nothing staged versus HEAD IS exact. Git refuses an empty commit by default, so
        a normal commit always stages something; an index identical to HEAD's tree means
        the commit being written replaces HEAD rather than following it.

    The second condition — HEAD's version differing from its parent's — stops the
    exemption spreading: without it, any commit after a bump inherits it, which in a
    decomposed series is every commit.

    Fail-closed. An amend that also stages content is NOT recognised and still gates;
    ``RACECAR_AMEND=1`` is the explicit escape, and the failure message names it. An
    escape the owner types on purpose beats a heuristic that guesses wrong silently.
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
# A version number is a DERIVED value. Its inputs are the trunk's current version and the
# ordered list of what lands before it, and a branch knows neither. So `version = "0.86.0"`
# written on a branch is not a fact, it is a PREDICTION about integration order, and it goes
# stale the moment a sibling lands.
#
# The collision is silent in the place that matters most: two branches that predict the same
# number write the byte-identical string into the version home, so git auto-merges it with no
# conflict marker at all and one release is discarded while every gate stays green. Only the
# changelog conflicts loudly, which points attention at the wrong file.
#
# Committing straight to the trunk makes "commit" and "land" the same event, so there the
# prediction is always correct and the rule is unchanged. This narrows WHERE the assertion
# applies; it does not weaken it. Off the trunk the assertion INVERTS -- the version home
# must not move -- because a rule that merely stopped asking would leave the prediction
# permitted, and permitted is how it keeps happening.
#
# This is Trunk-Based Development's release-authority rule (Hammant; cited as a
# high-performance indicator in Forsgren, Humble & Kim's "Accelerate"): a version number
# may only be decided on the trunk, because a branch cannot know its own integration
# order. `trunk_branch`/`current_branch`/`on_trunk` below resolve which branch that is;
# `_gate_branch`/`_gate_trunk` enforce the two-sided rule stated above.


def trunk_branch(root: Path) -> str:
    """The branch a version number may be decided on.

    Resolved rather than assumed, because `main` is not universal and a repo on `master`
    would otherwise have every commit treated as off-trunk. In order: an explicit
    `RACECAR_TRUNK`, the remote's own default via `origin/HEAD`, then whichever of `main`
    or `master` this repo actually has.
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
    """The checked-out branch, or None when HEAD is detached.

    Detached counts as off-trunk. A detached HEAD is a rebase, a bisect or a CI checkout,
    and none of those is a place to decide a release number.
    """
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
        f"number (scripts/renumber_bump.py --assign). Set RACECAR_TRUNK if this branch IS "
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

    commit_type, breaking = parse_type(message_body(args.commit_msg_file))
    bump = bump_for(commit_type, breaking)

    home = version_home(args.root)
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
