"""Library pyproject audit and the pylint-canon checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ._common import _dist_name, _is_type_stub, _rel_for_audit, _toml_load
from ._constants import (
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
from ._findings import Finding


# A single deterministic audit whose linear top-to-bottom shape reads clearest unfactored.
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
        # PEP 735 allows {include-group = "..."} entries; treat those as opaque and
        # match string entries against canon. Compare on distribution name (not the
        # raw string) so a project may pin a version ("pyyaml>=6.0") without the
        # pin reading as a different tool than canon's unpinned "pyyaml".
        entry_names = {_dist_name(d) for d in dev if isinstance(d, str)}
        canon_set = {_dist_name(t) for t in CANON_DEV_TOOLS}
        missing = canon_set - entry_names
        # Type-stub packages (pandas-stubs, types-*) are library-specific and permitted
        # appends to dev (PACKAGING.md §6); they are not "beyond canon".
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

    # Django shapes must carry djhtml in [dependency-groups].django (PACKAGING.md
    # §6). Keyed on manage.py so non-Django repos are never flagged. Entries may be
    # version-pinned ("djhtml>=3.0"), so compare on the distribution name only.
    is_django = (root / "manage.py").exists() or (
        root / "server" / "manage.py"
    ).exists()
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

    findings.extend(check_pylint_plugin_canon(data, label))

    # Only ONE thing under [tool.pylint] is asserted -- the plugin incompatibility above --
    # and the two rules that used to sit here were deleted after examination rather than
    # relaxed. The line between them is not severity, it is what kind of claim each makes:
    # the plugin rule states a FACT about racecar's own lint recipe, while both deleted rules
    # stated a PREFERENCE about the owner's linter.
    #
    # ignore-paths: required '^scripts/', justified as excluding vendored racecar check scripts
    # from doc-coherence. False -- check_docs, check_doc_graph and check_file_placement all
    # scan `*.md` only, so a vendored `scripts/check_packaging.py` was never in scope. What the
    # rule DID do was change pylint's scope as a side effect (ignore-paths filters even
    # explicitly-passed files), silently dropping every first-party file under scripts/ from
    # lint. It cost coverage to buy nothing. The one thing it genuinely excluded is MARKDOWN
    # under scripts/, and a scripts/README.md belongs inside the doc gate, not outside it.
    #
    # MESSAGES CONTROL.disable: which messages a repo suppresses is the owner's judgement, and
    # racecar advises rather than overrules (shared/OWNERSHIP.md). See _constants.py.
    #
    # Both are pylint's own keys, and what a repo scopes out of its own linter is its call.

    return findings, data


# pylint's own alias: MASTER was renamed MAIN in pylint 2.14 and both still load plugins.
# A repo predating the rename carries MASTER, and that is exactly the repo most likely to
# carry a plugin list from before the canonical lint went parallel -- so reading only MAIN
# would miss the population the rule is for.
PYLINT_MAIN_SECTIONS = ("MAIN", "MASTER")

# The one plugin racecar knows cannot be loaded globally, and why. Not a list of approved
# plugins: which plugins a repo loads is its call, and this names the single combination
# racecar's own recipe makes unworkable.
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

    racecar knew this failure in full and knew it in a comment: its own pyproject
    documents the RecursionError mechanism precisely, and nothing enforced it, so an
    adopter could hold the incompatible combination until `make lint` exploded on a file
    with nothing wrong with it. Two racecar decisions meeting -- loading the plugin
    globally was correct while the canonical lint was serial, and became wrong when the
    recipe gained `-j 0` -- with the adopter merely where they met.

    Blocker rather than Finding: the combination does not degrade the gate, it breaks it,
    and the diagnosis is unreachable from the failure text.
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
