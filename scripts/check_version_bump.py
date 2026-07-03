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
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

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


def _git_show(root: Path, spec: str) -> str | None:
    """Return `git show <spec>` content, or None when the object does not exist."""
    result = subprocess.run(
        ["git", "-C", str(root), "show", spec],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


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


def main(argv: list[str]) -> int:
    """Gate a commit message against the version-home bump rule; return an exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("commit_msg_file", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    commit_type, breaking = parse_type(message_body(args.commit_msg_file))
    bump = bump_for(commit_type, breaking)
    if bump == "none":
        return 0

    home = version_home(args.root)
    if home is None:
        print(
            "check_version_bump: no version home found "
            "(no [project].version and no VERSION file)",
            file=sys.stderr,
        )
        return 2
    home_path, current = home
    if version_unchanged(args.root, home_path):
        label = "breaking change" if breaking else f"'{commit_type}'"
        print(
            f"check_version_bump: {label} maps to a {bump} bump, but {home_path} is "
            f"unchanged versus HEAD (still {current}). Bump the version home or "
            f"reclassify the commit. See shared/COMMITS.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
