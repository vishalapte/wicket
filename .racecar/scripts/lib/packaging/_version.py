"""Legacy VERSION-file detection."""

from __future__ import annotations

from pathlib import Path

from ._findings import Finding


def check_legacy_version_file(
    root: Path, *, has_canonical_version: bool
) -> list[Finding]:
    """A Finding for a root `VERSION` file when `[project].version` exists (PACKAGING.md §8).

    Without `[project].version`, `VERSION` is the version home and is not flagged.
    """
    if not has_canonical_version:
        return []
    if (root / "VERSION").exists():
        return [
            Finding(
                "Finding",
                "VERSION",
                "deprecated-file",
                "VERSION file is no longer canon; pyproject [project].version is the sole source "
                "of truth. Delete this file.",
            )
        ]
    return []
