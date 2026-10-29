"""Project shape detection (PACKAGING.md "Scope")."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ._findings import Finding
from ._root import django_roots, flat_package, has_library

#: The two shapes where a library sits beside a `server/` Django project. A check about the
#: server tree, its pyproject or its lockfile asks this, never one name of the two.
LIBRARY_WITH_SERVER = ("src+server", "flat+server")


@dataclasses.dataclass(frozen=True)
class Shape:
    """The project shape as PYTHON_LIBRARY x DJANGO_PROJECT.

    `has_library` (a library package with its pyproject at repo root) and `has_django`
    are the two axes. The library axis is split by WHERE the package root is:

      - `src/<pkg>` — the src layout. `flat_package` is None.
      - `<pkg>` at the repo root, named by `[project].name` — the flat layout.
        `flat_package` is that folder.

    The Django axis is split by WHERE `manage.py` lives:

      - one or more top-level folders holding a `manage.py`, any names — Django surfaces
        wrapping the library (or standalone). Marked here by `django_root == False`.
      - a root `manage.py` — a `django-admin startproject` site with no library.
        Marked by `django_root == True`.

    `name` is the derived label; compare the booleans when the axis is what matters.
    """

    has_library: bool
    has_django: bool
    library_pyproject: Path | None  # the root pyproject (None only when absent)
    server_pyprojects: tuple[
        Path, ...
    ]  # each Django folder's pyproject, beside a library
    django_dirs: tuple[
        Path, ...
    ] = ()  # every Django project folder (or the root), else ()
    django_root: bool = (
        False  # Django marked by a ROOT manage.py (startproject site), not server/
    )
    flat_package: Path | None = None  # the library at the repo root (the flat layout)

    @property
    def source_root(self) -> str | None:
        """Where this shape's Python source lives, relative to the repo root.

        The value `racecar.mk` computes as `SRC`: `src` for the src-layout shapes, the
        package for the flat ones, the first Django folder for standalone Django projects, `.`
        for the startproject site, and None for no shape.
        """
        if self.flat_package is not None:
            return self.flat_package.name
        if self.name == "server":
            return self.django_dirs[0].name
        return {"src": "src", "src+server": "src", "django": "."}.get(self.name)

    @property
    def manage_pys(self) -> tuple[Path, ...]:
        """Each Django project's `manage.py`."""
        return tuple(folder / "manage.py" for folder in self.django_dirs)

    @property
    def name(self) -> str:
        """The derived label for this shape.

        The library splits by package root into `src` and `flat`; the (no-library, Django)
        cell splits by manage.py location into `server` and `django`.
        """
        layout = "flat" if self.flat_package is not None else "src"
        if not self.has_django:
            return layout if self.has_library else "unknown"
        if self.django_root:
            return "django"  # startproject site (root manage.py), no library
        return f"{layout}+server" if self.has_library else "server"


def detect_shape(root: Path) -> tuple[Shape, list[Finding]]:
    """Resolve the project shape from what is on disk (PACKAGING.md "Scope").

    Pure filesystem inference. `racecar.mk` makes the same decision in Make; a coherence
    test holds the two identical.

    Shape is PYTHON_LIBRARY x DJANGO_PROJECT. The library is `src/<pkg>`, or the package
    `[project].name` names at the repo root when there is no `src/` (`flat_package`). The
    Django axis is marked by a `manage.py` and split by its location:

      - (src library, no Django)   -> `src`         — library only.
      - (flat library, no Django)  -> `flat`        — library only, package at the root.
      - (library, <dir>/manage)    -> `src+server` / `flat+server` — servers wrapping it.
      - (no library, <dir>/manage) -> `server`      — standalone Django projects.
      - (no library, root manage)  -> `django`      — the django-admin startproject canon:
                                                      a standalone Django site.
      - (no library, no Django)    -> `unknown`     — a bare pyproject (neither axis); a
                                                      finding, not silently treated as `src`.

    Without a root `pyproject.toml` the repo is unclassifiable. Django is marked by a
    `manage.py`, never a folder name (`django_roots`). A root `manage.py` beside a library
    reads as the library shape, not `django`. TODO: the
    `{packages,pypkg}/<pkg>/src/<pkg>` workspace form.
    """
    root_py = root / "pyproject.toml"
    if not root_py.exists():
        return (
            Shape(False, False, None, ()),
            [
                Finding(
                    "Blocker",
                    "pyproject.toml",
                    "missing-file",
                    "no pyproject.toml found at repo root; cannot determine project shape",
                )
            ],
        )

    flat = flat_package(root)  # the library at the root, when there is no src/
    library = has_library(root)  # PYTHON_LIBRARY axis
    # DJANGO_PROJECT axis: `django_roots` decides where Django lives.
    homes = tuple(django_roots(root))
    has_django_root = homes == (root,)
    has_django = bool(homes)

    findings: list[Finding] = []
    if not (library or has_django):
        findings.append(
            Finding(
                "Blocker",
                "pyproject.toml",
                "no-shape",
                "root pyproject.toml present but none of a src/ library, a root "
                "package named by [project].name, a folder holding a Django project, or a root "
                "manage.py Django site; not a recognized shape",
            )
        )
    return (
        Shape(
            has_library=library,
            has_django=has_django,
            library_pyproject=root_py,
            server_pyprojects=(
                tuple(
                    h / "pyproject.toml"
                    for h in homes
                    if (h / "pyproject.toml").exists()
                )
                if library and not has_django_root
                else ()
            ),
            django_dirs=homes,
            django_root=has_django_root,
            flat_package=flat,
        ),
        findings,
    )
