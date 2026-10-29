"""Library pyproject audit and the pylint-canon checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..shared._constants import (
    CANON_BLACK_TARGET,
    CANON_BUILD_BACKEND,
    CANON_BUILD_REQUIRES,
    CANON_DEV_TOOLS,
    CANON_DJANGO_TOOLS,
    CANON_ISORT_PROFILE,
    CANON_REQUIRES_PYTHON,
    FORBIDDEN_HATCH_SUBKEYS,
    FORBIDDEN_TOOL_KEYS,
    SEMVER_RE,
)
from ..shared._root import django_roots, package_dir
from ._common import _dist_name, _is_type_stub, _rel_for_audit, _toml_load
from ._findings import Finding


def check_dev_pins(dev: list[Any], label: str) -> list[Finding]:
    """A Finding for each pinned canon dev tool the repo carries unpinned or at another version."""
    pinned = {_dist_name(t): t for t in CANON_DEV_TOOLS if _dist_name(t) != t}
    out: list[Finding] = []
    for d in dev:
        if not isinstance(d, str) or _dist_name(d) not in pinned:
            continue
        want = pinned[_dist_name(d)]
        if d.replace(" ", "") != want.replace(" ", ""):
            out.append(
                Finding(
                    "Finding",
                    label,
                    "[dependency-groups].dev",
                    f"`{d}`: racecar requires `{want}`, the version it lints its "
                    "delivered files under (PACKAGING.md §6)",
                )
            )
    return out


def check_library_pyproject(  # pylint: disable=too-many-locals,too-many-statements
    root: Path, pyproject_path: Path
) -> tuple[list[Finding], dict[str, Any] | None]:
    """Validate the library pyproject (the repo root in every shape).

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
        # {include-group = "..."} entries are opaque; strings compare on distribution name,
        # so a pinned "pyyaml>=6.0" matches canon's "pyyaml".
        entry_names = {_dist_name(d) for d in dev if isinstance(d, str)}
        canon_set = {_dist_name(t) for t in CANON_DEV_TOOLS}
        missing = canon_set - entry_names
        # Type-stub packages (pandas-stubs, types-*) are permitted (PACKAGING.md §6).
        extra = {n for n in entry_names - canon_set if not _is_type_stub(n)}
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
        findings += check_dev_pins(dev, label)

    # Django shapes must carry djhtml in [dependency-groups].django (PACKAGING.md §6),
    # keyed on `django_roots` and compared on distribution name.
    if django_roots(root):
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
    package = package_dir(root)
    named = il.get("root_package")
    if package is not None and named is not None and named != package.name:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.importlinter].root_package",
                f"names {named!r}, but the repo's package is {package.name!r}; "
                "the arch checks would grade a package that is not there",
            )
        )

    findings.extend(check_pylint_plugin_canon(data, label))

    # Under [tool.pylint] only the plugin incompatibility above is asserted; ignore-paths
    # and MESSAGES CONTROL.disable are the repo's own.

    return findings, data


# pylint's own alias: MAIN and MASTER both load plugins.
PYLINT_MAIN_SECTIONS = ("MAIN", "MASTER")

# The one plugin that cannot be loaded globally under racecar's lint recipe, and why.
PARALLEL_HOSTILE_PLUGINS = {
    "pylint_pytest": (
        "cannot survive the canonical `pylint -j 0` library pass in racecar.mk: "
        "FixtureChecker.open() saves the already-patched VariablesChecker.add_message "
        "into _original_add_message and patches again, and parallel mode calls open() "
        "once per job per worker with no matching close(). The chain reaches ~980 frames "
        "and every job dies with RecursionError, which pylint reports as an astroid "
        "failure naming an innocent file. racecar.mk already passes this plugin on the "
        "one invocation that needs it -- the serial test-profile run -- so remove it "
        "here (`load-plugins = []`) and the suites stay graded."
    ),
}


def check_pylint_plugin_canon(data: dict[str, Any], label: str) -> list[Finding]:
    """Flag a globally loaded pylint plugin that racecar's own lint recipe cannot run.

    Loaded globally, the plugin meets the canonical lint's `-j 0`, and `make lint` fails
    with a RecursionError on a correct file. A Blocker.
    """
    pylint_cfg = (data.get("tool", {}) or {}).get("pylint", {}) or {}
    findings: list[Finding] = []
    for section in PYLINT_MAIN_SECTIONS:
        block = pylint_cfg.get(section)
        if not isinstance(block, dict):
            continue
        plugins = block.get("load-plugins")
        if isinstance(plugins, str):
            plugins = [p.strip() for p in plugins.split(",")]
        if not isinstance(plugins, list):
            continue
        for name in plugins:
            why = PARALLEL_HOSTILE_PLUGINS.get(str(name).strip())
            if why:
                findings.append(
                    Finding(
                        "Blocker",
                        label,
                        f"[tool.pylint.{section}].load-plugins",
                        f"{name} {why}",
                    )
                )
    return findings
