"""Library pyproject audit and the pylint-canon checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ._common import _dist_name, _rel_for_audit, _toml_load
from ._constants import (
    CANON_BLACK_TARGET,
    CANON_BUILD_BACKEND,
    CANON_BUILD_REQUIRES,
    CANON_DEV_TOOLS,
    CANON_DJANGO_TOOLS,
    CANON_ISORT_PROFILE,
    CANON_PYLINT_FORBIDDEN_DISABLE,
    CANON_PYLINT_REQUIRED_DISABLE,
    CANON_REQUIRES_PYTHON,
    FORBIDDEN_HATCH_SUBKEYS,
    FORBIDDEN_TOOL_KEYS,
    SEMVER_RE,
)
from ._findings import Finding


# A single deterministic audit whose linear top-to-bottom shape reads clearest unfactored.
def check_library_pyproject(  # pylint: disable=too-many-locals,too-many-statements
    root: Path, pyproject_path: Path
) -> tuple[list[Finding], dict[str, Any] | None]:
    """Validate the library pyproject (root for src/djapp shapes, pypkg/src/ for pypkg shapes).

    Audits: [project] PEP 621 keys, [dependency-groups].dev = canon, [build-system],
    [tool.*] configs, absence of non-canon [tool.*] blocks.
    """
    label = _rel_for_audit(root, pyproject_path)
    data, findings = _toml_load(pyproject_path, label)
    if data is None:
        return findings, None

    project = data.get("project")
    if not isinstance(project, dict):
        findings.append(
            Finding("Blocker", label, "[project]", "required PEP 621 table is missing")
        )
        return findings, data

    # Required PEP 621 keys
    for key in (
        "name",
        "version",
        "description",
        "requires-python",
        "authors",
        "dependencies",
    ):
        if key not in project:
            findings.append(
                Finding("Blocker", label, f"[project].{key}", "required key is missing")
            )

    version = project.get("version")
    if isinstance(version, str) and not SEMVER_RE.match(version):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[project].version",
                f"not a semver string: {version!r}",
            )
        )

    rp = project.get("requires-python")
    if rp is not None and rp != CANON_REQUIRES_PYTHON:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[project].requires-python",
                f"must be exactly {CANON_REQUIRES_PYTHON!r}; got {rp!r}",
            )
        )

    deps = project.get("dependencies")
    if deps is not None and not (
        isinstance(deps, list) and all(isinstance(d, str) for d in deps)
    ):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[project].dependencies",
                "must be a list of strings (direct runtime deps only)",
            )
        )

    # Canonical dev group (PEP 735)
    groups = data.get("dependency-groups", {}) or {}
    dev = groups.get("dev")
    if dev is None:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[dependency-groups].dev",
                "required (PEP 735): must contain the canonical dev tools per PACKAGING.md §6",
            )
        )
    elif not isinstance(dev, list):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[dependency-groups].dev",
                "must be a list of strings (PEP 735)",
            )
        )
    else:
        # PEP 735 allows {include-group = "..."} entries; treat those as opaque and
        # match string entries against canon. Compare on distribution name (not the
        # raw string) so a project may pin a version ("pyyaml>=6.0") without the
        # pin reading as a different tool than canon's unpinned "pyyaml".
        entry_names = {_dist_name(d) for d in dev if isinstance(d, str)}
        canon_set = {_dist_name(t) for t in CANON_DEV_TOOLS}
        missing = canon_set - entry_names
        extra = entry_names - canon_set
        if missing:
            findings.append(
                Finding(
                    "Blocker",
                    label,
                    "[dependency-groups].dev",
                    f"missing canon tools: {sorted(missing)}",
                )
            )
        if extra:
            findings.append(
                Finding(
                    "Finding",
                    label,
                    "[dependency-groups].dev",
                    f"unexpected tools beyond canon: {sorted(extra)} -- propose a "
                    "standards change in PACKAGING.md §6 or remove",
                )
            )

    # Django shapes must carry djhtml in [dependency-groups].django (PACKAGING.md
    # §6). Keyed on manage.py so non-Django repos are never flagged. Entries may be
    # version-pinned ("djhtml>=3.0"), so compare on the distribution name only.
    is_django = (root / "manage.py").exists() or (root / "djapp" / "manage.py").exists()
    if is_django:
        django_group = groups.get("django")
        django_names = (
            {_dist_name(d) for d in django_group if isinstance(d, str)}
            if isinstance(django_group, list)
            else set()
        )
        missing_django = {t for t in CANON_DJANGO_TOOLS if t not in django_names}
        if missing_django:
            findings.append(
                Finding(
                    "Blocker",
                    label,
                    "[dependency-groups].django",
                    f"Django shape must include canon tools: {sorted(missing_django)} "
                    "(canonical Django-template formatter per PACKAGING.md §6)",
                )
            )

    # Reject the old [project.optional-dependencies].dev location (PEP 735 supersedes).
    old_opt = (
        (project.get("optional-dependencies") or {}).get("dev")
        if isinstance(project.get("optional-dependencies"), dict)
        else None
    )
    if old_opt is not None:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[project.optional-dependencies].dev",
                "deprecated location; move to [dependency-groups].dev per "
                "PEP 735 and PACKAGING.md §6",
            )
        )

    # Build system
    bs = data.get("build-system", {}) or {}
    if bs.get("requires") != CANON_BUILD_REQUIRES:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[build-system].requires",
                f"must be {CANON_BUILD_REQUIRES}; got {bs.get('requires')!r}",
            )
        )
    if bs.get("build-backend") != CANON_BUILD_BACKEND:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[build-system].build-backend",
                f"must be {CANON_BUILD_BACKEND!r}; got {bs.get('build-backend')!r}",
            )
        )

    # [tool.*] checks
    tool = data.get("tool", {}) or {}
    for forbidden in FORBIDDEN_TOOL_KEYS:
        if forbidden in tool:
            findings.append(
                Finding(
                    "Blocker",
                    label,
                    f"[tool.{forbidden}]",
                    "non-canon tool configuration; see PACKAGING.md §1 §2",
                )
            )
    hatch = tool.get("hatch")
    if isinstance(hatch, dict):
        for sub in FORBIDDEN_HATCH_SUBKEYS:
            if sub in hatch:
                findings.append(
                    Finding(
                        "Blocker",
                        label,
                        f"[tool.hatch.{sub}.*]",
                        "hatch-as-project-manager is non-canon; see PACKAGING.md §2",
                    )
                )

    black = tool.get("black", {}) or {}
    if black.get("target-version") != CANON_BLACK_TARGET:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.black].target-version",
                f"must be {CANON_BLACK_TARGET}; got {black.get('target-version')!r}",
            )
        )

    isort = tool.get("isort", {}) or {}
    if isort.get("profile") != CANON_ISORT_PROFILE:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.isort].profile",
                f"must be {CANON_ISORT_PROFILE!r}; got {isort.get('profile')!r}",
            )
        )

    mypy = tool.get("mypy", {}) or {}
    rp = (data.get("project") or {}).get("requires-python", "")
    m = re.search(r"(\d+\.\d+)", rp) if rp else None
    expected_mypy_python = m.group(1) if m else None
    if expected_mypy_python is None:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.mypy].python_version",
                "cannot derive expected value: [project].requires-python is missing or unparseable",
            )
        )
    elif mypy.get("python_version") != expected_mypy_python:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.mypy].python_version",
                f"must match requires-python ({expected_mypy_python!r}); "
                f"got {mypy.get('python_version')!r}",
            )
        )
    if mypy.get("strict") is not True:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.mypy].strict",
                f"must be true; got {mypy.get('strict')!r}",
            )
        )

    il = tool.get("importlinter", {}) or {}
    if "root_package" not in il and "root_packages" not in il:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.importlinter].root_package",
                "required for arch checks",
            )
        )

    findings.extend(_check_pylint_canon(tool, label))

    return findings, data


def _pylint_disable(tool: dict[str, Any]) -> list[str] | None:
    """The pylint disable list, or None if no pylint config is present.

    Tolerates the section spelling variants pylint accepts in pyproject
    (``MESSAGES CONTROL`` / ``messages_control``) and a bare ``disable`` under
    ``[tool.pylint]``.
    """
    pylint = tool.get("pylint")
    if not isinstance(pylint, dict):
        return None
    for key in (
        "MESSAGES CONTROL",
        "messages_control",
        "MESSAGES_CONTROL",
        "messages control",
    ):
        sect = pylint.get(key)
        if isinstance(sect, dict) and "disable" in sect:
            return [str(x) for x in (sect.get("disable") or [])]
    if "disable" in pylint:
        return [str(x) for x in (pylint.get("disable") or [])]
    return None


def _check_pylint_canon(tool: dict[str, Any], label: str) -> list[Finding]:
    master = tool.get("pylint", {}).get("MASTER", {}) or {}
    master_ignore = master.get("ignore-paths", []) or []
    findings: list[Finding] = []
    if not any("^scripts/" in str(p) for p in master_ignore):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.pylint.MASTER].ignore-paths",
                "must include '^scripts/' to exclude vendored racecar check "
                "scripts from doc-coherence checks",
            )
        )

    disable = _pylint_disable(tool)
    if disable is None:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.pylint] disable",
                'missing canonical pylint disable set; see PACKAGING.md "pylint canon"',
            )
        )
        return findings
    present = set(disable)
    for code in sorted(CANON_PYLINT_REQUIRED_DISABLE - present):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.pylint] disable",
                f'missing required disable {code!r}; see PACKAGING.md "pylint canon"',
            )
        )
    for code in sorted(CANON_PYLINT_FORBIDDEN_DISABLE & present):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.pylint] disable",
                f"{code!r} must not be disabled — class/function docstrings are required",
            )
        )
    return findings
