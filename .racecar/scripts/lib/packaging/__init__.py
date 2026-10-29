"""The packaging checker: one audit per module, composed by a plain `run_all`.

The runnable entry is the sibling `check_packaging.py`. Each audit is a function
`check_*(root, ...) -> list[Finding]` in its own module (`_pyproject`, `_makefile`,
`_gitignore`, ...); `run_all` calls them in PACKAGING.md order.

The public API is re-exported here, and again by `check_packaging.py`, so both
`from lib.packaging import detect_shape` and `from check_packaging import detect_shape` work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._changelog import check_changelog
from ._common import _rel_for_audit
from ._findings import Finding
from ._forbidden import check_forbidden_lockfiles, check_forbidden_pylintrc
from ._gitignore import check_gitignore
from ._makefile import check_makefile
from ._optin import check_optin
from ._precommit import check_precommit
from ._pyproject import check_library_pyproject
from ._pytyped import check_py_typed
from ._requirements import check_requirements
from ._server import (
    check_server_importlinter_coverage,
    check_server_isort_coverage,
    check_server_pyproject,
)
from ._shape import LIBRARY_WITH_SERVER, Shape, detect_shape
from ._version import check_legacy_version_file

__all__ = [
    "Finding",
    "Shape",
    "check_changelog",
    "check_server_importlinter_coverage",
    "check_server_isort_coverage",
    "check_server_pyproject",
    "check_forbidden_lockfiles",
    "check_forbidden_pylintrc",
    "check_gitignore",
    "check_legacy_version_file",
    "check_library_pyproject",
    "check_py_typed",
    "check_makefile",
    "check_optin",
    "check_precommit",
    "check_requirements",
    "detect_shape",
    "run_all",
]


def run_all(root: Path) -> list[Finding]:
    """Run every packaging audit in PACKAGING.md order and collect the findings.

    The library pyproject is parsed once; its data and `[project].version` flag feed
    the server coverage checks and the legacy-VERSION check. Output follows call order.
    """
    shape, shape_findings = detect_shape(root)
    findings: list[Finding] = list(shape_findings)
    if shape.name == "unknown":
        return findings

    # The library pyproject, parsed once. A pure server shape has none, and the `django`
    # shape's root pyproject is a config home, not a library manifest, so it is skipped.
    lib_data: dict[str, Any] | None = None
    has_canonical_version = False
    if shape.library_pyproject is not None and shape.name != "django":
        lib_findings, lib_data = check_library_pyproject(root, shape.library_pyproject)
        findings += lib_findings
        project = lib_data.get("project") if isinstance(lib_data, dict) else None
        has_canonical_version = isinstance(project, dict) and bool(
            project.get("version")
        )

        # PEP 561: the `py.typed` marker, and the packaging that ships it.
        findings += check_py_typed(
            root,
            lib_data,
            _rel_for_audit(root, shape.library_pyproject),
            shape.source_root,
        )

    # A library with a server: isort and import-linter must also cover the server tree.
    if shape.name in LIBRARY_WITH_SERVER and shape.library_pyproject is not None:
        lib_label = _rel_for_audit(root, shape.library_pyproject)
        findings += check_server_isort_coverage(shape.django_dirs, lib_data, lib_label)
        findings += check_server_importlinter_coverage(
            shape.django_dirs, lib_data, lib_label
        )

    # The server pyproject: validate it when present, or flag its absence for a library
    # with a server.
    for pyproject in shape.server_pyprojects:
        findings += check_server_pyproject(root, pyproject)
    if shape.name in LIBRARY_WITH_SERVER:
        for folder in shape.django_dirs:
            if not (folder / "pyproject.toml").exists():
                findings.append(
                    Finding(
                        "Blocker",
                        f"{folder.name}/pyproject.toml",
                        "missing-file",
                        f"required for shape {shape.name}: PEP 735 "
                        "[dependency-groups].runtime, no [project]",
                    )
                )

    # The remaining audits are independent: one file in, findings out.
    findings += check_legacy_version_file(
        root, has_canonical_version=has_canonical_version
    )
    findings += check_requirements(root, shape)
    findings += check_forbidden_lockfiles(root)
    findings += check_forbidden_pylintrc(root, shape)
    findings += check_gitignore(root)
    findings += check_makefile(root)
    findings += check_precommit(root, shape)
    findings += check_changelog(root)
    findings += check_optin(root)
    return findings
