#!/usr/bin/env python3
"""Assert CHANGELOG.md's headings parse and its newest entry matches the version home.

Delivered to every governed repo and run by `make docs`. Every `## ` heading must be
well-formed and unique, and the newest released one must match the declared version.
`lib/packaging/_changelog` checks only for a valid Keep-a-Changelog header.

The version home is resolved per shared/COMMITS.md "Version home" and
arch-python/PACKAGING.md section 8: the library pyproject's `[project].version`
where a `[project]` table exists, else a root `VERSION` file.

The repo is found by walking up from the working directory to the nearest `.git`, never
from `__file__`: `racecar check --root <repo>` runs racecar's own copy of a checker.

Complexity: O(n), n = lines in CHANGELOG.md
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from lib.shared import _markdown
from lib.shared._constants import RELEASE_TITLE_RE, UNRELEASED_TITLE
from lib.shared._root import find_repo_root


def newest_changelog_version(changelog: str) -> str | None:
    """Return the version of the newest released CHANGELOG.md entry, or None.

    A released entry is `## X.Y.Z - YYYY-MM-DD`; the `## [Unreleased]` placeholder a fresh
    changelog may carry has no version, so it is not one.
    """
    for heading in _markdown.parse(changelog).headings(2):
        if match := RELEASE_TITLE_RE.fullmatch(heading.title):
            return match.group(1)
    return None


def declared_version(root: Path) -> str | None:
    """Return the repo's version from its single home, or None if there is none.

    `[project].version` wins wherever a `[project]` table exists; otherwise a root
    `VERSION` file (COMMITS.md).
    """
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data.get("project", {}).get("version")
        if isinstance(version, str):
            return version
    version_file = root / "VERSION"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip()
    return None


def heading_problems(changelog: str) -> list[str]:
    """Return every structural fault in CHANGELOG.md's `## ` headings.

    `newest_changelog_version` parses these headings, `/racecar-commit` appends one per bump,
    and the brief cites them. Two faults:

    - Malformed: a `## ` line that is neither `[Unreleased]` nor `X.Y.Z - YYYY-MM-DD`. A
      malformed release heading is invisible to the regex, so the version check would pass
      on an older entry. This also catches a rename of `## [Unreleased]` applied to its
      quotation in the intro prose, which strands the rest of that sentence as a `## ` line.
    - Duplicate: a version that appears twice.
    """
    problems: list[str] = []
    seen: dict[str, int] = {}
    for lineno, _, heading in _markdown.parse(changelog).headings(2):
        if heading == UNRELEASED_TITLE:
            continue
        match = RELEASE_TITLE_RE.fullmatch(heading)
        if match is None:
            problems.append(
                f"CHANGELOG.md:{lineno}: malformed heading `## {heading}` — "
                "expected `## [Unreleased]` or `## X.Y.Z - YYYY-MM-DD`"
            )
            continue
        version = match.group(1)
        if version in seen:
            problems.append(
                f"CHANGELOG.md:{lineno}: duplicate section for {version} "
                f"(already at line {seen[version]})"
            )
        else:
            seen[version] = lineno
    return problems


def _read(
    root: Path, precomputed: tuple[str | None, str | None] | None
) -> tuple[str | None, str | None]:
    """`(version, changelog_text)`, from `precomputed` when given, else read fresh.

    `changelog_text` is None exactly when `CHANGELOG.md` does not exist -- distinct from
    an empty string, which means it exists and is empty (and fails `newest_changelog_version`
    on its own terms).
    """
    if precomputed is not None:
        return precomputed
    changelog_path = root / "CHANGELOG.md"
    text = (
        changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else None
    )
    return declared_version(root), text


def not_graded(
    root: Path, precomputed: tuple[str | None, str | None] | None = None
) -> str | None:
    """Return why the version comparison does not apply to `root`, or None when it does.

    Three states qualify:

      - No CHANGELOG.md. The file is recommended, not required (PACKAGING.md section 8);
        check_packaging reports its absence.
      - No version home. check_packaging reports a missing `[project].version`.
      - No released section yet. A repo before its first release carries only
        `## [Unreleased]` (COMMITS.md "One changelog section per bump").

    `heading_problems` still runs against whatever CHANGELOG.md exists; only the version
    comparison is skipped, and the skip is reported.

    `precomputed`, an optional `(version, changelog_text)` pair, lets `main()` read
    `pyproject.toml` and `CHANGELOG.md` once. A call site that passes only `root` reads
    both files itself.
    """
    version, changelog_text = _read(root, precomputed)
    if version is None:
        return (
            "no version home: neither [project].version in pyproject.toml "
            "nor a root VERSION file"
        )
    if changelog_text is None:
        return "no CHANGELOG.md"
    if newest_changelog_version(changelog_text) is None:
        return "CHANGELOG.md has no released `## X.Y.Z - YYYY-MM-DD` section yet"
    return None


def problem(
    root: Path, precomputed: tuple[str | None, str | None] | None = None
) -> str | None:
    """Return an error message if the version home and newest changelog entry disagree.

    Returns None for any state `not_graded` names, so a caller that skips that
    consultation gets silence rather than a defect report for a legitimate repo.
    """
    if not_graded(root, precomputed) is not None:
        return None
    version, changelog_text = _read(root, precomputed)
    assert changelog_text is not None  # not_graded above already ruled out None
    top = newest_changelog_version(changelog_text)
    if top != version:
        return (
            f"the declared version is {version} but the newest CHANGELOG.md entry "
            f"is {top}; add a {version} entry"
        )
    return None


def findings(root: Path) -> list[dict[str, str]]:
    """Everything this check has to say about `root`. Prints nothing.

    Keys are `level` — `error` for something that fails the check, `info` for something
    that is merely worth saying — and `message`. An empty list means a clean repo.
    `pyproject.toml` and `CHANGELOG.md` are each read once.
    """
    precomputed = _read(root, None)
    _, changelog_text = precomputed
    out: list[dict[str, str]] = []

    skipped = not_graded(root, precomputed)
    if skipped:
        out.append(
            {
                "level": "info",
                "message": f"skipping the version comparison ({skipped})",
            }
        )
    else:
        err = problem(root, precomputed)
        if err:
            out.append({"level": "error", "message": err})

    if changelog_text is not None:
        for fault in heading_problems(changelog_text):
            out.append({"level": "error", "message": fault})
    return out


def render(records: list[dict[str, str]]) -> str:
    """The report `main` prints, built from `findings`."""
    return "\n".join(f"check_changelog: {r['message']}" for r in records)


def failed(records: list[dict[str, str]]) -> int:
    """1 when anything in `records` fails the gate, else 0."""
    return 1 if any(r["level"] == "error" for r in records) else 0


def main() -> int:
    """CLI entry: 0 when the changelog leads with the version home and its headings parse."""
    records = findings(find_repo_root())
    for record in records:
        stream = sys.stderr if record["level"] == "error" else sys.stdout
        print(render([record]), file=stream)
    if failed(records):
        return 1
    print("check_changelog: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
