"""PEP 561: a typed library must ship the marker that says so."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import _rel_for_audit
from ._findings import Finding

_MARKER = "py.typed"


def _package_dirs(
    root: Path, lib_data: dict[str, Any] | None, source_root: str | None
) -> list[Path]:
    """The importable package directories this project publishes.

    Read from `[tool.setuptools.packages.find].where` when set, else the shape's `SRC`
    (PACKAGING.md §"Consuming the shape"): `src`, `server`, or for the flat shapes the
    package itself. The repo root is never scanned.

    A package is a directory with an `__init__.py`; a root that is itself a package (the
    flat shapes) is that package.
    """
    setuptools = (lib_data or {}).get("tool", {}).get("setuptools", {})
    find = setuptools.get("packages", {}).get("find", {})
    roots = find.get("where") or ([source_root] if source_root else [])
    out: list[Path] = []
    for where in roots:
        base = root / where
        if not base.is_dir():
            continue
        if (base / "__init__.py").is_file():
            out.append(base)
            continue
        out += [d for d in sorted(base.iterdir()) if (d / "__init__.py").is_file()]
    return out


#: Build backends that include every file inside the package in the wheel by default.
_SHIPS_PACKAGE_FILES = (
    "flit_core.buildapi",
    "hatchling.build",
    "setuptools.build_meta",
)


def _ships_marker(lib_data: dict[str, Any] | None) -> bool:
    """True when the build backend is told to include `py.typed` in the wheel.

    True for a backend in `_SHIPS_PACKAGE_FILES` (flit, hatchling and setuptools ship the
    marker with no configuration). Otherwise `[tool.setuptools.package-data]` naming the
    marker under any key, or `include-package-data = true`, satisfies it.
    """
    backend = (lib_data or {}).get("build-system", {}).get("build-backend")
    if backend in _SHIPS_PACKAGE_FILES:
        return True
    setuptools = (lib_data or {}).get("tool", {}).get("setuptools", {})
    if setuptools.get("include-package-data") is True:
        return True
    for patterns in (setuptools.get("package-data") or {}).values():
        if isinstance(patterns, list) and any(
            isinstance(p, str) and p.strip() == _MARKER for p in patterns
        ):
            return True
    return False


def check_py_typed(
    root: Path,
    lib_data: dict[str, Any] | None,
    lib_label: str,
    source_root: str | None = None,
) -> list[Finding]:
    """A published library declares its own types with a `py.typed` marker (PEP 561).

    Without it mypy reports `import-untyped` and treats the library as `Any`. Two
    Findings: the marker must exist beside the package, and the build backend must ship it.
    Applies only to a repo with a `[project]` table.
    """
    project = (lib_data or {}).get("project")
    if not isinstance(project, dict):
        return []

    packages = _package_dirs(root, lib_data, source_root)
    if not packages:
        return []

    findings: list[Finding] = []
    missing = [p for p in packages if not (p / _MARKER).is_file()]
    for pkg in missing:
        findings.append(
            Finding(
                "Finding",
                _rel_for_audit(root, pkg / _MARKER),
                "missing-py-typed",
                "PEP 561: add an empty py.typed so this library's annotations are "
                "visible to anything that imports it as an installed package.",
            )
        )

    if len(missing) < len(packages) and not _ships_marker(lib_data):
        findings.append(
            Finding(
                "Finding",
                lib_label,
                "py-typed-not-shipped",
                "py.typed exists but nothing ships it; add [tool.setuptools.package-data] "
                '"*" = ["py.typed"] or include-package-data = true, or the marker never '
                "reaches the wheel.",
            )
        )
    return findings
