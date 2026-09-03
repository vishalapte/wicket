"""server-tree audits for Shape src+server (isort, import-linter, server pyproject)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import _rel_for_audit, _toml_load
from ._findings import Finding


def _server_first_party_roots(root: Path) -> list[str]:
    """Top-level importable package names under server/.

    For Shape src+server, isort runs over both src and server from a
    settings file that lives at the repo root. isort can auto-detect
    first-party packages over a single tree, but not over the second (server)
    tree -- it has no settings file there. So server's top-level packages must
    be declared explicitly in the library pyproject's [tool.isort].

    A "first-party root" is any direct child directory of server/ that holds at
    least one .py file (covers both __init__.py packages and namespace/app
    dirs). Returns the sorted package names (e.g. ["apps", "core", "project"]).
    """
    server_dir = root / "server"
    if not server_dir.is_dir():
        return []
    roots: list[str] = []
    for child in sorted(server_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "__")):
            continue
        if any(child.glob("*.py")):
            roots.append(child.name)
    return roots


def check_server_isort_coverage(
    root: Path, data: dict[str, Any] | None, label: str
) -> list[Finding]:
    """For Shape src+server: [tool.isort] must cover the server source tree.

    `profile = "black"` alone is sufficient for single-root shapes (src,
    server), where isort auto-detects first-party packages over the one
    tree it is pointed at. It is a FALSE GREEN for src+server: the Makefile
    runs isort over BOTH src and server from a config that lives only in
    src, so isort cannot auto-detect server's first-party packages and
    misclassifies them as third-party -- failing files while a profile-only
    check passes. The library [tool.isort] must therefore declare:

      - src_paths -- must include "server" so isort scans that tree, and
      - known_first_party -- must include every server top-level package so
        those imports are classified first-party rather than third-party.

    Both are Blockers (consistent with the existing profile check).
    """
    if data is None:
        return []
    findings: list[Finding] = []
    isort = (data.get("tool", {}) or {}).get("isort", {}) or {}

    src_paths = isort.get("src_paths")
    if not isinstance(src_paths, list) or "server" not in src_paths:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.isort].src_paths",
                'Shape src+server: must include "server" so isort scans both source '
                "roots; single-tree auto-detection misclassifies server imports "
                "(PACKAGING.md §7)",
            )
        )

    expected_roots = _server_first_party_roots(root)
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
    root: Path, data: dict[str, Any] | None, label: str
) -> list[Finding]:
    """For Shape src+server: [tool.importlinter] must cover the server roots.

    A bare `root_package = "widgets"` audits only the library import graph;
    `lint-imports` never looks at server at all, yet the existence-only check
    passes. The same multi-root blind spot as isort. The import-linter config
    must name at least one server top-level package -- either in `root_packages`
    (plural) or referenced by a contract's modules -- so the server import graph
    is actually audited. Blocker (consistent with the root_package check).
    """
    if data is None:
        return []
    expected_roots = _server_first_party_roots(root)
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


# The tool tables the server pyproject legitimately OWNS, because racecar.mk reads this
# file as their config home: `typecheck` runs `cd $(SERVER) && mypy --config-file
# pyproject.toml .`, and django-stubs is inert without the mypy plugin that loads the
# settings module and builds the model graph.
#
# The concern the [tool.*] rule was written for is real -- tool config sprawling into a
# non-installable subtree -- and it survives, because every OTHER tool the Makefile runs is
# pointed at $(LIB_PYPROJECT) explicitly, so its config here would be read by nothing. What
# did not survive is asserting that against the two tables racecar's own template ships and
# its own recipe requires: an adopter following the template earned a permanent advisory
# Finding, and an adopter following the checker got an untyped Django server, because with
# no [tool.mypy] here mypy falls back to defaults and `strict` never reaches the server tree
# at all. Measured on one adopter: 50 reported errors against 147 real ones.
#
# Per-table rather than whole-file: a permitted table beside a sprawling one still reports,
# and the finding now NAMES the tables it means.
SERVER_OWN_TOOLS = frozenset({"mypy", "django-stubs"})


def check_server_pyproject(root: Path, pyproject_path: Path) -> list[Finding]:
    """Validate the server pyproject (only present for Shape src+server).

    server/pyproject.toml is intentionally PEP 735-only:
      - has [dependency-groups].runtime with the Django runtime deps
      - has NO [project] block (server is not a publishable package)
      - has no [tool.*] block OTHER than the typing pair racecar.mk reads here
        (SERVER_OWN_TOOLS); every other tool config lives in the library pyproject
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
