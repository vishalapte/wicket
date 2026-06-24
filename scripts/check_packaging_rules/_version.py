"""Legacy VERSION-file detection."""

from __future__ import annotations

from pathlib import Path

from ._findings import Finding


def check_legacy_version_file(
    root: Path, *, has_canonical_version: bool
) -> list[Finding]:
    """A `VERSION` file at repo root is the pre-v4 pattern when a canonical
    `[project].version` already exists; pyproject is then the sole source.

    The rule is contingent on that canonical version. If the library pyproject
    declares no `[project].version` -- a repo that is not a deployable/publishable
    package (docs, scripts, a standards framework) has no `[project]` table at
    all -- there is nothing for VERSION to be redundant with, so VERSION is the
    legitimate version home and is not flagged. Only when `[project].version`
    exists is a separate `VERSION` file dead canon. See PACKAGING.md §8.

    When applicable, flag as a Finding so projects migrate. Not a Blocker --
    harmless if it happens to match pyproject, but it's dead canon.
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
