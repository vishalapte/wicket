"""Project shape detection (PACKAGING.md "Scope")."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ._findings import Finding


@dataclasses.dataclass(frozen=True)
class Shape:
    """The resolved project shape plus the paths it implies."""

    name: str  # "src" | "pypkg" | "pypkg+djapp" | "djapp"
    library_pyproject: (
        Path | None
    )  # location of the library pyproject (None for pure djapp shape)
    djapp_pyproject: (
        Path | None
    )  # location of the djapp pyproject (only Shape pypkg+djapp)
    manage_py: Path | None = (
        None  # located Django manage.py: djapp/manage.py for pypkg+djapp and
        # nested-djapp, root manage.py for standalone-djapp, None for non-Django shapes.
        # The one home for "where Django lives" so checkers stop re-probing it.
    )


def detect_shape(root: Path) -> tuple[Shape, list[Finding]]:
    """Resolve the project shape from what is on disk (PACKAGING.md "Scope").

    Pure filesystem inference, the same four-way decision `racecar.mk` makes in Make
    so the build is self-contained. The two must stay in agreement; a coherence test
    asserts they classify every fixture identically. Shape is governed by what is, not
    by any declared value: there is no shape entry to read.
    """
    root_py = root / "pyproject.toml"
    pypkg_py = root / "pypkg" / "src" / "pyproject.toml"
    djapp_py = root / "djapp" / "pyproject.toml"
    djapp_manage = root / "djapp" / "manage.py"
    root_manage = root / "manage.py"

    pypkg_exists = pypkg_py.exists()
    # Django is recognized by manage.py, never by a bare djapp/ dir. A djapp/ that
    # holds only a pyproject (no manage.py) is not a runnable Django app, so it is
    # NOT a djapp: `djapp/` and `djapp/manage.py` together are what make a Django tree.
    djapp_django = djapp_manage.exists()

    if pypkg_exists and djapp_django:
        return (
            Shape(
                "pypkg+djapp",
                pypkg_py,
                djapp_py if djapp_py.exists() else None,
                manage_py=djapp_manage,
            ),
            [],
        )
    if pypkg_exists:
        return Shape("pypkg", pypkg_py, None), []
    if root_py.exists() and (root_manage.exists() or djapp_django):
        manage_py = root_manage if root_manage.exists() else djapp_manage
        return Shape("djapp", root_py, None, manage_py=manage_py), []
    if root_py.exists():
        return Shape("src", root_py, None), []
    return (
        Shape("unknown", None, None),
        [
            Finding(
                "Blocker",
                "pyproject.toml",
                "missing-file",
                "no pyproject.toml found at repo root or at pypkg/src/; "
                "cannot determine project shape",
            )
        ],
    )
