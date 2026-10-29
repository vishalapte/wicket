"""Canon definitions (mirror arch-python/PACKAGING.md §3 §6 §7)."""

from __future__ import annotations

import re

CANON_DEV_TOOLS = [
    "black",
    "isort",
    # Pinned so an adopter lints racecar's delivered files under the version racecar does.
    # Move the pin here, in racecar's own pyproject, in the template and in racecar.mk's
    # install-dev together.
    "pylint==4.0.9",
    "pylint-pytest",
    "mypy",
    "pytest",
    "pytest-cov",
    "pip-audit",
    "import-linter",
    "pre-commit",
    "validate-pyproject",
    "pyyaml",
    "pytest-xdist",  # parallel test workers; inert until PYTEST_ARGS enables -n (PACKAGING.md §6)
]

# Django shapes carry a second PEP 735 group, [dependency-groups].django. Two
# tools are racecar-canonical there (PACKAGING.md §6): djhtml (template formatter)
# and pylint-django (the pylint plugin that teaches the linter the ORM, loaded by
# racecar.mk's `lint` target on the server). The rest of that group is project-
# choice. Asserted only when the repo is Django.
CANON_DJANGO_TOOLS = ["djhtml", "pylint-django"]

CANON_REQUIRES_PYTHON = ">=3.12"
CANON_BLACK_TARGET = ["py312"]
CANON_ISORT_PROFILE = "black"
# >=77 is the PEP 639 floor: below it an SPDX `[project].license` is rejected and
# `license-files` is ignored. Build-time only.
CANON_BUILD_REQUIRES = ["setuptools>=77"]
CANON_BUILD_BACKEND = "setuptools.build_meta"

# racecar asserts nothing about [tool.pylint."MESSAGES CONTROL"].disable: which messages a
# repo suppresses is the owner's call.
# Standalone pylint config files — forbidden; config lives in the library
# pyproject [tool.pylint] (PACKAGING.md, "pylint canon" + §7).
FORBIDDEN_PYLINTRC = [
    ".pylintrc",
    "pylintrc",
    "src/.pylintrc",
]  # plus each Django folder's

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
    "file-placement",
}

# Package-only hooks: they audit library-package structure, so the flat `django` shape is
# exempt from them (SG3). The rest of REQUIRED_PRECOMMIT_HOOKS stays required.
PACKAGE_ONLY_PRECOMMIT_HOOKS = {
    "import-linter",
    "validate-pyproject",
    "no-upward-imports-in-business-modules",
}

# Make variables racecar.mk no longer defines, each mapped to its replacement, or to "" when
# racecar.mk now derives the value and nothing replaces it. A repo-owned file is not synced, so
# a retired `$(VAR)`, `make -s print-<VAR>` or `VAR := ...` silently means nothing; the Makefile
# and pre-commit checks flag each. `VENV`, `PYTHON`, `PIP`, `BIN` and `SERVER` are names many
# tools use, so racecar.mk's carry `RC_` to say they are its own.
RETIRED_MAKE_VARS = {
    "DJAPP": "RC_DJANGO",
    "SERVER": "RC_DJANGO",
    "SERVER_PYPROJECT": "",
    "VENV": "RC_VENV",
    "PYTHON": "RC_PYTHON",
    "PIP": "RC_PIP",
    "BIN": "RC_BIN",
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

# A released version: exactly `X.Y.Z`, no prerelease or build tag. What a version home, a
# changelog heading and a brief stamp carry.
RELEASE_RE = re.compile(r"^\d+\.\d+\.\d+$")

# The title of a CHANGELOG release heading, `X.Y.Z - YYYY-MM-DD` (a prerelease or build tag
# allowed), and of the section a release folds in. Matched against a heading's title as the
# markdown reader gives it (`_markdown.Document.headings(2)`), never against a raw line.
RELEASE_TITLE_RE = re.compile(r"(\d+\.\d+\.\d+(?:[-+][\w.-]+)?) - (\d{4}-\d{2}-\d{2})")
UNRELEASED_TITLE = "[Unreleased]"

# Where racecar's files land in an adopter, and what sync writes there to record it.
# A verbatim copy of `racecar.lib.delivery.record`, because a delivered checker cannot import
# racecar; `tests/slow/test_delivered_record_is_found.py` holds the two equal. The legacy
# spellings stay because an adopter that has not synced recently has its record there.
DELIVERY_DIR = ".racecar/scripts"
DELIVERED_RECORD_REL = ".racecar-delivered.txt"

# The boundary root, and the two provenance stamps sync writes at it, one per delivered
# tree, each holding git's own tree object id for what racecar delivered.
DELIVERY_ROOT = ".racecar"
LEXICON_STAMP_REL = ".racecar-lexicon"
SCRIPTS_STAMP_REL = ".racecar-scripts"
LEGACY_DELIVERY_DIRS = ("rc_scripts", "scripts")
LEGACY_DELIVERY_DIR = LEGACY_DELIVERY_DIRS[0]
LEGACY_DELIVERED_REL = "racecar-manifest.txt"

# Every place a delivered record has ever lived, newest first.
DELIVERED_RECORDS = (
    f"{DELIVERY_ROOT}/{DELIVERED_RECORD_REL}",
    f"{DELIVERY_DIR}/{DELIVERED_RECORD_REL}",
    *(f"{legacy}/{DELIVERED_RECORD_REL}" for legacy in LEGACY_DELIVERY_DIRS),
    *(f"{legacy}/{LEGACY_DELIVERED_REL}" for legacy in LEGACY_DELIVERY_DIRS),
)
