"""CHANGELOG.md validation."""

from __future__ import annotations

import re
from pathlib import Path

from ._findings import Finding

# A released entry (`## X.Y.Z - YYYY-MM-DD`) or the honest `## [Unreleased]`
# header a freshly-scaffolded changelog carries before its first release.
_CHANGELOG_HEADER_RE = re.compile(
    r"^## (?:\[Unreleased\]|\d+\.\d+\.\d+(?:[-+][\w.-]+)? - \d{4}-\d{2}-\d{2})",
    re.MULTILINE,
)


def check_changelog(root: Path) -> list[Finding]:
    """Recommend CHANGELOG.md and verify it has a Keep a Changelog header."""
    path = root / "CHANGELOG.md"
    if not path.exists():
        return [
            Finding(
                "Finding",
                "CHANGELOG.md",
                "missing-file",
                "CHANGELOG.md is recommended (Keep a Changelog format)",
            )
        ]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [Finding("Finding", "CHANGELOG.md", "encoding-error", str(exc))]
    if not _CHANGELOG_HEADER_RE.search(text):
        return [
            Finding(
                "Finding",
                "CHANGELOG.md",
                "header-format",
                "no `## [Unreleased]` or `## X.Y.Z - YYYY-MM-DD` heading found (PACKAGING.md §8)",
            )
        ]
    return []
