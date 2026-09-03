"""Forbidden lockfile and standalone pylintrc detection."""

from __future__ import annotations

from pathlib import Path

from ._constants import FORBIDDEN_LOCKFILES, FORBIDDEN_PYLINTRC
from ._findings import Finding
from ._shape import Shape


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


def check_forbidden_pylintrc(root: Path, shape: Shape | None = None) -> list[Finding]:
    """Flag a standalone pylint config file; pylint canon lives in pyproject.

    Exempt for the flat `django` shape (SG3): its pyproject is a config-home, not a
    library manifest, and `.pylintrc` (with pylint-django) is the idiomatic pylint
    config home for a Django site. pylint natively supports both `.pylintrc` and
    pyproject, so consolidating into pyproject is a racecar *preference* for packages,
    not a rule to force onto Django's own layout (python/django > racecar).
    """
    if shape is not None and shape.name == "django":
        return []
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
