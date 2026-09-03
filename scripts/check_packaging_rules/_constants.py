"""Canon definitions (mirror arch-python/PACKAGING.md §3 §6 §7)."""

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
# >=77 is the PEP 639 floor, not a version-chasing bump: below it `[project].license`
# as an SPDX expression is rejected and `license-files` is ignored, so a repo cannot
# state its license in the one place tooling reads. Build-time only -- pip fetches the
# backend in an isolated environment, so the floor costs an adopter nothing at runtime.
CANON_BUILD_REQUIRES = ["setuptools>=77"]
CANON_BUILD_BACKEND = "setuptools.build_meta"

# NOTE: racecar asserts NOTHING about [tool.pylint."MESSAGES CONTROL"].disable. There is no
# required set and no forbidden set, by design. Which pylint messages a repo suppresses is the
# owner's call, and a checker that graded it would make racecar the decider on a judgement that
# is not its to make (shared/OWNERSHIP.md: tooling enables design and confirms correctness;
# responsibility stays with the owner). racecar advises through what it SCAFFOLDS -- see
# templates/classic/library-pyproject.toml -- and a scaffolded default is a starting point the
# owner edits, not a rule enforced forever.
#
# Two sets used to live here and are deleted, not relaxed:
#
#   CANON_PYLINT_REQUIRED_DISABLE  -- codes every repo HAD to suppress. Examined one at a time,
#     it did not survive. Eight were pylint's informational (I) codes, which pylint withholds by
#     default, so requiring their suppression changed no output anywhere -- dead config that
#     read as a judgement. Two were the `use-implicit-booleaness` pair (C1804/C1805), which
#     report an explicit `x == ""` / `n == 0` and ask for `not x`; that is PEP 8 for sequences
#     and what CPython itself does for exit codes (`subprocess.check_returncode` is
#     `if self.returncode:`), so canon had been mandating the suppression of the more Pythonic
#     idiom -- racecar's own 26 contrary sites are now converted. And `duplicate-code` (R0801)
#     was racecar's LOCAL circumstance promoted to a universal rule: racecar ships script trees
#     that are delivered independently and may not import each other, so shared idioms are
#     copied by necessity -- a property almost no adopter has. Requiring it meant no governed
#     repo could detect its own copy-paste, which for a canon aimed at AI-built software is the
#     worst possible check to mandate off. It was over-broad even here: of 35 R0801 violations
#     only 12 are cross-delivery-unit, 23 are extractable.
#
#   CANON_PYLINT_FORBIDDEN_DISABLE -- codes a repo was FORBIDDEN to suppress (the two docstring
#     checks). This is the plainer error: racecar cannot forbid an owner a configuration choice.
#     Demoting it to advisory was still racecar holding an opinion in the gate; the opinion
#     belongs in the docs and the scaffold, and the gate should be silent.
#
# What remains asserted about pylint is config LOCATION, not content: no standalone .pylintrc
# (FORBIDDEN_PYLINTRC below), because tool config has one home. That is a structural rule about
# where a decision is recorded, not a ruling on the decision.
# Standalone pylint config files — forbidden; config lives in the library
# pyproject [tool.pylint] (PACKAGING.md, "pylint canon" + §7).
FORBIDDEN_PYLINTRC = [".pylintrc", "pylintrc", "src/.pylintrc", "server/.pylintrc"]

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

# Package-only hooks: they audit library-package structure and have nothing to act on
# in the flat `django` shape (a config-home site, not a package) — no import-linter
# contracts, no [project] to validate, no src-package upward-imports. Exempted from the
# required set for that shape (SG3), the same reasoning that skips its library-pyproject
# audit. The rest of REQUIRED_PRECOMMIT_HOOKS (format, doc-coherence, todo, placement)
# is shape-independent and stays required.
PACKAGE_ONLY_PRECOMMIT_HOOKS = {
    "import-linter",
    "validate-pyproject",
    "no-upward-imports-in-business-modules",
}

# Make variables retired by a canon rename. A repo-owned scaffold file is not content-synced,
# so a stale reference survives a racecar upgrade: the import-linter hook body calls
# `make -s print-<VAR>`, and a retired <VAR> resolves to empty, silently dropping the server
# root from PYTHONPATH (this is exactly how an adopter's djapp->server migration left a
# broken hook).
# Map each retired name to its current replacement; the precommit check flags any occurrence.
RETIRED_MAKE_VARS = {"DJAPP": "SERVER"}

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
