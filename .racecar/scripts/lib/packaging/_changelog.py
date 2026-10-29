"""CHANGELOG.md validation."""

from __future__ import annotations

from pathlib import Path

from lib.shared import _markdown
from lib.shared._constants import RELEASE_TITLE_RE, UNRELEASED_TITLE

from ._findings import Finding


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
    # A released entry (`## X.Y.Z - YYYY-MM-DD`) or the `## [Unreleased]` header a new
    # changelog carries before its first release.
    if not any(
        h.title == UNRELEASED_TITLE or RELEASE_TITLE_RE.fullmatch(h.title)
        for h in _markdown.parse(text).headings(2)
    ):
        return [
            Finding(
                "Finding",
                "CHANGELOG.md",
                "header-format",
                "no `## [Unreleased]` or `## X.Y.Z - YYYY-MM-DD` heading found (PACKAGING.md §8)",
            )
        ]
    return []
