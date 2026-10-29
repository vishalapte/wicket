"""server-tree audits for Shape src+server (isort, import-linter, server pyproject)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import _rel_for_audit, _toml_load
from ._findings import Finding


def _server_first_party_roots(folders: tuple[Path, ...]) -> list[str]:
    """Top-level importable package names under each Django project folder.

    A first-party root is any direct child directory of a Django folder holding at least one
    .py file. Returns the sorted names (e.g. ["apps", "core", "project"]).
    """
    roots: set[str] = set()
    for folder in folders:
        if not folder.is_dir():
            continue
        for child in folder.iterdir():
            if not child.is_dir() or child.name.startswith((".", "__")):
                continue
            if any(child.glob("*.py")):
                roots.add(child.name)
    return sorted(roots)


def check_server_isort_coverage(
    folders: tuple[Path, ...], data: dict[str, Any] | None, label: str
) -> list[Finding]:
    """For Shape src+server: [tool.isort] must cover the server source tree.

    isort runs over both trees from the root config and cannot auto-detect server's
    first-party packages, so the library [tool.isort] must declare:

      - src_paths -- including each Django project folder, and
      - known_first_party -- including every server top-level package.

    Both are Blockers.
    """
    if data is None:
        return []
    findings: list[Finding] = []
    isort = (data.get("tool", {}) or {}).get("isort", {}) or {}

    src_paths = isort.get("src_paths")
    absent = [
        f.name
        for f in folders
        if not isinstance(src_paths, list) or f.name not in src_paths
    ]
    if absent:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.isort].src_paths",
                f"Shape src+server: must include {absent} so isort scans every source "
                "root; single-tree auto-detection misclassifies server imports "
                "(PACKAGING.md §7)",
            )
        )

    expected_roots = _server_first_party_roots(folders)
    kfp = isort.get("known_first_party")
    if not isinstance(kfp, list):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.isort].known_first_party",
                "Shape src+server: required so isort classifies server's first-party "
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
                    f"Shape src+server: missing server first-party roots {missing}; "
                    "isort cannot auto-detect them over the second tree (PACKAGING.md §7)",
                )
            )

    return findings


def check_server_importlinter_coverage(
    folders: tuple[Path, ...], data: dict[str, Any] | None, label: str
) -> list[Finding]:
    """For Shape src+server: [tool.importlinter] must cover the server roots.

    The config must name at least one server top-level package, in `root_packages` or
    in a contract's modules; a bare `root_package` audits only the library. Blocker.
    """
    if data is None:
        return []
    expected_roots = _server_first_party_roots(folders)
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
                f"Shape src+server: import-linter covers only the library; it must "
                f"audit the server roots {expected_roots} too -- name them in "
                "root_packages or a contract (PACKAGING.md §7)",
            )
        ]
    return []


# The tool tables the server pyproject owns: racecar.mk's `typecheck` runs
# `cd $(RC_DJANGO) && mypy --config-file pyproject.toml .`. Every other tool reads
# $(LIB_PYPROJECT), so any other [tool.*] table here is reported by name.
SERVER_OWN_TOOLS = frozenset({"mypy", "django-stubs"})


def check_server_pyproject(root: Path, pyproject_path: Path) -> list[Finding]:
    """Validate the server pyproject (only present for Shapes src+server and flat+server).

    server/pyproject.toml is intentionally PEP 735-only:
      - has [dependency-groups].runtime with the Django runtime deps
      - has NO [project] block (server is not a publishable package)
      - has no [tool.*] block other than SERVER_OWN_TOOLS
      - has NO [build-system] (server is not pip-installable as a wheel)
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
                "server pyproject should not declare [project] -- server is "
                "not a publishable package (PACKAGING.md §3)",
            )
        )

    if "build-system" in data:
        findings.append(
            Finding(
                "Finding",
                label,
                "[build-system]",
                "server pyproject should not declare [build-system] -- not pip-installable",
            )
        )

    sprawl = sorted(t for t in (data.get("tool") or {}) if t not in SERVER_OWN_TOOLS)
    if sprawl:
        findings.append(
            Finding(
                "Finding",
                label,
                "[tool.*]",
                f"tool configs should live in the library pyproject (src/pyproject.toml), "
                f"not in server/pyproject.toml: {', '.join(f'[tool.{t}]' for t in sprawl)}",
            )
        )

    return findings
