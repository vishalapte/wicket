"""Forbidden lockfile and standalone pylintrc detection."""

from __future__ import annotations

from pathlib import Path

from ._constants import FORBIDDEN_LOCKFILES, FORBIDDEN_PYLINTRC
from ._findings import Finding


def check_forbidden_lockfiles(root: Path) -> list[Finding]:
    """Flag any non-canon lockfile present at the project root."""
    findings: list[Finding] = []
    for name in FORBIDDEN_LOCKFILES:
        if (root / name).exists():
            findings.append(
                Finding(
                    "Blocker",
                    name,
                    "non-canon-lockfile",
                    "only requirements.txt via pip-compile is canon; see PACKAGING.md §5",
                )
            )
    return findings


def check_forbidden_pylintrc(root: Path) -> list[Finding]:
    """Flag a standalone pylint config file; pylint canon lives in pyproject."""
    findings: list[Finding] = []
    for name in FORBIDDEN_PYLINTRC:
        if (root / name).is_file():
            findings.append(
                Finding(
                    "Blocker",
                    name,
                    "standalone-pylintrc",
                    "pylint config lives in the library pyproject [tool.pylint], "
                    'not a standalone file; see PACKAGING.md "pylint canon"',
                )
            )
    return findings
