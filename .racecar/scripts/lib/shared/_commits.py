"""The rules `shared/COMMITS.md` states about a commit and the version it moves.

Four readers apply them: the commit-msg gates (`check_commit_message.py`,
`check_version_bump.py`), the landing step that assigns a number (`bump_version.py`), and
the library's audit of the log (`racecar.package` commit history).

  `BUMP_BY_TYPE`    conventional type -> "major" | "minor" | "patch" | "none"
  `SUBJECT_RE`      `<type>(<scope>)!: <description>`, the description starting non-blank
  `SKIP_PREFIXES`   subjects git wrote or rebase consumes, which no rule here grades
  `strip_noise`     a message as git stores it: comments and the scissors tail removed
  `parse_type`      (type or None, breaking) from a whole message
  `bump_for`        the bump a type calls for, with the pre-1.0 rule when the version is known
  `next_version`    a version advanced by one bump level
  `classify_delta`  the step between two versions, or why it is not a valid one
  `semver_key`      `X.Y.Z` as an orderable tuple
  `version_home`    where a repo keeps its version, and the value there
  `version_from`    the version held in one version home's text

Standard library only: `bump_version.py` and the commit-msg hooks run with no venv.

Complexity: O(n) in the message or file read
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

# A type absent from this map is not a conventional type, and its bump is undefined rather
# than "none": a caller that must tell the two apart tests membership before asking.
BUMP_BY_TYPE = {
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

SUBJECT_RE = re.compile(r"^(?P<type>[a-z]+)(?P<scope>\([^)]*\))?(?P<bang>!)?: \S")
BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
SKIP_PREFIXES = ("merge ", "revert ", "fixup!", "squash!", "amend!")

_SCISSORS = "# ------------------------ >8"
_HOMES = ("pyproject.toml", "VERSION")


def strip_noise(text: str) -> list[str]:
    """The message's lines as git stores them: no comments, nothing below the scissors,
    no trailing whitespace and no trailing blank lines."""
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith(_SCISSORS):
            break
        if raw.startswith("#"):
            continue
        lines.append(raw.rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def parse_type(message: str) -> tuple[str | None, bool]:
    """(conventional type or None, breaking) from a whole message.

    Breaking is `!` after the type or scope, or a `BREAKING CHANGE:` footer anywhere; the
    footer counts even when the subject is not conventional.
    """
    subject = next((line for line in message.splitlines() if line.strip()), "")
    match = SUBJECT_RE.match(subject)
    breaking = bool(BREAKING_RE.search(message))
    if match is None:
        return None, breaking
    return match.group("type"), breaking or bool(match.group("bang"))


def bump_for(
    commit_type: str | None, breaking: bool, current: str | None = None
) -> str:
    """The bump a commit calls for: "major", "minor", "patch" or "none".

    A breaking change is major, except below 1.0.0, where a 0.x line has promised no
    stability and going to 1.0.0 is a deliberate owner decision: there it is minor. The
    rule needs the version it applies to, so without `current` a break reads as major.
    """
    if breaking:
        key = semver_key(current) if current is not None else None
        return "minor" if key is not None and key[0] == 0 else "major"
    if commit_type is None:
        return "none"
    return BUMP_BY_TYPE.get(commit_type, "none")


def next_version(current: str, level: str) -> str | None:
    """`current` advanced by `level`, or None when `current` is not `X.Y.Z`.

    `level` "none" (or anything that is not a bump) leaves the version where it is.
    """
    key = semver_key(current)
    if key is None:
        return None
    major, minor, patch = key
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return current


def classify_delta(old: str, new: str) -> str:
    """Name the step from `old` to `new`: "none", "patch", "minor", "major", or why not.

    Compared structurally, never as a component-wise delta: a minor bump resets patch to
    zero, so `0.60.2 -> 0.61.0` has a patch delta of -2 and a tuple comparison calls a
    valid bump invalid.
    """
    if old == new:
        return "none"
    o, n = semver_key(old), semver_key(new)
    if o is None or n is None:
        return f"unparseable {old}->{new}"
    if n == (o[0], o[1], o[2] + 1):
        return "patch"
    if n == (o[0], o[1] + 1, 0):
        return "minor"
    if n == (o[0] + 1, 0, 0):
        return "major"
    return f"invalid {old}->{new}"


def semver_key(version: str) -> tuple[int, int, int] | None:
    """`X.Y.Z` as an orderable tuple, or None when it is not three integers."""
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    major, minor, patch = (int(part) for part in parts)
    return major, minor, patch


def version_from(content: str | None, home: str) -> str | None:
    """The version one version home's text holds, or None.

    `pyproject.toml` holds it at `[project].version`; `VERSION` holds nothing else. A
    pyproject that does not parse holds none.
    """
    if content is None:
        return None
    if home == "VERSION":
        return content.strip() or None
    try:
        version = tomllib.loads(content).get("project", {}).get("version")
    except tomllib.TOMLDecodeError:
        return None
    return version if isinstance(version, str) else None


def version_home(root: Path) -> tuple[str, str] | None:
    """(repo-relative path, value) of the repo's version home, or None when it has none.

    `[project].version` in the root pyproject when it declares one, else a root `VERSION`.
    """
    for home in _HOMES:
        path = root / home
        if path.is_file():
            value = version_from(path.read_text(encoding="utf-8"), home)
            if value is not None:
                return home, value
    return None
