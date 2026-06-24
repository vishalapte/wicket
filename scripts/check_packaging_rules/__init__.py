"""The packaging checker: one audit per module, composed by a plain `run_all`.

The runnable entry is the sibling `check_packaging.py`; this package holds the
audits and composes them. Each audit is a plain function
`check_*(root, ...) -> list[Finding]` in its own module (`_pyproject`,
`_makefile`, `_gitignore`, ...). `run_all` below calls them in PACKAGING.md
order. There is no registry and no adapter layer: the orchestration IS the
readable list of calls, and the only state shared between audits (the parsed
library pyproject) is a local variable passed to the two checks that need it.

Public API is re-exported here so `from check_packaging_rules import detect_shape`
works, and the entry re-exports the same names so `from check_packaging import
detect_shape` keeps working for the other arch-coherence scripts.
"""

from __future__ import annotations

from pathlib import Path

from ._changelog import check_changelog
from ._common import _rel_for_audit
from ._djapp import (
    check_djapp_importlinter_coverage,
    check_djapp_isort_coverage,
    check_djapp_pyproject,
)
from ._findings import Finding
from ._forbidden import check_forbidden_lockfiles, check_forbidden_pylintrc
from ._gitignore import check_gitignore
from ._makefile import check_makefile
from ._precommit import check_precommit
from ._pyproject import check_library_pyproject
from ._requirements import check_requirements
from ._shape import Shape, detect_shape
from ._version import check_legacy_version_file

__all__ = [
    "Finding",
    "Shape",
    "check_changelog",
    "check_djapp_importlinter_coverage",
    "check_djapp_isort_coverage",
    "check_djapp_pyproject",
    "check_forbidden_lockfiles",
    "check_forbidden_pylintrc",
    "check_gitignore",
    "check_legacy_version_file",
    "check_library_pyproject",
    "check_makefile",
    "check_precommit",
    "check_requirements",
    "detect_shape",
    "run_all",
]


def run_all(root: Path) -> list[Finding]:
    """Run every packaging audit in PACKAGING.md order and collect the findings.

    The audits are independent except for one shared fact: the library pyproject
    is parsed once (by `check_library_pyproject`), and its parsed data and its
    `[project].version` flag feed two later audits (the djapp coverage checks and
    the legacy-VERSION-file gate). That fact is a pair of local variables, not a
    framework. The call order is fixed because audit output is presented in it.
    """
    shape, shape_findings = detect_shape(root)
    findings: list[Finding] = list(shape_findings)
    if shape.name == "unknown":
        return findings

    # The library pyproject, parsed once; its data and version flag feed the
    # djapp-coverage and legacy-VERSION audits below. A pure djapp shape has none.
    lib_data: dict | None = None
    has_canonical_version = False
    if shape.library_pyproject is not None:
        lib_findings, lib_data = check_library_pyproject(root, shape.library_pyproject)
        findings += lib_findings
        project = lib_data.get("project") if isinstance(lib_data, dict) else None
        has_canonical_version = isinstance(project, dict) and bool(
            project.get("version")
        )

    # pypkg+djapp: isort and import-linter must also cover the djapp tree.
    if shape.name == "pypkg+djapp" and shape.library_pyproject is not None:
        lib_label = _rel_for_audit(root, shape.library_pyproject)
        findings += check_djapp_isort_coverage(root, lib_data, lib_label)
        findings += check_djapp_importlinter_coverage(root, lib_data, lib_label)

    # The djapp pyproject: validate it when present, or flag its absence for
    # pypkg+djapp (where the djapp runtime deps would otherwise have no home).
    if shape.djapp_pyproject is not None:
        findings += check_djapp_pyproject(root, shape.djapp_pyproject)
    elif shape.name == "pypkg+djapp":
        findings.append(
            Finding(
                "Blocker",
                "djapp/pyproject.toml",
                "missing-file",
                "required for shape pypkg+djapp: PEP 735 "
                "[dependency-groups].runtime, no [project]",
            )
        )

    # The remaining audits are fully independent: one file in, findings out.
    findings += check_legacy_version_file(
        root, has_canonical_version=has_canonical_version
    )
    findings += check_requirements(root, shape)
    findings += check_forbidden_lockfiles(root)
    findings += check_forbidden_pylintrc(root)
    findings += check_gitignore(root)
    findings += check_makefile(root)
    findings += check_precommit(root)
    findings += check_changelog(root)
    return findings
