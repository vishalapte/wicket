"""djapp-tree audits for Shape pypkg+djapp (isort, import-linter, djapp pyproject)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import _rel_for_audit, _toml_load
from ._findings import Finding


def _djapp_first_party_roots(root: Path) -> list[str]:
    """Top-level importable package names under djapp/.

    For Shape pypkg+djapp, isort runs over both pypkg/src and djapp from a
    settings file that lives only in pypkg/src. isort can auto-detect
    first-party packages over a single tree, but not over the second (djapp)
    tree -- it has no settings file there. So djapp's top-level packages must
    be declared explicitly in the library pyproject's [tool.isort].

    A "first-party root" is any direct child directory of djapp/ that holds at
    least one .py file (covers both __init__.py packages and namespace/app
    dirs). Returns the sorted package names (e.g. ["apps", "core", "project"]).
    """
    djapp_dir = root / "djapp"
    if not djapp_dir.is_dir():
        return []
    roots: list[str] = []
    for child in sorted(djapp_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "__")):
            continue
        if any(child.glob("*.py")):
            roots.append(child.name)
    return roots


def check_djapp_isort_coverage(
    root: Path, data: dict[str, Any] | None, label: str
) -> list[Finding]:
    """For Shape pypkg+djapp: [tool.isort] must cover the djapp source tree.

    `profile = "black"` alone is sufficient for single-root shapes (src,
    pypkg, djapp), where isort auto-detects first-party packages over the one
    tree it is pointed at. It is a FALSE GREEN for pypkg+djapp: the Makefile
    runs isort over BOTH pypkg/src and djapp from a config that lives only in
    pypkg/src, so isort cannot auto-detect djapp's first-party packages and
    misclassifies them as third-party -- failing files while a profile-only
    check passes. The library [tool.isort] must therefore declare:

      - src_paths -- must include "djapp" so isort scans that tree, and
      - known_first_party -- must include every djapp top-level package so
        those imports are classified first-party rather than third-party.

    Both are Blockers (consistent with the existing profile check).
    """
    if data is None:
        return []
    findings: list[Finding] = []
    isort = (data.get("tool", {}) or {}).get("isort", {}) or {}

    src_paths = isort.get("src_paths")
    if not isinstance(src_paths, list) or "djapp" not in src_paths:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.isort].src_paths",
                'Shape pypkg+djapp: must include "djapp" so isort scans both source '
                "roots; single-tree auto-detection misclassifies djapp imports "
                "(PACKAGING.md §7)",
            )
        )

    expected_roots = _djapp_first_party_roots(root)
    kfp = isort.get("known_first_party")
    if not isinstance(kfp, list):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.isort].known_first_party",
                "Shape pypkg+djapp: required so isort classifies djapp's first-party "
                "packages correctly (profile alone is a false green) (PACKAGING.md §7)",
            )
        )
    else:
        missing = [r for r in expected_roots if r not in kfp]
        if missing:
            findings.append(
                Finding(
                    "Blocker",
                    label,
                    "[tool.isort].known_first_party",
                    f"Shape pypkg+djapp: missing djapp first-party roots {missing}; "
                    "isort cannot auto-detect them over the second tree (PACKAGING.md §7)",
                )
            )

    return findings


def check_djapp_importlinter_coverage(
    root: Path, data: dict[str, Any] | None, label: str
) -> list[Finding]:
    """For Shape pypkg+djapp: [tool.importlinter] must cover the djapp roots.

    A bare `root_package = "xenocrates"` audits only the library import graph;
    `lint-imports` never looks at djapp at all, yet the existence-only check
    passes. The same multi-root blind spot as isort. The import-linter config
    must name at least one djapp top-level package -- either in `root_packages`
    (plural) or referenced by a contract's modules -- so the djapp import graph
    is actually audited. Blocker (consistent with the root_package check).
    """
    if data is None:
        return []
    expected_roots = _djapp_first_party_roots(root)
    if not expected_roots:
        return []
    il = (data.get("tool", {}) or {}).get("importlinter", {}) or {}

    named: set[str] = set()
    root_pkgs = il.get("root_packages")
    if isinstance(root_pkgs, list):
        named.update(str(p) for p in root_pkgs)
    for contract in il.get("contracts", []) or []:
        if not isinstance(contract, dict):
            continue
        for field in ("modules", "source_modules", "forbidden_modules"):
            value = contract.get(field)
            if isinstance(value, list):
                named.update(str(v).split(".", 1)[0] for v in value)

    if not any(r in named for r in expected_roots):
        return [
            Finding(
                "Blocker",
                label,
                "[tool.importlinter]",
                f"Shape pypkg+djapp: import-linter covers only the library; it must "
                f"audit the djapp roots {expected_roots} too -- name them in "
                "root_packages or a contract (PACKAGING.md §7)",
            )
        ]
    return []


def check_djapp_pyproject(root: Path, pyproject_path: Path) -> list[Finding]:
    """Validate the djapp pyproject (only present for Shape pypkg+djapp).

    djapp/pyproject.toml is intentionally PEP 735-only:
      - has [dependency-groups].runtime with the Django runtime deps
      - has NO [project] block (djapp is not a publishable package)
      - has NO [tool.*] blocks (tool configs live in the library pyproject)
      - has NO [build-system] (djapp is not pip-installable as a wheel)
    """
    label = _rel_for_audit(root, pyproject_path)
    data, findings = _toml_load(pyproject_path, label)
    if data is None:
        return findings

    groups = data.get("dependency-groups", {}) or {}
    runtime = groups.get("runtime")
    if runtime is None:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[dependency-groups].runtime",
                "required: declare the Django runtime deps here (PEP 735)",
            )
        )
    elif not isinstance(runtime, list):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[dependency-groups].runtime",
                "must be a list of strings (PEP 735)",
            )
        )

    if "project" in data:
        findings.append(
            Finding(
                "Finding",
                label,
                "[project]",
                "djapp pyproject should not declare [project] -- djapp is "
                "not a publishable package (PACKAGING.md §3)",
            )
        )

    if "build-system" in data:
        findings.append(
            Finding(
                "Finding",
                label,
                "[build-system]",
                "djapp pyproject should not declare [build-system] -- djapp is not pip-installable",
            )
        )

    if data.get("tool"):
        findings.append(
            Finding(
                "Finding",
                label,
                "[tool.*]",
                "tool configs should live in the library pyproject (pypkg/src/pyproject.toml), "
                "not in djapp/pyproject.toml",
            )
        )

    return findings
