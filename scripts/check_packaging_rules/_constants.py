"""Canon definitions (mirror arch-coherence/PACKAGING.md §3 §6 §7)."""

from __future__ import annotations

import re

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
    "pre-commit",
    "validate-pyproject",
    "pyyaml",
]

# Django shapes carry a second PEP 735 group, [dependency-groups].django. Two
# tools are racecar-canonical there (PACKAGING.md §6): djhtml (template formatter)
# and pylint-django (the pylint plugin that teaches the linter the ORM, loaded by
# racecar.mk's `lint` target on the djapp). The rest of that group is project-
# choice. Asserted only when the repo is Django.
CANON_DJANGO_TOOLS = ["djhtml", "pylint-django"]

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
    "file-placement",
}

REQUIRED_MAKEFILE_TARGETS = {
    "help",
    "install",
    "install-dev",
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
