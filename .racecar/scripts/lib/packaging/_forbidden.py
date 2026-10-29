"""Forbidden lockfile and standalone pylintrc detection."""

from __future__ import annotations

from pathlib import Path

from ..shared._constants import FORBIDDEN_LOCKFILES, FORBIDDEN_PYLINTRC
from ..shared._root import django_roots
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

    Exempt for the `django` shape, whose `.pylintrc` is the Django site's pylint config home.
    """
    if shape is not None and shape.name == "django":
        return []
    findings: list[Finding] = []
    folders = shape.django_dirs if shape is not None else tuple(django_roots(root))
    names = FORBIDDEN_PYLINTRC + [
        f"{folder.relative_to(root).as_posix()}/.pylintrc"
        for folder in folders
        if folder != root
    ]
    for name in names:
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
