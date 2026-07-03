"""Project shape detection (PACKAGING.md "Scope")."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ._findings import Finding

# The shape is the product of two independent presences; the enum name is the derived
# label for each (has_library, has_django) cell -- a shorthand over the product, never the
# model itself. (False, False) is a real cell (neither a library nor a Django project),
# not a fallback to "src".
_SHAPE_NAMES = {
    (True, True): "src+server",
    (True, False): "src",
    (False, True): "server",
    (False, False): "unknown",
}


@dataclasses.dataclass(frozen=True)
class Shape:
    """The project shape as the product PYTHON_LIBRARY x DJANGO_PROJECT.

    `has_library` (a `src/` package with its pyproject at repo root) and `has_django`
    (`server/manage.py`) are the two independent axes; everything else is derived. `name`
    is the label for the (has_library, has_django) cell, exposed for readable comparisons
    but not the primitive -- compare the booleans when the axis is what matters.
    """

    has_library: bool
    has_django: bool
    library_pyproject: Path | None  # the root pyproject (None only when absent)
    server_pyproject: Path | None  # the server pyproject (only Shape src+server)
    manage_py: Path | None = None  # located Django manage.py (server/manage.py), else None

    @property
    def name(self) -> str:
        """The derived enum label for this (has_library, has_django) cell."""
        return _SHAPE_NAMES[(self.has_library, self.has_django)]


def detect_shape(root: Path) -> tuple[Shape, list[Finding]]:
    """Resolve the project shape from what is on disk (PACKAGING.md "Scope").

    Pure filesystem inference, the same decision `racecar.mk` makes in Make so the build
    is self-contained; a coherence test asserts the two classify every fixture identically.
    Shape is governed by what is, not by any declared value: there is no shape entry to read.

    Shape is the product PYTHON_LIBRARY (`src/<pkg>`) x DJANGO_PROJECT (`server/manage.py`),
    a 2x2 of independent presences -> a derived label:

      - (library, no Django)   -> `src`        — library only.
      - (library, Django)      -> `src+server` — a server implementation wrapping the library.
      - (no library, Django)   -> `server`     — a standalone Django project.
      - (no library, no Django)-> `unknown`    — a bare pyproject (neither axis); a finding,
                                                 not silently treated as a library.

    The root `pyproject.toml` is the shared shell, the precondition for any shape; without
    it the repo is unclassifiable. Django is detected by `server/manage.py` (never a bare
    `server/`). TODO: library-axis polymorphism — the `{packages,pypkg}/<pkg>/src/<pkg>`
    workspace form — is a downstream addition, not yet recognized.
    """
    root_py = root / "pyproject.toml"
    src_dir = root / "src"
    server_py = root / "server" / "pyproject.toml"
    server_manage = root / "server" / "manage.py"

    if not root_py.exists():
        return (
            Shape(False, False, None, None),
            [
                Finding(
                    "Blocker",
                    "pyproject.toml",
                    "missing-file",
                    "no pyproject.toml found at repo root; cannot determine project shape",
                )
            ],
        )

    has_library = src_dir.is_dir()  # PYTHON_LIBRARY axis (src/<pkg>)
    has_django = server_manage.exists()  # DJANGO_PROJECT axis (server/manage.py)
    findings: list[Finding] = []
    if not (has_library or has_django):
        findings.append(
            Finding(
                "Blocker",
                "pyproject.toml",
                "no-shape",
                "root pyproject.toml present but neither a src/ library nor a server/ "
                "Django project; not a recognized shape",
            )
        )
    return (
        Shape(
            has_library=has_library,
            has_django=has_django,
            library_pyproject=root_py,
            server_pyproject=(
                server_py if (has_library and has_django and server_py.exists()) else None
            ),
            manage_py=server_manage if has_django else None,
        ),
        findings,
    )
