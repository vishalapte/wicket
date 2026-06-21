"""Validate project files against the racecar packaging canon.

See arch-coherence/PACKAGING.md for the canon this script enforces.

Shape detection (per PACKAGING.md §"Scope"):

    src           root pyproject.toml + src/<pkg>/
    pypkg         pypkg/src/pyproject.toml (no djapp/)
    pypkg+djapp   pypkg/src/pyproject.toml + djapp/pyproject.toml
    djapp         root pyproject.toml (no pypkg/), djapp/manage.py present

Each shape has a "library pyproject" (the one with [project], canonical
[tool.*] configs, [dependency-groups].dev) and -- for pypkg+djapp -- a
"djapp pyproject" (PEP 735 [dependency-groups].runtime only, no [project]).

Findings have two severities:

  Blocker  -- the file or rule is broken in a way that violates the canon
  Finding  -- a recommendation; passes by default, fails with --strict

Exit code: 0 on no Blockers; 1 if any Blocker is found (or any Finding with
--strict). Output is line-oriented and machine-greppable.

This script is pure-stdlib by design (tomllib + re + pathlib + dataclasses).

Usage:
    python check_packaging.py                  # validate current directory
    python check_packaging.py --root <path>    # validate elsewhere
    python check_packaging.py --strict         # treat Findings as Blockers
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
import tomllib
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Canon definitions (mirror arch-coherence/PACKAGING.md §3 §6 §7)
# ---------------------------------------------------------------------------

CANON_DEV_TOOLS = [
    "black",
    "isort",
    "pylint",
    "pylint-pytest",
    "mypy",
    "pytest",
    "pytest-cov",
    "pip-audit",
    "import-linter",
    "pip-tools",
    "pre-commit",
    "validate-pyproject",
]

CANON_REQUIRES_PYTHON = ">=3.12"
CANON_BLACK_TARGET = ["py312"]
CANON_ISORT_PROFILE = "black"
CANON_BUILD_REQUIRES = ["setuptools>=64"]
CANON_BUILD_BACKEND = "setuptools.build_meta"

# pylint canon (PACKAGING.md, "pylint canon"). Every code below must appear in
# [tool.pylint."MESSAGES CONTROL"].disable; a project may add more.
CANON_PYLINT_REQUIRED_DISABLE = {
    "raw-checker-failed",
    "bad-inline-option",
    "locally-disabled",
    "file-ignored",
    "suppressed-message",
    "useless-suppression",
    "deprecated-pragma",
    "use-symbolic-message-instead",
    "duplicate-code",
    "use-implicit-booleaness-not-comparison-to-string",
    "use-implicit-booleaness-not-comparison-to-zero",
    "missing-module-docstring",
}
# These must NOT be disabled: class + function docstrings are required.
CANON_PYLINT_FORBIDDEN_DISABLE = {
    "missing-class-docstring",
    "missing-function-docstring",
}
# Standalone pylint config files — forbidden; config lives in the library
# pyproject [tool.pylint] (PACKAGING.md, "pylint canon" + §7).
FORBIDDEN_PYLINTRC = [".pylintrc", "pylintrc", "pypkg/src/.pylintrc", "djapp/.pylintrc"]

# Forbidden top-level [tool.<key>] blocks (per §1 §2).
FORBIDDEN_TOOL_KEYS = {"uv", "ruff", "poetry", "pdm"}
FORBIDDEN_HATCH_SUBKEYS = {"envs"}

# Lockfiles produced by non-canon tools (per §5).
FORBIDDEN_LOCKFILES = ["uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock"]

REQUIRED_PRECOMMIT_HOOKS = {
    "black",
    "isort",
    "import-linter",
    "validate-pyproject",
    "no-upward-imports-in-business-modules",
    "doc-coherence-mechanical-pre-pass",
    "todo-format",
    "claude-md-shape",
    "file-placement",
}

REQUIRED_MAKEFILE_TARGETS = {
    "help",
    "install",
    "install-dev",
    "lock",
    "check",
    "check-full",
    "fix",
    "fmt",
    "fmt-check",
    "lint",
    "test",
    "coverage",
    "typecheck",
    "arch",
    "audit",
    "docs",
    "clean",
    "distclean",
    "system-deps",
}

FORBIDDEN_MAKEFILE_TOOLS = {"uv", "ruff", "poetry", "pdm", "pipenv"}

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$")


# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Finding:
    severity: str  # "Blocker" or "Finding"
    file: str
    rule: str
    message: str

    def render(self) -> str:
        return f"  {self.severity:7s}  {self.file:32s}  {self.rule:42s}  {self.message}"


# ---------------------------------------------------------------------------
# Shape detection
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Shape:
    name: str                       # "src" | "pypkg" | "pypkg+djapp" | "djapp"
    library_pyproject: Path | None  # location of the library pyproject (None for pure djapp shape)
    djapp_pyproject: Path | None    # location of the djapp pyproject (only Shape pypkg+djapp)


def detect_shape(root: Path) -> tuple[Shape, list[Finding]]:
    """Inspect the filesystem to determine which racecar shape this project is."""
    root_py = root / "pyproject.toml"
    pypkg_py = root / "pypkg" / "src" / "pyproject.toml"
    djapp_py = root / "djapp" / "pyproject.toml"
    djapp_manage = root / "djapp" / "manage.py"
    root_manage = root / "manage.py"

    pypkg_exists = pypkg_py.exists()
    djapp_dir_exists = djapp_manage.exists() or djapp_py.exists()

    if pypkg_exists and djapp_dir_exists:
        return (
            Shape("pypkg+djapp", pypkg_py, djapp_py if djapp_py.exists() else None),
            [],
        )
    if pypkg_exists:
        return Shape("pypkg", pypkg_py, None), []
    if root_py.exists() and (root_manage.exists() or djapp_dir_exists):
        return Shape("djapp", root_py, None), []
    if root_py.exists():
        return Shape("src", root_py, None), []
    return (
        Shape("unknown", None, None),
        [
            Finding(
                "Blocker",
                "pyproject.toml",
                "missing-file",
                "no pyproject.toml found at repo root or at pypkg/src/; cannot determine project shape",
            )
        ],
    )


# ---------------------------------------------------------------------------
# pyproject.toml
# ---------------------------------------------------------------------------


def _toml_load(path: Path, label: str) -> tuple[dict[str, Any] | None, list[Finding]]:
    """Read and parse a TOML file. Return (data_or_None, findings). label is the audit-rendered filename."""
    if not path.exists():
        return None, [
            Finding("Blocker", label, "missing-file", "required file is missing")
        ]
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), []
    except tomllib.TOMLDecodeError as exc:
        return None, [Finding("Blocker", label, "parse-error", str(exc))]


def _rel_for_audit(root: Path, path: Path) -> str:
    """Render a file path relative to root for audit output."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def check_library_pyproject(root: Path, pyproject_path: Path) -> tuple[list[Finding], dict[str, Any] | None]:
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
        # match string entries against canon.
        string_entries = {d for d in dev if isinstance(d, str)}
        canon_set = set(CANON_DEV_TOOLS)
        missing = canon_set - string_entries
        extra = string_entries - canon_set
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

    # Reject the old [project.optional-dependencies].dev location (PEP 735 supersedes).
    old_opt = (project.get("optional-dependencies") or {}).get("dev") if isinstance(
        project.get("optional-dependencies"), dict
    ) else None
    if old_opt is not None:
        findings.append(
            Finding(
                "Blocker",
                label,
                "[project.optional-dependencies].dev",
                "deprecated location; move to [dependency-groups].dev per PEP 735 and PACKAGING.md §6",
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
                f"must match requires-python ({expected_mypy_python!r}); got {mypy.get('python_version')!r}",
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
    for key in ("MESSAGES CONTROL", "messages_control", "MESSAGES_CONTROL", "messages control"):
        sect = pylint.get(key)
        if isinstance(sect, dict) and "disable" in sect:
            return [str(x) for x in (sect.get("disable") or [])]
    if "disable" in pylint:
        return [str(x) for x in (pylint.get("disable") or [])]
    return None


def _check_pylint_canon(tool: dict[str, Any], label: str) -> list[Finding]:
    master = (tool.get("pylint", {}).get("MASTER", {}) or {})
    master_ignore = master.get("ignore-paths", []) or []
    findings: list[Finding] = []
    if not any("^scripts/" in str(p) for p in master_ignore):
        findings.append(
            Finding(
                "Blocker",
                label,
                "[tool.pylint.MASTER].ignore-paths",
                "must include '^scripts/' to exclude vendored racecar check scripts from doc-coherence checks",
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


def check_djapp_isort_coverage(root: Path, data: dict[str, Any] | None, label: str) -> list[Finding]:
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
                "djapp pyproject should not declare [project] -- djapp is not a publishable package "
                "(PACKAGING.md §3)",
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


# ---------------------------------------------------------------------------
# Legacy VERSION file detection
# ---------------------------------------------------------------------------


def check_legacy_version_file(root: Path, *, has_canonical_version: bool) -> list[Finding]:
    """A `VERSION` file at repo root is the pre-v4 pattern when a canonical
    `[project].version` already exists; pyproject is then the sole source.

    The rule is contingent on that canonical version. If the library pyproject
    declares no `[project].version` -- a repo that is not a deployable/publishable
    package (docs, scripts, a standards framework) has no `[project]` table at
    all -- there is nothing for VERSION to be redundant with, so VERSION is the
    legitimate version home and is not flagged. Only when `[project].version`
    exists is a separate `VERSION` file dead canon. See PACKAGING.md §8.

    When applicable, flag as a Finding so projects migrate. Not a Blocker --
    harmless if it happens to match pyproject, but it's dead canon.
    """
    if not has_canonical_version:
        return []
    if (root / "VERSION").exists():
        return [
            Finding(
                "Finding",
                "VERSION",
                "deprecated-file",
                "VERSION file is no longer canon; pyproject [project].version is the sole source "
                "of truth. Delete this file.",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# requirements.txt + forbidden lockfiles
# ---------------------------------------------------------------------------


_PIN_RE = re.compile(r"^[A-Za-z][\w.\-]*\s*==\s*", re.MULTILINE)


def _is_real_lockfile(path: Path) -> bool:
    """A real pip-compile lockfile has the autogen header or at least one pin.

    An empty or comments-only file is treated as a placeholder, not a lockfile.
    pip-compile output always starts with a header containing the literal
    string `pip-compile`; additionally, it contains at least one pinned
    requirement of the form `name==version`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    head = text[:1000]
    if "pip-compile" in head or "autogenerated" in head.lower():
        return True
    return bool(_PIN_RE.search(text))


def check_requirements(root: Path, shape: Shape) -> list[Finding]:
    """Lockfiles are optional in racecar canon; if committed, they must be real.

    The canon does not require a requirements.txt. Pyproject is the sole source
    of truth for dependencies. But if a project chooses to commit a
    requirements.txt (snapshot, deployment artifact, CI input), it must be a
    real pip-compile or pip-freeze output -- not an empty placeholder.

    Checks the standard lockfile locations for each shape:
      src/djapp:    requirements.txt at root
      pypkg:        requirements.txt at pypkg/src/
      pypkg+djapp:  requirements.txt at pypkg/src/ AND djapp/

    Each path: if present, validated; if absent, no finding.
    """
    findings: list[Finding] = []
    candidates: list[Path] = []
    if shape.name in ("src", "djapp"):
        candidates.append(root / "requirements.txt")
    if shape.name in ("pypkg", "pypkg+djapp"):
        candidates.append(root / "pypkg" / "src" / "requirements.txt")
    if shape.name == "pypkg+djapp":
        candidates.append(root / "djapp" / "requirements.txt")
    for path in candidates:
        if not path.exists():
            continue  # optional; absence is fine
        if not _is_real_lockfile(path):
            label = _rel_for_audit(root, path)
            findings.append(
                Finding(
                    "Blocker",
                    label,
                    "not-a-lockfile",
                    "committed requirements.txt must be a real pip-compile or pip-freeze output "
                    "(empty or comments-only file is rejected); either populate it or remove it",
                )
            )
    return findings


def check_forbidden_lockfiles(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for name in FORBIDDEN_LOCKFILES:
        if (root / name).exists():
            findings.append(
                Finding(
                    "Blocker",
                    name,
                    "non-canon-lockfile",
                    "only requirements.txt via pip-compile is canon; see PACKAGING.md §5",
                )
            )
    return findings


def check_forbidden_pylintrc(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for name in FORBIDDEN_PYLINTRC:
        if (root / name).is_file():
            findings.append(
                Finding(
                    "Blocker",
                    name,
                    "standalone-pylintrc",
                    'pylint config lives in the library pyproject [tool.pylint], '
                    'not a standalone file; see PACKAGING.md "pylint canon"',
                )
            )
    return findings


# ---------------------------------------------------------------------------
# .gitignore
# ---------------------------------------------------------------------------


def check_gitignore(root: Path) -> list[Finding]:
    path = root / ".gitignore"
    if not path.exists():
        return [
            Finding("Blocker", ".gitignore", "missing-file", ".gitignore is required")
        ]
    text = path.read_text(encoding="utf-8")
    findings: list[Finding] = []
    if not re.search(r"^\.venv/?\s*$", text, re.MULTILINE):
        findings.append(
            Finding(
                "Blocker",
                ".gitignore",
                "missing-venv-rule",
                ".venv/ must be gitignored (PACKAGING.md §4)",
            )
        )
    if not re.search(r"^__pycache__/?\s*$", text, re.MULTILINE):
        findings.append(
            Finding(
                "Finding",
                ".gitignore",
                "missing-pycache-rule",
                "__pycache__/ should be gitignored",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Makefile
# ---------------------------------------------------------------------------

_MAKEFILE_TARGET_RE = re.compile(r"^([a-zA-Z_][\w-]*)\s*:", re.MULTILINE)


def _target_body(text: str, target: str) -> str:
    """Return the recipe lines (tab-indented) for a named target, or '' if absent."""
    m = re.search(
        rf"^{re.escape(target)}\s*:[^\n]*\n((?:[\t][^\n]*\n?)*)",
        text,
        re.MULTILINE,
    )
    return m.group(1) if m else ""


def check_makefile(root: Path) -> list[Finding]:
    path = root / "Makefile"
    if not path.exists():
        return [Finding("Blocker", "Makefile", "missing-file", "Makefile is required")]
    text = path.read_text(encoding="utf-8")
    findings: list[Finding] = []

    found = set(_MAKEFILE_TARGET_RE.findall(text))
    missing = REQUIRED_MAKEFILE_TARGETS - found
    for target in sorted(missing):
        findings.append(
            Finding(
                "Blocker",
                "Makefile",
                f"missing-target:{target}",
                "required canonical target absent; see PACKAGING.md §7",
            )
        )

    for tool in FORBIDDEN_MAKEFILE_TOOLS:
        if re.search(rf"(^|[\s\t])({re.escape(tool)})(\s|$)", text, re.MULTILINE):
            findings.append(
                Finding(
                    "Blocker",
                    "Makefile",
                    f"non-canon-tool:{tool}",
                    f"invocation of {tool!r} is non-canon (PACKAGING.md §1 §2)",
                )
            )

    # Fast `check` = fmt-check + lint + test (pre-commit cadence).
    check_line = re.search(r"^check\s*:\s*(.*?)(?:##|$)", text, re.MULTILINE)
    if check_line is not None:
        deps = set(check_line.group(1).split())
        for required in ("fmt-check", "lint", "test"):
            if required not in deps:
                findings.append(
                    Finding(
                        "Finding",
                        "Makefile",
                        f"check-chain:{required}",
                        f"fast `check` should depend on {required!r} (PACKAGING.md §7)",
                    )
                )

    # install-dev: must depend on install, install the dev group, wire pre-commit.
    decl = re.search(r"^install-dev\s*:\s*(.*?)(?:##|$)", text, re.MULTILINE)
    if decl and "install" not in decl.group(1).split():
        findings.append(
            Finding(
                "Blocker",
                "Makefile",
                "install-dev:missing-install-dep",
                "install-dev must depend on 'install' (PACKAGING.md §7)",
            )
        )
    body = _target_body(text, "install-dev")
    if body and "--group" not in body:
        findings.append(
            Finding(
                "Blocker",
                "Makefile",
                "install-dev:pip-group",
                "install-dev must run 'pip install --group' for the PEP 735 dev group (PACKAGING.md §7)",
            )
        )
    if body and "pre-commit install" not in body:
        findings.append(
            Finding(
                "Blocker",
                "Makefile",
                "install-dev:pre-commit-install",
                "install-dev must run 'pre-commit install' (PACKAGING.md §7)",
            )
        )

    # lock must use pip-tools.
    body = _target_body(text, "lock")
    if body and "piptools compile" not in body:
        findings.append(
            Finding(
                "Blocker",
                "Makefile",
                "lock:piptools-compile",
                "lock must invoke 'piptools compile' (PACKAGING.md §7)",
            )
        )

    # fmt: isort must precede black (DRIFT.md / memory: isort before black).
    body = _target_body(text, "fmt")
    if body:
        isort_pos = body.find("isort")
        black_pos = body.find("black")
        if isort_pos == -1 or black_pos == -1 or isort_pos > black_pos:
            findings.append(
                Finding(
                    "Blocker",
                    "Makefile",
                    "fmt:isort-before-black",
                    "fmt must invoke isort before black (PACKAGING.md §7)",
                )
            )

    # arch must invoke the canonical check scripts.
    body = _target_body(text, "arch")
    for script in ("check_upward_imports.py", "check_packaging.py"):
        if body and script not in body:
            findings.append(
                Finding(
                    "Blocker",
                    "Makefile",
                    f"arch:{script}",
                    f"arch must invoke scripts/{script} (PACKAGING.md §7)",
                )
            )

    # docs must invoke all four doc-coherence scripts.
    body = _target_body(text, "docs")
    for script in (
        "check_docs.py",
        "check_todo_format.py",
        "check_claude_shape.py",
        "check_file_placement.py",
    ):
        if body and script not in body:
            findings.append(
                Finding(
                    "Blocker",
                    "Makefile",
                    f"docs:{script}",
                    f"docs must invoke scripts/{script} (PACKAGING.md §7)",
                )
            )

    # help must use ##@ section markers.
    body = _target_body(text, "help")
    if body and "##@" not in body:
        findings.append(
            Finding(
                "Finding",
                "Makefile",
                "help:no-section-markers",
                "help should use ##@ section markers to group non-canon targets (PACKAGING.md §7)",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# .pre-commit-config.yaml
# ---------------------------------------------------------------------------

_PRECOMMIT_ID_RE = re.compile(r"^\s*-\s*id\s*:\s*([\w-]+)\s*$", re.MULTILINE)


def check_precommit(root: Path) -> list[Finding]:
    path = root / ".pre-commit-config.yaml"
    if not path.exists():
        return [
            Finding(
                "Blocker",
                ".pre-commit-config.yaml",
                "missing-file",
                "required; copy from templates/classic/pre-commit-config.yaml",
            )
        ]
    text = path.read_text(encoding="utf-8")
    found = set(_PRECOMMIT_ID_RE.findall(text))
    findings: list[Finding] = []
    missing = REQUIRED_PRECOMMIT_HOOKS - found
    for hook in sorted(missing):
        findings.append(
            Finding(
                "Blocker",
                ".pre-commit-config.yaml",
                f"missing-hook:{hook}",
                "required hook is not configured",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# CHANGELOG.md
# ---------------------------------------------------------------------------

# A released entry (`## X.Y.Z - YYYY-MM-DD`) or the honest `## [Unreleased]`
# header a freshly-scaffolded changelog carries before its first release.
_CHANGELOG_HEADER_RE = re.compile(
    r"^## (?:\[Unreleased\]|\d+\.\d+\.\d+(?:[-+][\w.-]+)? - \d{4}-\d{2}-\d{2})",
    re.MULTILINE,
)


def check_changelog(root: Path) -> list[Finding]:
    path = root / "CHANGELOG.md"
    if not path.exists():
        return [
            Finding(
                "Finding",
                "CHANGELOG.md",
                "missing-file",
                "CHANGELOG.md is recommended (Keep a Changelog format)",
            )
        ]
    if not _CHANGELOG_HEADER_RE.search(path.read_text(encoding="utf-8")):
        return [
            Finding(
                "Finding",
                "CHANGELOG.md",
                "header-format",
                "no `## [Unreleased]` or `## X.Y.Z - YYYY-MM-DD` heading found (PACKAGING.md §8)",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_all(root: Path) -> list[Finding]:
    """Run every check, in the order PACKAGING.md presents them."""
    findings: list[Finding] = []
    shape, shape_findings = detect_shape(root)
    findings.extend(shape_findings)
    if shape.name == "unknown":
        return findings

    has_canonical_version = False
    if shape.library_pyproject is not None:
        lib_findings, lib_data = check_library_pyproject(root, shape.library_pyproject)
        findings.extend(lib_findings)
        project = lib_data.get("project") if isinstance(lib_data, dict) else None
        has_canonical_version = isinstance(project, dict) and bool(project.get("version"))
        if shape.name == "pypkg+djapp":
            lib_label = _rel_for_audit(root, shape.library_pyproject)
            findings.extend(check_djapp_isort_coverage(root, lib_data, lib_label))
            findings.extend(check_djapp_importlinter_coverage(root, lib_data, lib_label))

    if shape.djapp_pyproject is not None:
        findings.extend(check_djapp_pyproject(root, shape.djapp_pyproject))
    elif shape.name == "pypkg+djapp":
        # Shape detected via djapp/manage.py, but djapp/pyproject.toml is absent.
        # Without it the djapp runtime deps have no canonical home -- a false
        # green if left unflagged (run_all would simply skip djapp validation).
        findings.append(
            Finding(
                "Blocker",
                "djapp/pyproject.toml",
                "missing-file",
                "required for shape pypkg+djapp: PEP 735 [dependency-groups].runtime, no [project]",
            )
        )

    findings.extend(check_legacy_version_file(root, has_canonical_version=has_canonical_version))
    findings.extend(check_requirements(root, shape))
    findings.extend(check_forbidden_lockfiles(root))
    findings.extend(check_forbidden_pylintrc(root))
    findings.extend(check_gitignore(root))
    findings.extend(check_makefile(root))
    findings.extend(check_precommit(root))
    findings.extend(check_changelog(root))
    return findings


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Validate project files against the racecar packaging canon. "
            "See arch-coherence/PACKAGING.md."
        )
    )
    p.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root to validate (default: cwd).",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Treat Findings as Blockers (non-zero exit on any issue).",
    )
    return p


def main() -> int:
    args = parser().parse_args()
    findings = run_all(args.root.resolve())
    if not findings:
        print("packaging: OK")
        return 0
    blockers = sum(1 for f in findings if f.severity == "Blocker")
    other = len(findings) - blockers
    print(f"packaging: {blockers} blocker(s), {other} finding(s)")
    print(
        f"  {'SEVERITY':7s}  {'FILE':32s}  {'RULE':42s}  MESSAGE"
    )
    for f in findings:
        print(f.render())
    if blockers or args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
