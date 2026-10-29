# racecar.mk — canonical racecar build. Synced by racecar; DO NOT EDIT.
#
# This file is IDENTICAL in every racecar project — it carries no per-repo content.
# It detects the project shape from what is on disk (PACKAGING.md "Scope") and
# selects the matching source variables, falling back to stock for any layout it
# does not recognize. Project-specific targets and variable overrides belong in the
# owned `Makefile`, which `include`s this file; edits here are lost on `make racecar-sync`.
#
# Tools are invoked as `$(RC_PYTHON) -m <tool>` / `$(RC_BIN)/<tool>` (never bare names),
# so no target needs an *activated* venv — `make check` works from a cold shell,
# sidestepping the GNU Make 3.81 (macOS) PATH-export bug where `export PATH :=` is
# ignored for non-shell execvp lookups. Requires pip >= 25.1 for PEP 735.

# --- shape: governed by what is on disk. The same decision check_packaging.detect_shape
# makes, expressed in Make so racecar.mk stays self-contained (no script call to know the
# shape). Shape = PYTHON_LIBRARY x DJANGO_PROJECT (manage.py). The library is src/<pkg>, or,
# with no src/, the package [project].name names at the repo root (flat_package in
# lib/shared/_root.py; `-` and `.` read as `_`): src / flat (library only), src+server /
# flat+server (library x Django projects in their own folders), server (those projects alone,
# no library), django (a root manage.py site — the django-admin startproject canon, no
# library). Django is marked by a manage.py, never a folder name: RC_DJANGO lists every
# top-level, non-hidden folder holding a manage.py that git does not ignore (arch-python/
# MAKEFILE.md). A root manage.py beside a library is not the django shape (a library's Django
# belongs in its own folder), so it degrades to the library reading. TODO: the {packages,pypkg}/<pkg>/src/<pkg> library-axis workspace
# form is a downstream addition. ---
# A manage.py counts only where git does not ignore it, so a vendored or scratch copy never
# makes a Django project; outside a git repository every file counts.
_RC_IN_GIT  := $(shell git rev-parse --is-inside-work-tree 2>/dev/null)
_RC_GIT_FILES = $(shell git ls-files --cached --others --exclude-standard -- '$(1)' 2>/dev/null)
_RC_DJANGO_MNG := $(filter-out .%,$(if $(_RC_IN_GIT),$(call _RC_GIT_FILES,:(glob)*/manage.py),$(wildcard */manage.py)))
_RC_DJANGO_DIRS := $(patsubst %/manage.py,%,$(_RC_DJANGO_MNG))
_ROOT_MNG   := $(if $(_RC_IN_GIT),$(call _RC_GIT_FILES,manage.py),$(wildcard manage.py))
_SRC_DIR    := $(wildcard src)
_ROOT_PY    := $(wildcard pyproject.toml)
_PROJECT_NAME := $(shell sed -n "/^\[project\]/,/^\[/{s/^name *= *[\"']\([^\"']*\)[\"'].*/\1/p;}" pyproject.toml 2>/dev/null | head -n 1)
_PROJECT_MODULE := $(subst .,_,$(subst -,_,$(_PROJECT_NAME)))
_ROOT_PKG   := $(if $(_SRC_DIR),,$(if $(_PROJECT_MODULE),$(wildcard $(_PROJECT_MODULE)/__init__.py)))

ifeq ($(_ROOT_PY),)
  SHAPE := stock
else ifneq ($(_RC_DJANGO_MNG),)
  ifneq ($(_SRC_DIR),)
    SHAPE := src+server
  else ifneq ($(_ROOT_PKG),)
    SHAPE := flat+server
  else
    SHAPE := server
  endif
else ifneq ($(_SRC_DIR),)
  SHAPE := src
else ifneq ($(_ROOT_PKG),)
  SHAPE := flat
else ifneq ($(_ROOT_MNG),)
  SHAPE := django
else
  SHAPE := stock
endif

# --- variables: one block per shape, stock for any other value. Set with ?= so the owned
# Makefile can override any by assigning it with := BEFORE `include racecar.mk`. ---
ifeq ($(SHAPE),src)
  SRC ?= src
  LIB_PYPROJECT ?= pyproject.toml
else ifeq ($(SHAPE),src+server)
  SRC ?= src
  RC_DJANGO ?= $(_RC_DJANGO_DIRS)
  LIB_PYPROJECT ?= pyproject.toml
else ifeq ($(SHAPE),flat)
  # The flat layout: identical to src except the package root is the repo root, so SRC
  # is the package itself, the equivalent of src/ holding only the package.
  SRC ?= $(_PROJECT_MODULE)
  LIB_PYPROJECT ?= pyproject.toml
else ifeq ($(SHAPE),flat+server)
  SRC ?= $(_PROJECT_MODULE)
  RC_DJANGO ?= $(_RC_DJANGO_DIRS)
  LIB_PYPROJECT ?= pyproject.toml
else ifeq ($(SHAPE),server)
  SRC ?= $(_RC_DJANGO_DIRS)
  RC_DJANGO ?= $(_RC_DJANGO_DIRS)
  LIB_PYPROJECT ?= pyproject.toml
else ifeq ($(SHAPE),django)
  # Startproject Django site (root manage.py, no library): the whole repo root is the tree.
  # The owned Makefile SHOULD narrow SRC to the real first-party app dirs (e.g.
  # `SRC := apps mysite`) before the include; `.` is the honest, broad default.
  SRC ?= .
  RC_DJANGO ?= .
  LIB_PYPROJECT ?= pyproject.toml
else
  SRC ?= src
  LIB_PYPROJECT ?= pyproject.toml
endif

# PKG: the repo's package, the subject of every package check (check_upward_imports,
# check_deferred_imports, check_cli_commands, coverage). The same answer `package_dir`
# (lib/shared/_root.py) gives in Python, decided again here because the build must work
# with nothing but `make`; a test holds the two in step. In the flat shapes SRC is the
# package; under `src`, the one package, or with several the one
# `[project].name` names (`-` and `.` read as `_`). Otherwise EMPTY: a Django-only repo
# (`server`, `django`) and a repo with several packages and none named have no package,
# and a package check there says so rather than guessing one. Override with
# `PKG := ...` before the include.
_SRC_PKGS := $(patsubst %/,%,$(dir $(wildcard $(SRC)/*/__init__.py)))
ifneq ($(filter flat flat+server,$(SHAPE)),)
  PKG ?= $(SRC)
else ifeq ($(SRC),src)
  PKG ?= $(if $(filter 1,$(words $(_SRC_PKGS))),$(_SRC_PKGS),$(filter src/$(_PROJECT_MODULE),$(_SRC_PKGS)))
else
  PKG ?=
endif
# Why a check does not apply, in the owner's three cases, worded as `not_present` words
# them (lib/shared/_root.py): a package and no Django server, a Django server and no
# package, or neither.
_HAS_DJANGO := $(filter src+server flat+server server django,$(SHAPE))
_NOT_PRESENT = $(if $(PKG),No django server to check,$(if $(_HAS_DJANGO),No package to check,Nothing to check))

# Shared defaults (no-op when a block above already set them).
RC_DJANGO ?=
# Each Django project's own pyproject, whose `runtime` group `install` adds: only where a library
# sits beside the projects. Derived, never set: RC_DJANGO is its one home.
_RC_DJANGO_PYPROJECT := $(if $(filter src+server flat+server,$(SHAPE)),$(foreach d,$(RC_DJANGO),$(wildcard $(d)/pyproject.toml)))

# Is there a Django project shell here? The same question the `arch` Django check asks,
# answered in Make rather than in a recipe's shell, because the ANSWER decides whether a
# delivered script is expected to exist at all — and script resolution happens at expansion
# time, before any shell runs (see `racecar_script` below). Reads $(RC_DJANGO) rather than the
# literal `server/` of _RC_DJANGO_MNG above, so a repo that renamed its server dir before the
# include is still read correctly; the `$(if $(RC_DJANGO),...)` keeps an empty RC_DJANGO from
# probing `/manage.py` at the filesystem root.
#
# One direction of this matters: sync_delivery.deliver._is_django_repo delivers the Django checker on
# ANY manage.py anywhere in the tree, which is BROADER than root-or-$(RC_DJANGO). So non-empty
# here always implies the script was delivered, which is the invariant the guard needs.
_DJANGO_MNG := $(strip $(_ROOT_MNG) $(wildcard $(addsuffix /manage.py,$(RC_DJANGO))))

# A VENV given on make's command line names racecar's venv, as it did before RC_VENV; a VENV
# from the environment may belong to another tool and is not read. A VENV and an RC_VENV that
# disagree are refused before anything runs.
_RC_VENV_ARG := $(if $(filter command line,$(origin VENV)),$(VENV))
ifneq ($(and $(_RC_VENV_ARG),$(RC_VENV)),)
ifneq ($(_RC_VENV_ARG),$(RC_VENV))
$(error VENV=$(VENV) and RC_VENV=$(RC_VENV) disagree; pass one)
endif
endif

# --- toolchain (shape-independent) ---
# The venv: RC_VENV from the command line or the environment, else VENV from the command line,
# else the first of .venv, venv, ../venv that exists, else .venv, where `make venv` creates it.
# arch-python/MAKEFILE.md is the one home for how the venv is chosen and repaired.
ifeq ($(origin RC_VENV),undefined)
RC_VENV   := $(or $(_RC_VENV_ARG),$(firstword $(wildcard .venv venv ../venv)),.venv)
endif
export PATH := $(abspath $(RC_VENV))/bin:$(PATH)
# RC_ marks these as racecar's: VENV, PYTHON, PIP and BIN are names many tools use.
# _RC_PYTHON is the venv's interpreter when it really runs. realpath is empty for one that is
# missing and for one whose Python was removed. Without one, RC_PYTHON is python3, so a target
# run before any install uses the system interpreter, as it always has.
_RC_PYTHON := $(if $(realpath $(RC_VENV)/bin/python),$(RC_VENV)/bin/python)
RC_PYTHON := $(or $(_RC_PYTHON),python3)
# Every install uses the venv's pip, even before the venv exists, so a first-run install
# cannot land in the system interpreter.
RC_PIP    := $(RC_VENV)/bin/python -m pip
RC_BIN    := $(if $(_RC_PYTHON),$(RC_VENV)/bin,$(HOME)/.local/bin)

# Library install directory (where pip -e installs from).
LIB_DIR := $(if $(filter pyproject.toml,$(LIB_PYPROJECT)),.,$(patsubst %/pyproject.toml,%,$(LIB_PYPROJECT)))

# Extra pytest args, e.g. make test PYTEST_ARGS="-k foo -q"
PYTEST_ARGS ?=

# Which tests `test-fast` skips, BY PATH. A test whose cost IS its claim -- it runs a real
# compiler, a real `make`, a real `ansible-inventory` -- cannot be made fast without
# deleting the claim it makes. Those live in a `slow/` directory beside their peers, so the
# tier a test belongs to is its location (PRINCIPLES.md R-04) rather than a marker someone
# remembers to apply.
#
# It must be a path filter, and markers cannot substitute. `-m` and `-k` select AFTER
# collection, so every module is still imported and its import cost is already spent:
# deselecting all 1750 tests in this repo still costs 0.86s against 0.14s of fixed startup.
# `--ignore-glob` is read during collection, so pytest never opens the file.
#
# An adopter with no `slow/` directory gets a `test-fast` identical to `test`, which is the
# correct degenerate case: nothing is declared slow, so nothing is skipped.
SLOW_TIER_GLOB ?= */slow/*

# The THIRD tier, and the distinction is not duration. A `slow/` test is slow because its
# claim needs a real compiler or a real `make`; a `macro/` test RECREATES A UNIVERSE to
# test the universe -- it builds a repo tree and runs racecar against it as a subprocess.
# That is the most valuable kind of coverage and the wrong thing to pay for on every
# change: each such test starts processes of its own, so running them beside gate legs
# that each size themselves to every core overloads the machine. `check-full-full` runs
# them on a cadence the owner picks.
#
# Same mechanism as the slow tier for the same reason: the tier is a LOCATION, discovered
# from the tree, so a new `macro/` directory joins with nothing to remember (R-04).
MACRO_TIER_GLOB ?= */macro/*

# The two knobs `test-fast` re-enters `test` with. Defaults ARE the `test` behaviour, so a
# direct `make test` is unchanged by their existence.
XDIST_N ?= auto
PYTEST_EXTRA ?=
# Where each test leg writes its JUnit records (`$(RESULTS_DIR)/$(TEST_LEG).xml`), and the
# leg's name: `test` for a bare `make test`, `fast`, `slow` or `macro` for the tiers. One file
# per leg, so `check-full`'s parallel legs never write the same file.
#
# In the checkout's `.rclocal/`, racecar's per-machine folder, which the canon `.gitignore`
# ignores: each checkout keeps its own results, and no folder is shared between users or
# clones. Where git does NOT ignore `.rclocal`, the file is deleted as soon as its verdict
# prints, so it can never be committed; nothing else in `.rclocal/` is touched, and
# `check_packaging` reports the missing ignore line. A RESULTS_DIR given on the command line
# (check_red passes its own scratch folder) is the caller's and is never deleted.
RESULTS_LOCAL := .rclocal/test/results
RESULTS_DIR ?= $(RESULTS_LOCAL)
TEST_LEG ?= test

# Where the racecar checkout lives, used by `make racecar-sync`. Discovered from the
# installed skill symlink so no machine-specific path is baked in. Override with
# `make sync racecar-sync racecar-upgrade RACECAR_ROOT=/path/to/racecar` if racecar is not installed as a skill.
RACECAR_ROOT ?= $(shell readlink "$(HOME)/.claude/skills/racecar" 2>/dev/null)

# Where the delivered check scripts live. `scripts/` in every adopter, because that is where
# `make racecar-sync` vendors them -- so this default makes the recipes below identical to what they
# have always been.
#
# It is a variable so a repo that keeps its checkers somewhere else can say so. racecar
# itself no longer needs that: its scripts live in one flat `scripts/`, the same shape
# delivery produces, so the default is correct in the canon repo too. It was not always --
# racecar used to hold them under eight lens directories and override this, which is why
# the variable exists at all.
#
# Space-separated search path; the first directory containing the script wins.
#
# A name that resolves nowhere is an `$(error)`. `$(wildcard)` yields EMPTY on a miss, so
# without it `$(RC_PYTHON) $(call racecar_script,x.py)` degrades to a bare `python3`, which
# reads EOF and exits 0 -- a gate reporting green having run nothing (R-10).
#
# CONSEQUENCE FOR ANYONE ADDING A CALL SITE. `$(error)` fires at EXPANSION time, and Make
# expands a target's whole recipe before running its first line. A shell `if` around the
# call therefore cannot skip it, and the failure takes the entire target, not one line. So
# a script that is only SOMETIMES delivered needs a MAKE-level guard. There is one today:
# `sync_scripts.DJANGO_SCRIPTS` ships check_dj_model_ref_as_string.py only to a repo with a
# manage.py, and its call site sits behind `ifneq ($(_DJANGO_MNG),)` in `_arch`, which keeps
# the untaken branch out of the recipe so the `$(call)` never expands. Anything added to a
# conditional manifest needs the same.
# Where `make` and the pre-commit hooks find racecar's checkers: the directory sync
# delivers them into. The same value in racecar and in every adopter, because racecar
# runs this file AS AN ADOPTER -- it commits its own `.racecar/scripts/` like anyone else.
# racecar's own CLI (`python -m racecar.*`) is the other caller and reaches `scripts/`
# directly, because that is racecar's source rather than a copy of it.
#
# TWO entries, and the order is the whole point. `.racecar/scripts` is where delivery puts
# them now; `rc_scripts` is where it put them before `.racecar/` became one boundary for
# everything racecar owns in a repo. An adopter is on the old layout until the sync that
# moves them, and that sync runs `make` -- so a single-entry default would break the gate
# of every repo in exactly the window the upgrade needs it working. The retired name is
# searched second and costs a `wildcard` miss.
RACECAR_SCRIPT_DIRS ?= .racecar/scripts rc_scripts
racecar_script = $(or $(firstword $(wildcard $(addsuffix /$(1),$(RACECAR_SCRIPT_DIRS)))),\
  $(error racecar: $(1) not found in RACECAR_SCRIPT_DIRS ($(RACECAR_SCRIPT_DIRS)) -- run 'make racecar-sync', or set RACECAR_ROOT if this is racecar itself))


.DEFAULT_GOAL := help
.PHONY: help venv install install-dev check _check check-full fix fmt fmt-check lint \
        test test-fast test-slow test-macro check-full-full check-tests lint-tests _check_static coverage typecheck arch _arch audit docs clean distclean sync system-deps \
        check-overrides cron cron-list

help: ## Show this help
	@awk 'BEGIN{FS=":.*?## ";n=0} \
	  function p(n,k,v, i,j,t){for(i=0;i<n;i++)for(j=i+1;j<n;j++)if(k[i]>k[j]){t=k[i];k[i]=k[j];k[j]=t;t=v[i];v[i]=v[j];v[j]=t};for(i=0;i<n;i++)printf v[i]} \
	  /^##@/{p(n,k,v);n=0;delete k;delete v;printf "\n\033[1m%s\033[0m\n",substr($$0,5)} \
	  /^[a-zA-Z_-]+:.*?## /{k[n]=$$1;v[n]=sprintf("  \033[36m%-14s\033[0m %s\n",$$1,$$2);n++} \
	  END{p(n,k,v)}' $(MAKEFILE_LIST)

# print-<VAR>: echo a resolved make variable, e.g. `make -s print-LIB_PYPROJECT`. The
# pre-commit hooks read shape-derived config (LIB_PYPROJECT / RC_DJANGO) through this rather
# than grepping the owned Makefile, which no longer holds them: the fold moved them into
# racecar.mk and the value is computed from the layout, so only Make can resolve it.
print-%:
	@echo '$($*)'

# `make venv` makes the venv's pip run, since pip is what every install needs. A working
# venv is left alone. Where Python runs and only pip is missing (a `uv venv`, a run
# interrupted before pip was installed), ensurepip adds pip and keeps what is installed.
# Where Python does not run (an empty directory, a removed Python), the venv is rebuilt;
# --clear only where pyvenv.cfg says the directory is already a venv.
venv: ## Create the venv (RC_VENV, default .venv) if its pip does not run
	@$(RC_PIP) --version >/dev/null 2>&1 \
	  || $(RC_VENV)/bin/python -m ensurepip >/dev/null 2>&1 \
	  || { echo "venv: building $(RC_VENV)"; python3 -m venv $(if $(wildcard $(RC_VENV)/pyvenv.cfg),--clear) $(RC_VENV); }

install: venv system-deps ## Bootstrap: editable library install + system deps
	$(RC_PIP) install -q --upgrade 'pip>=25.1'
	$(RC_PIP) install -q -e $(LIB_DIR)
	@for p in $(_RC_DJANGO_PYPROJECT); do $(RC_PIP) install -q --group $$p:runtime || exit 1; done

system-deps: ## Install system dependencies outside pip (see scripts/install_system_deps.sh)
	bash scripts/install_system_deps.sh

# The second pip line installs what racecar's delivered files need, whatever the repo's dev
# group says: sync never rewrites that group, so an older repo's copy lacks the pin and the stubs.
# The pylint pin moves with CANON_DEV_TOOLS (scripts/lib/shared/_constants.py).
install-dev: install ## install + PEP 735 dev group + pre-commit hooks (requires pip >= 25.1)
	$(RC_PIP) install -q --group $(LIB_PYPROJECT):dev
	$(RC_PIP) install -q 'pylint==4.0.9' types-PyYAML
	@if git rev-parse --git-dir >/dev/null 2>&1; then $(RC_VENV)/bin/pre-commit install --hook-type pre-commit --hook-type commit-msg; else echo "install-dev: skipping pre-commit install (not a git repo)"; fi
	@if grep -qi '"django' $(LIB_PYPROJECT); then $(RC_PIP) install -q --group $(LIB_PYPROJECT):django; fi

check: _check ## Fast gate (pre-commit cadence): format, lint, docs, fast tests

# Private gate body. The public target names only this, so an owned Makefile extends the
# gate by adding prerequisites to `_check` rather than redefining a canonical recipe.
_check: fmt-check lint test-fast docs

check-full: _check_full ## Full gate (pre-push / CI cadence) — adds typecheck + arch + the slow tests

# `check-full` plus the macro tier, SERIAL after it. Not another leg on the fan: the macro
# tests spawn their own subprocesses, so adding them to a fan whose legs already size
# themselves to every core is what the tier exists to stop.
check-full-full: ## check-full plus the macro tier — a cadence you choose, not every change
	@$(MAKE) --no-print-directory check-full
	@$(MAKE) --no-print-directory test-macro

# Private gate body, same reason as `_check` above. It runs `_arch`, the PRIVATE body, not
# the public `arch` target, so the pair stays one shape across all three gates.
#
# Depends on `_check` itself -- never restates its members -- so `check-full` is a TRUE
# superset of `check` rather than a second, independently-maintained list that silently
# drifts from it. This repo's own root Makefile extends `_check` with racecar-check,
# check-architecture, check-skill-reachability and check-script-ownership (self-hosting
# racecar's own gates beyond what it delivers); listing `fmt-check lint test` here by name
# instead of depending on `_check` meant `check-full` silently never ran any of the four,
# despite its own description claiming to be the full gate.
# The legs run in PARALLEL and render in a FIXED order, which `make -j` alone cannot do:
# `--output-sync=target` is a make 4.0 option and macOS ships 3.81, where passing it is a
# hard error rather than a no-op. `gate_orchestrate.py` is that flag as a stdlib script --
# it spawns the legs at once and prints each one whole, in the order named here, as soon as
# every leg before it has finished. See its module docstring.
#
# `_check` and the test legs are disjoint: one grades the code and
# runs the fast tier, the other grades the suites and runs the slow tier. Nothing is done
# twice, so the two are a partition rather than an overlap -- which is what makes running
# them concurrently safe rather than merely faster.
#
# Still depends on `_check` transitively through that sub-make, so `check-full` remains a
# TRUE superset of `check`: restating its members here by name is what once let `check-full`
# silently run none of the four gates this repo adds locally.
_check_full:
	@$(RC_PYTHON) $(call racecar_script,gate_orchestrate.py) --make "$(MAKE)" \
	  _check lint-tests test-slow _check_static

# The three read-only graders, grouped so the fan-out above is one sub-make rather than a
# serial prologue followed by a parallel pair. They share no output and write nothing.
_check_static: typecheck _arch

audit: ## pip-audit dependency vulnerability scan (standalone; run weekly / on-demand)
	$(RC_PYTHON) -m pip_audit --strict

fix: fmt ## Auto-fix formatting (isort + black; djhtml for Django templates)

# Every Python file inside the .gitignore boundary, NUL-separated: tracked files, plus new
# ones git is not ignoring. NUL and `xargs -0` because a path with a space in it must not
# become two arguments — the formatters would then rewrite whichever file the fragment
# happened to name, or fail on a path that does not exist.
# TWO HALVES, NOT ONE PIPELINE, and that is the point. A pipeline reports only its LAST
# stage, so `git ls-files | filter` hides a git failure behind a filter that succeeded on the
# empty input git handed it -- the formatters would then be given nothing and every target
# would pass. Kept apart, each half is redirected and each status is read on its own line.
# `.racecar/templates/` is left out: its `.py` files are text `surface.py` renders into a
# package, not code this repo runs, so neither rulebook grades them.
PYFILES_GIT = git ls-files -z --cached --others --exclude-standard -- '*.py' ':!.racecar/templates/'
PYFILES_FILTER = $(RC_PYTHON) $(call racecar_script,check_content_blind.py) --exclude-delivered
PYFILES_DELIVERED = $(RC_PYTHON) $(call racecar_script,check_content_blind.py) --only-delivered
# The Django projects' own files of one suffix, as git lists them: never a walk of $(RC_DJANGO),
# which in the django shape is `.` and would reach an in-tree .venv.
_RC_DJANGO_FILES = git ls-files -z --cached --others --exclude-standard -- $(foreach d,$(RC_DJANGO),'$(d)/*.$(1)')

# CANON'S RULEBOOK FOR THE FILES RACECAR DELIVERED. The gates used to grade the delivered
# tree with the ADOPTER's pyproject.toml, which is the wrong rulebook for files the adopter
# did not write and cannot edit -- the next sync overwrites whatever the finding named. The
# scope decision was right and stays: delivered files are graded. They are graded as
# racecar's.
#
# Resolved by wildcard rather than `racecar_script`, because ABSENT IS A LEGITIMATE STATE
# and must not error: a repo that never synced has no canon file and no delivered files
# either, so both halves of the split are empty and every gate runs exactly as before.
# racecar itself is that repo -- it authors these files rather than receiving them.
#
# At the ADOPTER ROOT, not under $(RACECAR_SCRIPT_DIRS): isort's first-party-import
# detection is sensitive to the settings file's own filesystem location, and a copy
# nested a directory below the tree it grades misclassifies a sibling module as
# third-party (issue #47). Root-relative and independent of RACECAR_SCRIPT_DIRS --
# canon's rulebook is not itself one of the check scripts it governs.
RACECAR_CANON = $(firstword $(wildcard racecar-canon.toml))

# The same delivered set as a regex, because pylint and mypy take a scope as DIRECTORIES and
# subtract by pattern where the formatters take a file list. Lazily assigned: it costs a
# python start, and `make help` must not pay for it. Empty means nothing was delivered, and
# every use below is guarded on that -- an empty --ignore-paths would match everything.
#
# MEMOIZED past that first cost, and that second part is not optional. `lint`'s own ignore-path
# composition below references this more than once by construction -- an emptiness test plus
# the value itself, repeated at every layer of the `$(if)`/`$(and)` chain it feeds -- and a
# lazily-assigned (`=`) variable RE-RUNS its `$(shell)` on every single reference; Make caches
# nothing between them. Measured in this repo: 12 spawns of check_content_blind.py per
# `make lint` for what should be one. `:=` would fix the count but pays the python start on
# EVERY make invocation, including `make help` -- exactly the cost the paragraph above already
# refuses. So this caches its own result the first time anything asks for it within one `make`
# run, and stays lazy otherwise: nothing runs until that first reference, whichever target it
# turns out to be. The DONE sentinel is separate from the cached value because the value is
# routinely empty (nothing delivered, the common case) and `$(if EMPTY,...)` cannot tell
# "not yet computed" from "computed and empty" on its own. The `$(subst $$,$$$$,...)` around
# the `$(shell)` call matters and is not decorative: `$(eval)` expands its argument once to
# build the `VAR := ...` line and then parses THAT as a second, independent piece of makefile
# text, so a raw `$` surviving from the shell's own output (this is a regex; `$` anchors are
# routine) gets treated as the start of a variable reference on that second pass and silently
# vanishes. Doubling it first means the second pass collapses `$$` back to the one `$` the
# shell actually printed, instead of eating it.
DELIVERED_IGNORE = $(if $(_DELIVERED_IGNORE_DONE),$(_DELIVERED_IGNORE_CACHE),$(eval _DELIVERED_IGNORE_CACHE := $(subst $$,$$$$,$(shell $(RC_PYTHON) $(call racecar_script,check_content_blind.py) --delivered-regex 2>/dev/null)))$(eval _DELIVERED_IGNORE_DONE := 1)$(_DELIVERED_IGNORE_CACHE))

# A racecar-delivered file is subtracted here, at the point the list is built, and not in
# either formatter's config. black honours `exclude` / `extend-exclude` only while WALKING a
# directory: a path named explicitly on the command line -- which is exactly what this list
# becomes -- bypasses both, and only `force-exclude` survives it. So the two forms disagree
# on one tree and one config, and `fmt` WRITES, which made this a silent rewrite of files the
# repo does not own rather than a red check.
#
# isort has the SAME asymmetry and was wrongly recorded here as not having it. Its `skip` /
# `skip_glob` are honoured while walking and defeated by an explicit path unless
# `--filter-files` is passed -- measured: walk 0, explicit 1, explicit with the flag 0. So
# both recipes pass it, and a repo whose config skips `*/migrations/*` gets that skip back.
#
# The membership question is answered by `check_content_blind.py --exclude-delivered`, which
# owns the delivery record's format, rather than by a second parser here. Which files racecar
# delivered is RACECAR's fact and its record already ships; `force-exclude` would move the
# rule into the adopter's own pyproject.toml, where every repo must declare it and can forget.
#
# In racecar itself this subtracts NOTHING, and that is the point: racecar AUTHORS the files
# it delivers, so it must keep formatting them. Only a repo that received them by sync has
# something to exclude, and only there would `make fmt` and `racecar sync` otherwise fight
# with no fixed point.

# The formatters are fed the .gitignore boundary AS A LIST, because "which files are ours"
# already has an answer and it is `.gitignore`. `PYFILES` asks git for it: tracked files plus
# new ones that are not ignored. One home for the boundary, and no second list that can drift
# from it.
#
# The list is what makes this fast, and the reason is not obvious. Pointing the formatters at
# `.` and letting them filter is correct and costs 6.7s here, because `--gitignore` and
# black's native handling both apply AFTER the walk -- they fix the scope and never touched
# the cost. The same files named up front: 0.44s. `--others --exclude-standard` is not
# optional in that command: without it a brand-new file is invisible to `fmt-check` and gets
# reformatted by the commit hook, which is precisely the failure described below.
#
# This replaced FMT_PATHS := $(SRC) $(RC_DJANGO) $(wildcard tests). That list was NARROWER than
# the pre-commit hooks, which carry `types: [python]` and format every changed file. So the
# old arrangement had a live failure: edit a root-level conftest.py or tools/ script, `make
# fix` skips it, `fmt-check` passes, and then the commit hook reformats it. The comment that
# used to sit here described exactly that failure and fixed it only for `tests` by bolting
# one more path on. `.` fixes the general case, which is what "formatting scope must match
# the hooks', or the gate is not the gate" actually requires.
#
# To keep a subtree out: `.gitignore` it. That is the whole boundary. Third-party code
# committed into the tree and not gitignored is the owner's problem to declare, not racecar's
# to guess around.
# THE LIST IS BUILT ONCE, INTO A FILE, AND EACH TOOL IS GRADED ON ITS OWN STATUS. Both halves
# of that are defects this replaced, and each was silent.
#
# The file list was a PIPELINE piped into `xargs`, and a pipeline reports only its last stage.
# A failure upstream -- the filter's script missing, a delivery record it cannot read, git
# itself failing -- read as xargs succeeding on an empty list, so both targets reported success
# having formatted nothing. Each stage now lands in a file and is graded on its own line, so
# there is no position in the chain whose failure is invisible.
#
# And `out=$( { isort; black; } )` took `status=$?` from the GROUP, which is the exit of its
# LAST command. An isort-only failure was discarded: measured, isort exit 1 and black exit 0
# gave `fmt-check: OK` and exit 0. Each tool now sets `rc` itself, so neither can hide the
# other, and both run rather than the first stopping the second -- a formatting report is
# worth more whole.
fmt: ## Format in place (isort orders imports, black formats, djhtml reindents Django templates)
	@raw=$$(mktemp); list=$$(mktemp); trap 'rm -f "$$raw" "$$list"' EXIT; \
	  $(PYFILES_GIT) >"$$raw" || { echo "fmt: git could not list the python files" >&2; exit 1; }; \
	  $(PYFILES_FILTER) <"$$raw" >"$$list" || { echo "fmt: could not filter the delivered files" >&2; exit 1; }; \
	  if [ -s "$$list" ]; then \
	    xargs -0 $(RC_PYTHON) -m isort --filter-files --settings-file $(LIB_PYPROJECT) <"$$list" && \
	    xargs -0 $(RC_PYTHON) -m black --config $(LIB_PYPROJECT) <"$$list"; \
	  fi
	$(if $(RC_DJANGO),@l=$$(mktemp); trap 'rm -f "$$l"' EXIT; $(call _RC_DJANGO_FILES,html) >"$$l" && if [ -s "$$l" ]; then xargs -0 $(RC_BIN)/djhtml <"$$l"; fi)

# The COUNT is printed before either formatter runs, and it is the whole of what stops a
# green from being ambiguous. A repo whose scope yields zero python files prints
# `fmt-check: OK` and exits 0 -- measured, not supposed -- which is indistinguishable from
# grading every file and finding nothing wrong. That is the shape this repo names as the
# failure a checker must not have: reporting OK for having read nothing. The delivered leg
# below already printed its count; the owned leg did not, so the half that grades almost
# everything was the half that said least.
#
# A broken scope is loud already: `git ls-files` outside a repository exits non-zero and the
# `||` clause below turns that into `fmt-check: git could not list the python files` and exit
# 1, which fails `make check`. An EMPTY scope is the quiet case, and a count is what makes it
# visible without turning a legitimate empty repo into a failure.
fmt-check: ## Check formatting only — no writes
	@raw=$$(mktemp); list=$$(mktemp); trap 'rm -f "$$raw" "$$list"' EXIT; \
	  $(PYFILES_GIT) >"$$raw" || { echo "fmt-check: git could not list the python files" >&2; exit 1; }; \
	  $(PYFILES_FILTER) <"$$raw" >"$$list" || { echo "fmt-check: could not filter the delivered files" >&2; exit 1; }; \
	  rc=0; \
	  printf 'fmt-check: grading %s owned file(s) under %s ...\n' "$$(tr -cd '\0' <"$$list" | wc -c | tr -d ' ')" '$(LIB_PYPROJECT)'; \
	  if [ -s "$$list" ]; then \
	    o=$$(xargs -0 $(RC_PYTHON) -m isort --check-only --filter-files --settings-file $(LIB_PYPROJECT) <"$$list" 2>&1) || { rc=1; printf '%s\n' "$$o"; }; \
	    o=$$(xargs -0 $(RC_PYTHON) -m black --check --config $(LIB_PYPROJECT) <"$$list" 2>&1) || { rc=1; printf '%s\n' "$$o"; }; \
	  fi; \
	  $(if $(RACECAR_CANON),$(PYFILES_DELIVERED) <"$$raw" >"$$list" || { echo "fmt-check: could not select the delivered files" >&2; exit 1; }; \
	  if [ -s "$$list" ]; then \
	    printf 'fmt-check: grading %s delivered file(s) under %s ...\n' "$$(tr -cd '\0' <"$$list" | wc -c | tr -d ' ')" '$(RACECAR_CANON)'; \
	    o=$$(xargs -0 $(RC_PYTHON) -m isort --check-only --filter-files --settings-file $(RACECAR_CANON) <"$$list" 2>&1) || { rc=1; printf '%s\n' "$$o"; }; \
	    o=$$(xargs -0 $(RC_PYTHON) -m black --check --config $(RACECAR_CANON) <"$$list" 2>&1) || { rc=1; printf '%s\n' "$$o"; }; \
	  fi;) \
	  $(if $(RC_DJANGO),$(call _RC_DJANGO_FILES,html) >"$$list" || { echo "fmt-check: git could not list the templates" >&2; exit 1; }; \
	  if [ -s "$$list" ]; then o=$$(xargs -0 $(RC_BIN)/djhtml --check <"$$list" 2>&1) || { rc=1; printf '%s\n' "$$o"; }; fi;) \
	  if [ $$rc -ne 0 ]; then exit $$rc; fi; \
	  echo "fmt-check: OK"

# Announce a gate's scope without dumping it. LINT_PATHS/TYPECHECK_PATHS is `src` in the
# default shape -- short, and worth printing literally. In a repo whose graded tree is wider
# it is a find-expanded FILE list of ~100 entries, and echoing that buries the gate output
# underneath it. Print the paths up to four, a count past that.
scope_brief = $(if $(word 5,$(1)),$(words $(1)) paths,$(1))

# Trees a repo commits that sit OUTSIDE $(SRC). One definition, shared by lint and typecheck,
# because two copies is how the two scopes drift apart. `wildcard` so a repo without one of
# them contributes nothing rather than handing the tools a path that does not exist.
#
# A gate narrower than the code it governs reads as a guarantee it is not making, so the
# default is everything the repo commits, not just the library.
# `scripts` only when it actually HOLDS Python. `$(wildcard scripts)` tested the wrong
# thing: it asks whether the directory exists, and mypy refuses a directory with no `.py`
# in it -- "There are no .py[i] files in directory 'scripts'" -- which is a hard failure,
# not a skip.
#
# That went unseen because racecar's own `scripts/` is full of Python. An ADOPTER's is not:
# once delivery moved out of `scripts/`, the only thing left in it is the
# `install_system_deps.sh` template, so every repo that completes the migration and keeps a
# non-Python `scripts/` had `make typecheck` fail on canon's own default. Racecar could not
# reproduce it in its own tree, which is the same blind spot the delivered-record lookup
# had: racecar's shape is not its adopters' shape, and the gate only ever ran on racecar's.
OWNED_EXTRA ?= $(if $(shell find scripts -name '*.py' -print -quit 2>/dev/null),scripts) $(TEST_PATHS)

# TEST_PATHS: the suites. Named separately because RACECAR_TEST_LINT can take them out of the
# LINT scope (never out of typecheck), and because a repo whose suites are not in a root
# `tests/` sets this before the include. Whatever it names is graded under the test profile
# and NOWHERE ELSE -- see LINT_TEST_IGNORE below, which is what makes that true for a repo
# whose suites sit inside $(SRC) rather than beside it.
TEST_PATHS ?= $(wildcard tests)

# Tests are graded like every other file the repo commits. Set RACECAR_TEST_LINT=false to opt
# a repo out of LINTING them; the default is on, because a suite nothing grades is the part of
# the tree most likely to rot -- and it is the part that decides whether every other gate is
# telling the truth.
RACECAR_TEST_LINT ?= true

# What test code legitimately does that library code must not. Argued ONCE here rather than
# repeated as a header in every test file:
#   protected-access       a unit test reaches into the module under test on purpose
#   wrong-import-position  the checker under test is imported after sys.path is extended
#   import-outside-toplevel  a late import is how a test controls import-time behaviour
#   use-implicit-booleaness-not-comparison
#                          `assert f() == []` is a PRECISE assertion; `assert not f()` also
#                          passes for None, 0 and "", so the rewrite weakens the test
# A repo that disagrees narrows this list; it is a parameter, not a rule.
TEST_LINT_RELAX ?= protected-access,wrong-import-position,import-outside-toplevel,use-implicit-booleaness-not-comparison

# What a CLI ENTRY POINT legitimately does that the modules below it must not. Argued in
# arch-python/PYTHON.md §2, which owns the rule; the one line of it that belongs here is
# why pylint cannot express the exception itself:
#   import-outside-toplevel  a `__main__.py` defers a verb module's import into its
#                            dispatch, so --help, a bare invocation and every usage error
#                            stop resolving the union of every verb's dependency closure
#                            to dispatch nothing. pylint gives this message one answer for
#                            the whole run, so the exception has to be scoped by WHICH
#                            FILES a pass grades -- exactly how the test profile below is
#                            scoped, and for the same reason.
# Relaxing it here does NOT leave the deferral ungraded. `check_deferred_imports.py` (run
# by `make arch`) grades its SHAPE, and that check is narrower than the message it
# replaces: dispatch only, never a pure data function, never a nested scope, never upward.
MAIN_LINT_RELAX ?= import-outside-toplevel

# LINT_PATHS: what pylint grades. Defaults to everything committed; a repo whose lintable code
# is wider still sets it with `LINT_PATHS = ...` before the include rather than redefining this
# recipe -- an override makes Make warn on every invocation and forks canon in owned space.
LINT_PATHS ?= $(SRC) $(wildcard scripts)

# LINT_TEST_IGNORE: what makes the two pylint passes DISJOINT BY CONSTRUCTION rather than by
# where a repo happens to keep its suites.
#
# The two scopes never overlapped only because of a layout coincidence. With a root `tests/`,
# $(LINT_PATHS) and $(TEST_PATHS) are separate trees and the test profile is the only pass
# that sees a suite. With suites INSIDE the package -- `src/<pkg>/*/tests/` -- $(SRC) already
# contains them, so the strict pass graded every test file with no plugin and no relaxation,
# and setting TEST_PATHS only ADDED a second, redundant pass over files the first had already
# reported. The comment above TEST_PATHS described a remedy that did not apply.
#
# Narrowing LINT_PATHS is not the remedy either. `ignore-paths` in the rcfile applies to every
# invocation, so it would blind the test-profile pass as well and leave the suites graded by
# nothing -- the state RACECAR_TEST_LINT ?= true exists to prevent. pylint takes the option
# per invocation, so the exclusion is scoped to the passes that must not see a suite. The
# rcfile stays untouched as a FILE -- but its VALUE for this option does not survive a flag,
# which is what LINT_IGNORE_PATHS below composes back in.
#
# The regex is anchored at a path boundary on both ends, which is what makes it work for the
# two ways pylint is handed work: a DIRECTORY (`src`) that it walks, and an explicit FILE LIST
# (what a `find`-built LINT_PATHS produces). `tests` must not match `tests_helper.py` and must
# match `src/pkg/sub/tests/test_it.py`.
#
# Unconditional, including when RACECAR_TEST_LINT=false. That is the point of the rule rather
# than an oversight: a directory named by TEST_PATHS is graded under the test profile or not
# at all, so switching the test profile off now removes the suites from linting in BOTH
# layouts. Before this it took a root `tests/` out entirely and took nothing out for a repo
# whose suites were inside the package.
# Holds the bare comma-joined regex, never the flag and never the quoting. `print-%` is
# `echo '$(VAR)'`, so a value carrying its own single quotes would break the one way a hook
# or a test can read a resolved variable. The recipes below add `--ignore-paths=` and the
# quotes, which is where the shell metacharacters need protecting anyway.
_RC_COMMA := ,
_RC_EMPTY :=
_RC_SPACE := $(_RC_EMPTY) $(_RC_EMPTY)
LINT_TEST_IGNORE = $(subst $(_RC_SPACE),$(_RC_COMMA),$(strip $(foreach p,$(TEST_PATHS),(^|.*/)$(p)(/.*)?$$)))

# LINT_IGNORE_PATHS: the WHOLE value of `--ignore-paths` for the passes that override it,
# which is the rcfile's own list UNION the test patterns above.
#
# pylint gives an option ONE value. A list passed on the command line REPLACES whatever the
# rcfile set for that option -- it does not union with it. So passing $(LINT_TEST_IGNORE)
# alone took every pattern the repo declared in its own rcfile out of the strict pass the
# moment TEST_PATHS became non-empty. The rcfile was untouched as a FILE; its value was not.
#
# The loss was silent in the direction that matters: nothing reports a dropped exclusion, so
# the gate simply starts grading trees the repo scoped out and the findings read as ordinary
# new drift rather than as a lost setting. One repo met this as 84 findings across a vendored
# tree and two deferred subsystems, none of which it lints on purpose.
#
# The two lists are not the same kind of thing, which is why both must hold at once and
# neither can be expressed through the other. The rcfile's patterns say THIS TREE IS NOT OURS
# TO GRADE (vendored code, generated output, a deferred subsystem). The test patterns say
# THIS TREE IS GRADED UNDER THE OTHER PROFILE. Expressing both through one option that only
# one of them can own is what forced the loss.
#
# The rcfile stays the one home for the repo's own exclusions -- `check_docs.py`,
# `check_doc_graph.py` and `check_file_placement.py` read that same key, so a repo declaring
# a tree out of scope declares it once. This reads it back through the same reader rather
# than growing a second parser in Make: `check_docs.py --print-ignore-paths` prints what
# those checkers match with, under BOTH spellings of pylint's first section (`MAIN` and the
# legacy `MASTER`), since pylint honors either and a repo that used the other spelling would
# otherwise lose its whole list here.
#
# Comma-joined, which is pylint's list syntax on the command line AND in the rcfile: it
# splits every value of this option on commas, so a pattern carrying one is already not
# expressible upstream and joining loses nothing that pylint could have read.
#
# The sentinel is the point of the rule rather than belt-and-braces. A reader that fails
# prints nothing, and empty output is indistinguishable from "this repo declares no
# exclusions" -- which would silently reinstate the exact defect this composition fixes. So
# an unreadable list becomes a word pylint never sees, and `lint` stops on it below.
_RC_IGNORE_UNREADABLE := __racecar_ignore_paths_unreadable__
# Memoized for the same reason the delivered-set regex above is (see its comment for why the
# `$(subst $$,$$$$,...)` guard is load-bearing, not decorative): LINT_IGNORE_PATHS below
# references this twice in its own body (a value plus an emptiness test), and is itself
# referenced more than once downstream -- unmemoized, that compounds into double digits of
# check_docs.py spawns per `make lint` (measured: 18, for what should be one).
LINT_RCFILE_IGNORE = $(if $(_LINT_RCFILE_IGNORE_DONE),$(_LINT_RCFILE_IGNORE_CACHE),$(eval _LINT_RCFILE_IGNORE_CACHE := $(subst $$,$$$$,$(shell $(RC_PYTHON) $(call racecar_script,check_docs.py) --print-ignore-paths || echo $(_RC_IGNORE_UNREADABLE))))$(eval _LINT_RCFILE_IGNORE_DONE := 1)$(_LINT_RCFILE_IGNORE_CACHE))
LINT_IGNORE_PATHS = $(if $(LINT_TEST_IGNORE),$(LINT_RCFILE_IGNORE)$(if $(LINT_RCFILE_IGNORE),$(_RC_COMMA))$(LINT_TEST_IGNORE))

# MAIN_PATHS / LINT_IGNORE_PATTERNS: the same disjointness for the CLI entry points, which
# are graded under their own profile below (MAIN_LINT_RELAX) and so must not also be seen
# by the strict pass.
#
# `ignore-paths` cannot express it, and the difference is pylint's, not a style choice.
# That option prunes a DIRECTORY during the walk; an individual module inside a package is
# reached whatever the path patterns say, so `(^|.*/)__main__\.py$$` there excludes
# nothing. `ignore-patterns` matches BASE NAMES, and a base name is exactly what this
# scope is -- every `__main__.py`, wherever it sits.
#
# It is composed rather than passed alone for the reason documented above: pylint gives
# this option one value too, so a bare flag would drop whatever the repo declared. The
# rcfile's list comes back through the same reader (`--print-ignore-patterns`), and when
# the repo declares none, pylint's own default is what is preserved -- an empty rcfile
# list means the default is in force, and passing the flag without it would retire it.
_RC_PYLINT_NAME_DEFAULT := ^\.\#
MAIN_PATHS = $(sort $(shell find $(LINT_PATHS) $(RC_DJANGO) -name '__main__.py' \
  -not -path '*/.*' -not -path '*/venv/*' 2>/dev/null))
# Memoized for the same reason LINT_RCFILE_IGNORE above is (same `$(subst $$,$$$$,...)`
# guard, same reason): LINT_NAME_IGNORE and LINT_IGNORE_PATTERNS both reference this more than
# once, and check_docs.py's own launch cost (a fresh python start) is not something
# `make lint` should pay for per reference.
LINT_RCFILE_NAME_IGNORE = $(if $(_LINT_RCFILE_NAME_IGNORE_DONE),$(_LINT_RCFILE_NAME_IGNORE_CACHE),$(eval _LINT_RCFILE_NAME_IGNORE_CACHE := $(subst $$,$$$$,$(shell $(RC_PYTHON) $(call racecar_script,check_docs.py) --print-ignore-patterns || echo $(_RC_IGNORE_UNREADABLE))))$(eval _LINT_RCFILE_NAME_IGNORE_DONE := 1)$(_LINT_RCFILE_NAME_IGNORE_CACHE))
LINT_NAME_IGNORE = $(if $(LINT_RCFILE_NAME_IGNORE),$(LINT_RCFILE_NAME_IGNORE),$(_RC_PYLINT_NAME_DEFAULT))
LINT_IGNORE_PATTERNS = $(if $(MAIN_PATHS),$(LINT_NAME_IGNORE)$(_RC_COMMA)^__main__\.py$$)

# The adopter's passes stop at the adopter's files. The delivered tree is not dropped from
# scope -- that is the blind spot this issue measured, since `scripts/` holds delivered files
# AND the repo's own -- it moves to its own pass under canon's rulebook below.
LINT_IGNORE_ALL = $(LINT_IGNORE_PATHS)$(if $(and $(LINT_IGNORE_PATHS),$(DELIVERED_IGNORE)),$(_RC_COMMA))$(DELIVERED_IGNORE)

# PYLINT_FATAL_BITS: pylint's exit status is a BITMASK, and two of its bits mean the run
# did not happen rather than that it found something -- 1 (a fatal message: a crashed
# worker, an unloadable plugin) and 32 (a usage error). Everything else is findings.
#
# Reading it as an opaque non-zero is how a hard crash came to read as a perfect score. A
# RecursionError inside a plugin killed every parallel job, and the recipe reported:
#
#     Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)
#     make: *** [lint] Error 1
#
# Two independent causes, both fixed below. The SUMMARY matched the trailing parenthesised
# word of any line, which a Python traceback has plenty of -- `(child)`, `(astroid)`,
# `(timeout)` -- so a stack trace was counted as message codes. And on failure the recipe
# printed `tail -1` of the captured output, which after a crashed worker is pylint's own
# rating line, so the operator was told the score of a run that produced no report while
# the traceback sat unread in $$out.
PYLINT_FATAL_BITS := 33

# PYLINT_ON_LIST: run pylint over a NUL-separated file list with pylint's OWN exit status.
# The list used to go through `xargs -0`, and `$?` was then xargs's status, not pylint's:
# xargs folds any child exit from 1 to 125 into one value of its own, 1 on BSD and 123 on
# GNU, and both carry bit 1 of PYLINT_FATAL_BITS. So a single convention message in a
# delivered file was reported as "DID NOT COMPLETE" (issue #151). `os.execv` replaces the
# interpreter with pylint in the same process, so nothing sits between pylint and `$?`.
# Usage: $(RC_PYTHON) -c "$(PYLINT_ON_LIST)" <nul-list-file> <pylint args...>
PYLINT_ON_LIST = import os, sys; paths = [p for p in open(sys.argv[1], encoding='utf-8').read().split(chr(0)) if p]; os.execv(sys.executable, [sys.executable, '-m', 'pylint', *sys.argv[2:], *paths])

lint: ## pylint (summary view: count by message code) + no-upward-imports
	@printf 'lint: running pylint on %s ...\n' '$(call scope_brief,$(LINT_PATHS))'
	@case '$(LINT_IGNORE_PATHS)$(LINT_IGNORE_PATTERNS)' in *$(_RC_IGNORE_UNREADABLE)*) \
	  printf 'lint: could not read ignore-paths from %s -- refusing to lint with a partial exclusion list\n' '$(LIB_PYPROJECT)' >&2; exit 1;; esac
	@out=$$($(RC_PYTHON) -m pylint -j 0 --rcfile $(LIB_PYPROJECT) $(if $(LINT_IGNORE_ALL),--ignore-paths='$(LINT_IGNORE_ALL)') $(if $(LINT_IGNORE_PATTERNS),--ignore-patterns='$(LINT_IGNORE_PATTERNS)') $(LINT_PATHS) 2>&1); status=$$?; \
	  if [ -n "$(RC_DJANGO)" ]; then \
	    printf 'lint: running pylint on %s (django plugin) ...\n' '$(strip $(RC_DJANGO))'; \
	    djlist=$$(mktemp); $(call _RC_DJANGO_FILES,py) >"$$djlist"; \
	    if [ -s "$$djlist" ]; then \
	      djout=$$($(RC_PYTHON) -c "$(PYLINT_ON_LIST)" "$$djlist" --rcfile $(LIB_PYPROJECT) --load-plugins=pylint_django $(if $(LINT_IGNORE_ALL),--ignore-paths='$(LINT_IGNORE_ALL)') $(if $(LINT_IGNORE_PATTERNS),--ignore-patterns='$(LINT_IGNORE_PATTERNS)') 2>&1); \
	      status=$$(( status | $$? )); \
	      out=$$(printf '%s\n%s' "$$out" "$$djout"); \
	    fi; rm -f "$$djlist"; \
	  fi; \
	  $(if $(RACECAR_CANON),raw=$$(mktemp); dlist=$$(mktemp); \
	  $(PYFILES_GIT) >"$$raw" && $(PYFILES_DELIVERED) <"$$raw" >"$$dlist" || { echo "lint: could not select the delivered files" >&2; rm -f "$$raw" "$$dlist"; exit 1; }; \
	  if [ -s "$$dlist" ]; then \
	    printf 'lint: running pylint on the delivered tree under %s ...\n' '$(RACECAR_CANON)'; \
	    cout=$$($(RC_PYTHON) -c "$(PYLINT_ON_LIST)" "$$dlist" -j 0 --rcfile $(RACECAR_CANON) 2>&1); \
	    status=$$(( status | $$? )); \
	    out=$$(printf '%s\n%s' "$$out" "$$cout"); \
	  fi; rm -f "$$raw" "$$dlist"; ) \
	  if [ -n "$(MAIN_PATHS)" ]; then \
	    printf 'lint: running pylint on %s (entry-point profile) ...\n' '$(call scope_brief,$(MAIN_PATHS))'; \
	    mout=$$($(RC_PYTHON) -m pylint --rcfile $(LIB_PYPROJECT) $(if $(DELIVERED_IGNORE),--ignore-paths='$(DELIVERED_IGNORE)') --disable=$(MAIN_LINT_RELAX) $(MAIN_PATHS) 2>&1); \
	    status=$$(( status | $$? )); \
	    out=$$(printf '%s\n%s' "$$out" "$$mout"); \
	  fi; \
	  printf '%s\n' "$$out" | sed -nE 's/^.*: [A-Z][0-9]{4}: .*\(([a-z0-9-]+)\)$$/(\1)/p' | sort | uniq -c | sort -rn || true; \
	  if [ $$(( status & $(PYLINT_FATAL_BITS) )) -ne 0 ]; then \
	    printf 'lint: pylint DID NOT COMPLETE (exit %s: fatal or usage error). No score below is meaningful. Full output:\n' "$$status"; \
	    printf '%s\n' "$$out"; \
	  elif [ $$status -ne 0 ]; then \
	    printf '%s\n' "$$out" | tail -1; \
	  fi; \
	  exit $$status
	@printf 'lint: checking no-upward-imports in business modules ...\n'
	@$(RC_BIN)/pre-commit run no-upward-imports-in-business-modules --all-files

# The `django` shape (a startproject site) has no library to pytest — its tests are Django's, run by the
# framework's own runner (django > racecar). It defaults test/coverage to `manage.py test`;
# every other shape keeps pytest. An owned Makefile can still override either recipe.
ifeq ($(SHAPE),django)
test: ## Django test runner (manage.py test); scope via TEST_ARGS=...
	$(RC_PYTHON) manage.py test $(TEST_ARGS)

# The `django` shape has no tier split: `manage.py test` takes a label, not a path
# glob, so there is nothing for SLOW_TIER_GLOB to filter. `test-fast` is the whole suite
# here, which keeps `_check` working without teaching the gate which shape it is in.
test-fast: test

coverage: ## Django tests under branch coverage; HTML report at htmlcov/index.html
	$(RC_PYTHON) -m coverage run --branch manage.py test $(TEST_ARGS)
	$(RC_PYTHON) -m coverage report -m
	$(RC_PYTHON) -m coverage html
else
# One captured pytest run, shared by both tiers. $(1) is the xdist `-n` value, or empty to
# force serial; $(2) is extra pytest arguments.
#
# Output is TRAPPED. `-n auto` interleaves progress from every worker, which is unreadable
# and buries the one line that matters. Captured, the summary prints on success and the
# FAILED lines on failure -- the shape `lint` above already uses.
#
# xdist is probed, never assumed: this file is DELIVERED, and an adopter without it must
# still run its tests rather than meet an unknown-argument error.
#
# The summary is read from RECORDS, not grepped from pytest's text: pytest writes one JUnit
# file per leg ($(RESULTS_DIR)/$(TEST_LEG).xml, xunit1 so each case carries its file and
# line), and `results.py` prints each failure and then the verdict in words, LAST. A pipe
# such as `make test | tail -1` drops the exit code but still shows the verdict. The file is
# removed first, so a run that dies before writing one reads as "could not run", never as a
# stale pass.
#
# pytest runs with the outer make's variables removed from its environment. GNU make exports
# every command-line variable to its recipes, so under `make test-macro` a test that runs
# `make` in a fixture repo would otherwise hand that repo racecar's PYTEST_ARGS.
# PYTEST_ADDOPTS goes too: pytest reads it, so an inherited `-k` could select no test,
# and a run that collects nothing reads as a pass.
define pytest_run
par=""; if [ -n "$(1)" ]; then $(RC_PYTHON) -c "import xdist" >/dev/null 2>&1 && par="-n $(1)"; fi; \
	  junit="$(RESULTS_DIR)/$(TEST_LEG).xml"; mkdir -p "$(RESULTS_DIR)"; rm -f "$$junit"; \
	  out=$$(env -u MAKEFLAGS -u MAKELEVEL -u MFLAGS -u RC_VENV -u VENV -u PYTEST_ADDOPTS -u PYTEST_ARGS -u PYTEST_EXTRA -u XDIST_N -u TEST_LEG -u RESULTS_DIR \
	    $(RC_PYTHON) -m pytest -c $(LIB_PYPROJECT) $$par --tb=line --junitxml="$$junit" -o junit_family=xunit1 $(2) $(PYTEST_ARGS) 2>&1); \
	  status=$$?; \
	  if [ $$status -eq 5 ]; then echo "(no tests collected)"; exit 0; fi; \
	  if [ $$status -ne 0 ] && [ ! -s "$$junit" ]; then printf '%s\n' "$$out" | tail -20; fi; \
	  $(RC_PYTHON) $(call racecar_script,results.py) "$$junit"; \
	  if [ "$(RESULTS_DIR)" = "$(RESULTS_LOCAL)" ] && ! git check-ignore -q .rclocal 2>/dev/null; then rm -f "$$junit"; fi; \
	  exit $$status
endef

# Comments live ABOVE the recipe, never inside it: `@` silences only the first recipe line,
# so a `#` on any later line is echoed to the terminal as though it were output.
test: ## pytest, parallel when xdist is present; one summary line (PYTEST_ARGS=...)
	@$(call pytest_run,$(XDIST_N),$(PYTEST_EXTRA))

# Everything that grades the TESTS rather than the code, which the fast gate does not run.
# `check-full` is `check` plus this, and nothing else: the two are a partition, so a check
# that is in neither is a check nobody runs.
#
# pylint over the suites is 12.6s of this repo's 26s lint stage -- half of it, to grade
# files that cannot ship. That is worth paying before a push and not before every commit,
# which is the same argument the slow test tier makes one line below.
check-tests: ## Grade the tests themselves: pylint over the suites + the slow tier
	@$(MAKE) --no-print-directory -j2 lint-tests test-slow

# The two halves of `check-tests`, separate TARGETS so `check-full`'s fan can place them on
# their own legs. They were one recipe, run back to back, and that put 12.6s of pylint --
# SERIAL, because pylint_pytest cannot survive `-j` -- in front of 20s of tests on a single
# leg, while eleven cores sat idle behind it. Nothing about them is ordered: one reads the
# suites, the other runs them.
lint-tests: ## pylint over $(TEST_PATHS) under the test profile
	@if [ -n "$(TEST_PATHS)" ] && [ "$(RACECAR_TEST_LINT)" = "true" ]; then \
	  printf 'check-tests: running pylint on %s (test profile) ...\n' '$(call scope_brief,$(TEST_PATHS))'; \
	  out=$$($(RC_PYTHON) -m pylint --load-plugins=pylint_pytest --rcfile $(LIB_PYPROJECT) $(if $(DELIVERED_IGNORE),--ignore-paths='$(DELIVERED_IGNORE)') --disable=$(TEST_LINT_RELAX) $(TEST_PATHS) 2>&1); \
	  status=$$?; \
	  printf '%s\n' "$$out" | sed -nE 's/^.*: [A-Z][0-9]{4}: .*\(([a-z0-9-]+)\)$$/(\1)/p' | sort | uniq -c | sort -rn || true; \
	  if [ $$status -ne 0 ]; then printf '%s\n' "$$out" | tail -1; exit $$status; fi; \
	fi

# The fast tier. It RE-ENTERS `test` with two variables rather than carrying its own pytest
# recipe, and that indirection is the whole point: an adopter who overrides `test:` in their
# owned Makefile is overriding what the pre-commit gate runs, because `_check` depends on
# this target. A second recipe here would silently route around their override and run stock
# pytest instead -- the gate quietly stops grading what they told it to grade, with no
# output saying so.
#
# SERIAL on purpose (`XDIST_N=`). `-n auto` spends about 2.8s starting twelve workers before
# one assertion runs, which on this suite is more than the whole fast tier costs; the
# parallelism that pays for a 70s run is pure overhead on a 3s one.
# The COMPLEMENT of `test-fast`: only what `test-fast` skips. `check` runs the fast tier and
# `check-tests` runs this, so the two partition the suite and `check-full` never runs a test
# twice. Discovered from the tree rather than listed, because the tier is a LOCATION -- a new
# `slow/` directory joins it with nothing to remember.
SLOW_TIER_DIRS = $(shell find $(TEST_PATHS) -type d -name slow 2>/dev/null)

test-slow: ## only $(SLOW_TIER_GLOB) — the complement of `test-fast`
	@if [ -z "$(strip $(SLOW_TIER_DIRS))" ]; then echo "(no slow tier)"; else \
	  $(MAKE) --no-print-directory test TEST_LEG=slow PYTEST_ARGS="$(SLOW_TIER_DIRS)"; fi

# The macro tier. `test-slow` finds only directories named `slow`, so a `macro/` sibling is
# already outside it and needs no exclusion -- the two tiers partition by construction.
MACRO_TIER_DIRS = $(shell find $(TEST_PATHS) -type d -name macro 2>/dev/null)

test-macro: ## only $(MACRO_TIER_GLOB) — tests that build a repo and run racecar on it
	@if [ -z "$(strip $(MACRO_TIER_DIRS))" ]; then echo "(no macro tier)"; else \
	  $(MAKE) --no-print-directory test TEST_LEG=macro PYTEST_ARGS="$(MACRO_TIER_DIRS)"; fi

test-fast: ## pytest minus the slow and macro tiers, serial — the pre-commit cadence
	@$(MAKE) --no-print-directory test XDIST_N= TEST_LEG=fast \
	  PYTEST_EXTRA="$(PYTEST_EXTRA) --ignore-glob='$(SLOW_TIER_GLOB)' --ignore-glob='$(MACRO_TIER_GLOB)'"

coverage: ## pytest with branch coverage; HTML report at htmlcov/index.html
	$(RC_PYTHON) -m pytest -c $(LIB_PYPROJECT) \
	  --cov=$(or $(PKG),$(SRC)) --cov-branch \
	  --cov-report=term-missing --cov-report=html
endif

# TYPECHECK_PATHS: what mypy grades. Everything the repo commits, same rule as LINT_PATHS,
# and tests are IN unconditionally -- RACECAR_TEST_LINT governs pylint only, because the
# relaxations it names are about style in test code, not about its types. Scoping mypy
# narrower than the code you ship is how a typing gate becomes decorative.
#
# There is deliberately no TEST_TYPECHECK_RELAX to mirror TEST_LINT_RELAX, and the asymmetry
# is mypy's, not an omission. The rule this gets asked about is `no_implicit_reexport`: a test
# doing `from <pkg> import _helper`, where `_helper` is defined in `<pkg>._impl` and merely
# imported by `<pkg>/__init__.py`, fails with `Module "<pkg>" does not explicitly export
# attribute "_helper"`. It looks like the private-symbol access `protected-access` is already
# relaxed for in TEST_LINT_RELAX, so a matching mypy profile looks like the answer. It is not
# available: mypy applies `no_implicit_reexport` per EXPORTING module, so the only override
# that silences it names the library, not the tests -- which would drop the check for every
# caller, not just the suite.
#
# Fix it at the source, and there are three routes of which only one is free. Declare
# `__all__` in the package: it states the public surface outright, satisfies mypy, and is the
# ONLY route both gates accept unchanged. `from ._impl import _helper as _helper` also
# satisfies mypy -- the repeated name is PEP 484's explicit re-export marker -- but pylint
# reports `useless-import-alias` on it and the base scaffold ships no disable, because
# [tool.pylint] starts at `disable = []` on purpose; a repo taking that route writes the one
# entry WITH its own argument, the bar every other entry in that list clears. Importing from
# the module that DEFINES the symbol clears both gates too, and gives up the package boundary
# the re-export existed to draw.
#
# These are DIRECTORIES, not a file list, which works because [tool.mypy] sets
# `exclude_gitignore = true`: mypy walks the tree and skips whatever git already ignores
# (.venv/, build/, dist/, vendored trees), so the scope needs no second exclusion list of
# its own. Adding a directory here is therefore additive and safe.
TYPECHECK_PATHS ?= $(SRC) $(OWNED_EXTRA)

typecheck: ## mypy (library + tests; the delivered tree under canon; the server under its own)
	@printf 'typecheck: running mypy on %s ...\n' '$(call scope_brief,$(TYPECHECK_PATHS))'
	@delivered_ignore=$(if $(DELIVERED_IGNORE),'$(DELIVERED_IGNORE)',''); \
	if [ -z "$$delivered_ignore" ]; then \
	  $(RC_PYTHON) -m mypy --config-file $(LIB_PYPROJECT) $(TYPECHECK_PATHS); \
	else \
	  raw=$$(mktemp); owned=$$(mktemp); trap 'rm -f "$$raw" "$$owned"' EXIT; \
	  $(PYFILES_GIT) >"$$raw" || { echo "typecheck: git could not list the python files" >&2; exit 1; }; \
	  $(RC_PYTHON) $(call racecar_script,check_content_blind.py) --exclude-delivered <"$$raw" | tr '\0' '\n' >"$$owned" || { echo "typecheck: could not select the owned files" >&2; exit 1; }; \
	  paths=""; \
	  for d in $(TYPECHECK_PATHS); do \
	    if grep -q "^$$d/" "$$owned"; then paths="$$paths $$d"; \
	    else printf 'typecheck: skipping %s -- delivered-exclude would leave mypy nothing to check there\n' "$$d"; fi; \
	  done; \
	  if [ -n "$$paths" ]; then \
	    $(RC_PYTHON) -m mypy --config-file $(LIB_PYPROJECT) --exclude "$$delivered_ignore" $$paths; \
	  else \
	    echo "typecheck: every TYPECHECK_PATHS entry is fully delivered content; nothing to check"; \
	  fi; \
	fi
ifneq ($(RACECAR_CANON),)
	@raw=$$(mktemp); list=$$(mktemp); trap 'rm -f "$$raw" "$$list"' EXIT; \
	  $(PYFILES_GIT) >"$$raw" || { echo "typecheck: git could not list the python files" >&2; exit 1; }; \
	  $(PYFILES_DELIVERED) <"$$raw" >"$$list" || { echo "typecheck: could not select the delivered files" >&2; exit 1; }; \
	  if [ -s "$$list" ]; then \
	    printf 'typecheck: running mypy on the delivered tree under %s ...\n' '$(RACECAR_CANON)'; \
	    xargs -0 $(RC_PYTHON) -m mypy --config-file $(RACECAR_CANON) <"$$list"; \
	  fi
endif
ifneq ($(_RC_DJANGO_PYPROJECT),)
	@for d in $(patsubst %/pyproject.toml,%,$(_RC_DJANGO_PYPROJECT)); do \
	  printf 'typecheck: running mypy on %s (django plugin) ...\n' "$$d"; \
	  (cd "$$d" && MYPYPATH=. $(abspath $(RC_PYTHON)) -m mypy --config-file pyproject.toml .) || exit 1; \
	done
endif

arch: _arch ## lint-imports + §1 upward + §3 CLI tree + packaging canon + surface orchestration (+ Django string-relations)

# Private gate body, the same pair as `check` / `_check`.
#
# check_deferred_imports is the counterpart to the entry-point pylint profile
# (MAIN_LINT_RELAX above): that profile stops pylint refusing the one deferral
# PYTHON.md §2 permits, and this grades its shape, so the exception is enforced rather
# than trusted. A repo with no CLI has no entry point and the line is a no-op.
_arch:
	@out=$$($(if $(RC_DJANGO),PYTHONPATH=$(subst $(_RC_SPACE),:,$(strip $(RC_DJANGO))) )$(RC_BIN)/lint-imports --config $(LIB_PYPROJECT) 2>&1); \
	  status=$$?; \
	  if [ $$status -ne 0 ]; then printf '%s\n' "$$out"; exit $$status; fi; \
	  printf '%s\n' "$$out" | grep -E "^Contracts:" || echo "import-linter: OK"
	@if [ -n "$(PKG)" ]; then \
	  $(RC_PYTHON) $(call racecar_script,check_upward_imports.py) $$(find $(PKG) $(RC_DJANGO) -name '*.py' -not -path '*/.*' -not -path '*/venv/*'); \
	else \
	  echo "arch: skipping check_upward_imports ($(_NOT_PRESENT))"; \
	fi
	@mains=$$([ -n "$(PKG)" ] && find $(PKG) $(RC_DJANGO) -name '__main__.py' -not -path '*/.*' -not -path '*/venv/*' 2>/dev/null); \
	if [ -n "$$mains" ]; then \
	  $(RC_PYTHON) $(call racecar_script,check_deferred_imports.py) $$mains \
	    && echo "check_deferred_imports: OK"; \
	fi
	@main=$$([ -n "$(PKG)" ] && find $(PKG) -name '__main__.py' -not -path '*/.*' -not -path '*/venv/*' -print -quit 2>/dev/null); \
	if [ -z "$(PKG)" ]; then \
	  echo "arch: skipping check_cli_commands ($(_NOT_PRESENT))"; \
	elif [ -z "$$main" ]; then \
	  echo "arch: skipping check_cli_commands ($(PKG) has no __main__.py — no CLI surface)"; \
	else \
	  out=$$($(RC_PYTHON) $(call racecar_script,check_cli_commands.py) $(PKG) 2>&1); \
	  status=$$?; \
	  if [ $$status -ne 0 ]; then printf '%s\n' "$$out"; exit $$status; fi; \
	  printf '%s\n' "$$out" | tail -1; \
	fi
	@$(RC_PYTHON) $(call racecar_script,check_packaging.py)
# The pair. check_cli_commands above grades the HOW -- every node has __main__.py +
# commands(), the listing matches, nothing runs at import. check_surface grades the WHAT:
# the verbs that exist are the verbs the spec declared, in both directions. A tree can
# satisfy every structural rule in §3 and still offer a verb nobody designed. It also grades
# every row the spec serves on REST or MCP for its scope, in every shape. No-ops in a repo
# with no surface.jsonl, which is most of them.
	@$(RC_PYTHON) $(call racecar_script,check_surface.py)
	@$(RC_PYTHON) $(call racecar_script,check_surface_orchestration.py)
	@$(RC_PYTHON) $(call racecar_script,check_test_isolation.py)
# The nounspace projection. check_surface_orchestration grades a vertical's SHAPE (does
# this api front a lib); this grades whether the vertical should exist at all -- every noun
# the ontology declares has one, every addressable vertical is a declared noun, and every
# declared verb is reachable from that vertical's api. No-ops in a repo with no declared
# term ontology, which is most of them.
	@$(RC_PYTHON) $(call racecar_script,lexicon.py) check --all
# Make-level, not a shell `if`: the Django checker is delivered only to a repo that has a
# manage.py (sync_scripts.DJANGO_SCRIPTS), so in every other repo the name resolves nowhere
# — and `racecar_script` resolves before any shell runs. `ifneq` drops the untaken branch
# from the recipe, so the `$(call)` is never expanded here. See the note on racecar_script.
ifneq ($(_DJANGO_MNG),)
	@$(RC_PYTHON) $(call racecar_script,check_dj_model_ref_as_string.py)
else
	@echo "arch: skipping check_dj_model_ref_as_string (no manage.py found — not a Django project)"
endif

# Assert this repo has not overridden racecar: no [tool.racecar] table in pyproject and
# a racecar.mk byte-identical to canon (fix racecar, do not override it — see
# upgrade/README.md). It needs an installed racecar to compare against, so it is part of
# the explicit upgrade (`racecar upgrade` runs it) and of no check: a clone must give the
# same answer on a machine with racecar and on one without. Run it by hand here, or through
# `make racecar-upgrade`.
check-overrides: ## Assert the repo has not overridden racecar (pyproject + racecar.mk)
	@if [ -n "$(RACECAR_ROOT)" ]; then \
	  PYTHONPATH="$(RACECAR_ROOT)/src" $(RC_PYTHON) -m racecar.root.lib.upgrade._overrides --root .; \
	else \
	  echo "check-overrides: skipping (RACECAR_ROOT unset; install racecar as a skill or pass RACECAR_ROOT=/path/to/racecar)"; \
	fi

# EVERY CHECKER IN THIS LEG RUNS, AND THE TARGET FAILS ONCE AT THE END. The recipe used to
# be one `@`-line per checker, so make ended the target at the first non-zero exit and the
# ones behind it graded nothing: a repo carrying a single lexicon finding never reached
# `check_changelog`, `check_brief` or the generated-docs check, and the last of those
# reported 56 errors the first time it was run by hand. The cost is not that findings are
# missed forever -- it is that their number is unknowable until the ones in front of them are
# gone, so a repo cannot budget the work and a checker added to the end of this list can sit
# ungraded for as long as anything ahead of it is red.
#
# Everything around this already aggregates: each checker prints all of its own findings, and
# `gate_orchestrate.py` runs every LEG to completion and reports them side by side. Only
# *within* a leg did the first failure hide the rest, which is the one place a reader would
# not expect it.
#
# So each step records its status instead of ending the target, and the last step names which
# ones found something. The order is unchanged and deliberate, and the steps are NOT
# parallelized: some read what an earlier one writes. `gate_orchestrate.py` is not the home
# for this -- it spawns make TARGETS concurrently, and these are sequential commands.
#
# `docs_orchestrate.py` is the nearer relative: it already runs doc checkers as staged gates
# and returns one exit code over all of them. It is deliberately NOT what this target calls,
# and the reason is what the two sets contain rather than the mechanism. It runs
# `check_content_blind.py`, which is switched off here by owner direction (below), and it does
# not run `check_changelog.py` or `surface generate --docs`. Routing this
# target through it would silently re-enable one check and drop three, which is a decision
# about what the gate grades and not a fix to how it reports. Reconciling the two sets is the
# owner's call; until then the leg reports in full, which is all this recipe claims.
#
# One property this recipe now owes a reader: A STEP WHOSE SUCCESSOR DEPENDS ON IT HAVING
# SUCCEEDED MUST NOT BE IN THE ACCUMULATED SET. None is today -- no step here writes what a
# later one grades, `surface generate --docs` writing nothing without `--apply` -- and a step
# that gains such a dependent stays sequenced with a `|| exit 1` instead.
#
# The whole recipe is ONE shell command, joined with `; \`, which is what lets a status
# accumulate across steps. That is also why the comments sit above the target rather than
# between the steps: a `#` line inside a continued recipe ends the logical line, and the
# accumulator would silently split into two shells.
#
# Content blindness is COMMENTED OUT at the owner's direction (2026-09-19). It is the one
# doc checker switched off by hand rather than by config, so it is switched off where it is
# invoked -- here, and in the root Makefile's `check-content-blind` -- and nowhere else.
# Restore by adding this step back between `check_required_docs` and the lexicon check, and
# uncommenting the root Makefile's:
#
#     $(RC_PYTHON) $(call racecar_script,check_content_blind.py) || fail="$$fail check_content_blind"; \
#
# The `--exclude-delivered` / `--only-delivered` / `--delivered-regex` uses of the same
# script are file SELECTION for lint and typecheck, not the check, and they stay.
#
# The two vocabulary gates hold this repo's words to racecar's TERM TREES, which are
# racecar's one home for them and not something an adopter keeps a copy of. With no
# racecar checkout on the machine there is no canon to check against, so they are skipped
# with a printed reason rather than run against nothing — a checker that reports OK
# because it found nothing to read is the failure these two exist to prevent. A repo
# carrying its own docs/lexicon/param/ has something to check either way, so it still runs.
#
# The last step is the generated projections, CHECKED rather than merely generatable. A
# projection with no gate goes stale silently, and this repo proved it: the CLI-docs
# generator's `--check` shipped and was wired into nothing for months, so a node quoting a
# finding the code had stopped emitting passed `make check-full` clean. `cli-docs/SKILL.md`
# had said to wire it into CI the whole time -- the instruction existed and nothing enforced
# it, which is the shape R-02 is about.
#
# What is generated now is one block, the README's `## CLI`, rendered by the CLI audit, and
# `racecar.surface generate --surface cli --docs --strict` fails when the tree left it STALE.
# The lexicon writes lexicon entries and no node body: every lexicon node is authored.
# The step runs the delivered `surface.py`, the same verb `racecar.surface` wraps, so it runs
# in every clone whether or not racecar is installed.
#
# Every step here reads only the clone: the repo's own files and what racecar delivered into
# `.racecar/`, never an installed racecar. A repo pushed by someone with racecar and cloned by
# someone without it must give the same answer. So the lexicon grades the corpus
# `.racecar/docs/lexicon` + `docs/lexicon` (a clean no-op when neither exists), and doc drift,
# which compares against racecar's templates, belongs to `racecar upgrade`, not here.
docs: ## doc-coherence pre-pass (links / §N / vocab) + doc graph + placement + changelog + brief
	@fail=''; \
	$(RC_PYTHON) $(call racecar_script,check_docs.py) || fail="$$fail check_docs"; \
	$(RC_PYTHON) $(call racecar_script,check_doc_graph.py) || fail="$$fail check_doc_graph"; \
	$(RC_PYTHON) $(call racecar_script,check_file_placement.py) || fail="$$fail check_file_placement"; \
	$(RC_PYTHON) $(call racecar_script,check_required_docs.py) || fail="$$fail check_required_docs"; \
	$(RC_PYTHON) $(call racecar_script,lexicon.py) check --all || fail="$$fail lexicon"; \
	$(RC_PYTHON) $(call racecar_script,check_changelog.py) || fail="$$fail check_changelog"; \
	if ls docs/summary/*.md >/dev/null 2>&1; then \
	  $(RC_PYTHON) $(call racecar_script,check_brief.py) || fail="$$fail check_brief"; \
	else \
	  echo "docs: skipping check_brief (no docs/summary/ brief)"; \
	fi; \
	$(RC_PYTHON) $(call racecar_script,surface.py) generate --surface cli --docs --strict || fail="$$fail surface-generate"; \
	if [ -n "$$fail" ]; then \
	  echo "docs: finding(s) reported by:$$fail"; \
	  exit 1; \
	fi

# Scheduled work is declared in the job file, not in a crontab. A crontab is
# per-machine, unreviewed, and drifts silently from what anyone believes is
# running; a `# cron:` / `# schedule:` header in scripts/cron/<job>.sh is under
# review with the code it runs and travels with the checkout. `cron` rewrites
# only its own managed block, so unrelated entries survive and re-running is
# safe. Both targets no-op when the repo has no scripts/cron/.
cron: ## Sync scripts/cron/ into the user crontab (entries declaring `cron: on`)
	bash $(call racecar_script,install_cron.sh)

cron-list: ## Show what each job in scripts/cron/ declares; never touches crontab
	@bash $(call racecar_script,install_cron.sh) --list

clean: ## Remove caches, *.pyc, .DS_Store, build artifacts (never the venv)
	bash $(call racecar_script,clean_files.sh)

# DISTCLEAN_PATHS: extra paths a repo wants removed alongside the venv (fetched artifacts,
# vendored caches). Set it rather than redefining this recipe, for the same reason as LINT_PATHS.
DISTCLEAN_PATHS ?=

distclean: clean ## clean + remove the virtualenv
	rm -rf $(RC_VENV) $(DISTCLEAN_PATHS)

racecar-sync: ## Pull racecar's canon into this repo (racecar.mk + the check scripts)
	@# Named for its SUBJECT. Every other target racecar.mk delivers acts on this repo --
	@# `test`, `lint`, `check` are the repo's own build verbs and racecar merely supplies the
	@# recipe. This one acts on the repo's RELATIONSHIP to racecar, and it was called `sync`,
	@# which says neither what is synced nor with what, in a namespace an adopter also owns.
	@#
	@# Two ways in, tried in order, because both are legitimate: racecar INSTALLED (pip)
	@# needs no checkout, and a bare CLONE works before any install exists. Addressed as the
	@# public verb `racecar sync`, never as a module inside the package: a delivered Makefile
	@# reaching `racecar.lib.delivery._sync` would have every adopter depending on a private
	@# leaf module, which is the layering this repo forbids -- and `-m` over a file path is
	@# why a rename cannot outlive the call, the failure that broke this target in 0.80.0.
	@if $(RC_PYTHON) -c "import racecar" >/dev/null 2>&1; then \
	  $(RC_PYTHON) -m racecar sync --dest .; \
	elif [ -n "$(RACECAR_ROOT)" ]; then \
	  PYTHONPATH="$(RACECAR_ROOT)/src" $(RC_PYTHON) -m racecar sync --dest .; \
	else \
	  echo "racecar not importable and RACECAR_ROOT unset -- pip install racecar, or pass RACECAR_ROOT=/path/to/racecar"; exit 1; \
	fi

sync: ## DISABLED — renamed to racecar-sync; this target is removed on 2026-11-01
	@# Refuses rather than forwarding. An alias that still does the work is one nobody
	@# migrates off: the deprecation notice scrolls past, the target keeps succeeding, and
	@# the old name is still in scripts and muscle memory on the day it is deleted. Failing
	@# costs one re-run and moves the caller once.
	@echo "make sync has been renamed to make racecar-sync." >&2
	@echo "  The old name said neither what was synced nor with what, in a target namespace" >&2
	@echo "  this repo also owns. Run: make racecar-sync" >&2
	@exit 2

racecar-upgrade: ## Report this repo's mechanical divergence from current racecar (writes nothing; clears .racecar bytecode)
	@if $(RC_PYTHON) -c "import racecar" >/dev/null 2>&1; then \
	  $(RC_PYTHON) -m racecar --root . upgrade; \
	elif [ -n "$(RACECAR_ROOT)" ]; then \
	  PYTHONPATH="$(RACECAR_ROOT)/src" $(RC_PYTHON) -m racecar --root . upgrade; \
	else \
	  echo "racecar not importable and RACECAR_ROOT unset -- pip install racecar, or pass RACECAR_ROOT=/path/to/racecar"; exit 1; \
	fi
