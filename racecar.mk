# racecar.mk — canonical racecar build. Synced by racecar; DO NOT EDIT.
#
# This file is IDENTICAL in every racecar project — it carries no per-repo content.
# It detects the project shape from what is on disk (PACKAGING.md "Scope") and
# selects the matching source variables, falling back to stock for any layout it
# does not recognize. Project-specific targets and variable overrides belong in the
# owned `Makefile`, which `include`s this file; edits here are lost on `make sync`.
#
# Tools are invoked as `$(PYTHON) -m <tool>` / `$(BIN)/<tool>` (never bare names),
# so no target needs an *activated* venv — `make check` works from a cold shell,
# sidestepping the GNU Make 3.81 (macOS) PATH-export bug where `export PATH :=` is
# ignored for non-shell execvp lookups. Requires pip >= 25.1 for PEP 735.

# --- shape: governed by what is on disk. The same decision check_packaging.detect_shape
# makes, expressed in Make so racecar.mk stays self-contained (no script call to know the
# shape). Shape = PYTHON_LIBRARY (src/) x DJANGO_PROJECT (server/): src (library only),
# src+server (library x Django), server (standalone Django, no library). Django is marked
# by server/manage.py, never a bare server/. TODO: the {packages,pypkg}/<pkg>/src/<pkg>
# library-axis workspace form is a downstream addition. ---
_SERVER_MNG := $(wildcard server/manage.py)
_SRC_DIR    := $(wildcard src)
_ROOT_PY    := $(wildcard pyproject.toml)

ifeq ($(_ROOT_PY),)
  SHAPE := stock
else ifneq ($(_SERVER_MNG),)
  ifneq ($(_SRC_DIR),)
    SHAPE := src+server
  else
    SHAPE := server
  endif
else ifneq ($(_SRC_DIR),)
  SHAPE := src
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
  SERVER ?= server
  LIB_PYPROJECT ?= pyproject.toml
  SERVER_PYPROJECT ?= server/pyproject.toml
else ifeq ($(SHAPE),server)
  SRC ?= server
  SERVER ?= server
  LIB_PYPROJECT ?= pyproject.toml
else
  SRC ?= src
  LIB_PYPROJECT ?= pyproject.toml
endif

# PKG: the importable package directory the audits require — check_cli_commands
# resolves a package dir (e.g. src/<pkg> -> the `<pkg>` package), NOT the namespace
# source root (`src` has no __init__.py and is rejected), and coverage attributes to
# the package. Auto-derived from SRC so no per-repo override is needed: SRC itself
# when SRC is the whole tree (`.`) or is itself a package (has __init__.py); otherwise
# the package directory found under SRC, falling back to SRC when none is found. This
# descends `src` -> `src/<pkg>`, the case a flat `PKG ?= $(SRC)` left at the namespace
# root. Override with `PKG := ...` before the include.
ifeq ($(SRC),.)
  PKG ?= .
else ifneq ($(wildcard $(SRC)/__init__.py),)
  PKG ?= $(SRC)
else
  PKG ?= $(patsubst %/,%,$(firstword $(dir $(wildcard $(SRC)/*/__init__.py)) $(SRC)/))
endif

# Shared defaults (no-op when a block above already set them).
SERVER ?=
SERVER_PYPROJECT ?=

# --- toolchain (shape-independent) ---
# Auto-detect venv (order: .venv, venv, ../venv). Override with `make VENV=...`.
VENV   := $(firstword $(wildcard .venv venv ../venv))
ifdef VENV
  export PATH := $(abspath $(VENV))/bin:$(PATH)
endif
PYTHON := $(if $(VENV),$(VENV)/bin/python,python3)
PIP    := $(PYTHON) -m pip
BIN    := $(if $(VENV),$(VENV)/bin,$(HOME)/.local/bin)

# Library install directory (where pip -e installs from).
LIB_DIR := $(if $(filter pyproject.toml,$(LIB_PYPROJECT)),.,$(patsubst %/pyproject.toml,%,$(LIB_PYPROJECT)))

# Extra pytest args, e.g. make test PYTEST_ARGS="-k foo -q"
PYTEST_ARGS ?=

# Where the racecar checkout lives, used by `make sync`. Discovered from the
# installed skill symlink so no machine-specific path is baked in. Override with
# `make sync RACECAR_ROOT=/path/to/racecar` if racecar is not installed as a skill.
RACECAR_ROOT ?= $(shell readlink "$(HOME)/.claude/skills/racecar" 2>/dev/null)

.DEFAULT_GOAL := help
.PHONY: help venv install install-dev check check-full fix fmt fmt-check lint \
        test coverage typecheck arch audit docs clean distclean sync system-deps \
        check-overrides

help: ## Show this help
	@awk 'BEGIN{FS=":.*?## ";n=0} \
	  function p(n,k,v, i,j,t){for(i=0;i<n;i++)for(j=i+1;j<n;j++)if(k[i]>k[j]){t=k[i];k[i]=k[j];k[j]=t;t=v[i];v[i]=v[j];v[j]=t};for(i=0;i<n;i++)printf v[i]} \
	  /^##@/{p(n,k,v);n=0;delete k;delete v;printf "\n\033[1m%s\033[0m\n",substr($$0,5)} \
	  /^[a-zA-Z_-]+:.*?## /{k[n]=$$1;v[n]=sprintf("  \033[36m%-14s\033[0m %s\n",$$1,$$2);n++} \
	  END{p(n,k,v)}' $(MAKEFILE_LIST)

# print-<VAR>: echo a resolved make variable, e.g. `make -s print-LIB_PYPROJECT`. The
# pre-commit hooks read shape-derived config (LIB_PYPROJECT / SERVER) through this rather
# than grepping the owned Makefile, which no longer holds them: the fold moved them into
# racecar.mk and the value is computed from the layout, so only Make can resolve it.
print-%:
	@echo '$($*)'

$(VENV):
	python3 -m venv .venv

venv: ## Create .venv if missing (always at .venv/)
	@test -n "$(VENV)" || $(MAKE) .venv VENV=.venv

install: venv system-deps ## Bootstrap: editable library install + system deps
	$(PIP) install -q --upgrade 'pip>=25.1'
	$(PIP) install -q -e $(LIB_DIR)
	@if [ -n "$(SERVER_PYPROJECT)" ]; then \
	  $(PIP) install -q --group $(SERVER_PYPROJECT):runtime; \
	fi

system-deps: ## Install system dependencies outside pip (see scripts/install_system_deps.sh)
	bash scripts/install_system_deps.sh

install-dev: install ## install + PEP 735 dev group + pre-commit hooks (requires pip >= 25.1)
	$(PIP) install -q --group $(LIB_PYPROJECT):dev
	@if git rev-parse --git-dir >/dev/null 2>&1; then $(BIN)/pre-commit install --hook-type pre-commit --hook-type commit-msg; else echo "install-dev: skipping pre-commit install (not a git repo)"; fi
	@if grep -qi '"django' $(LIB_PYPROJECT); then $(PIP) install -q --group $(LIB_PYPROJECT):django; fi


check: fmt-check lint test ## Fast gate (~30s; pre-commit cadence)

check-full: ## Full gate (parallel; pre-push / CI cadence) — adds typecheck + arch + docs
	@$(MAKE) -j fmt-check lint test typecheck arch docs

audit: ## pip-audit dependency vulnerability scan (standalone; run weekly / on-demand)
	$(PYTHON) -m pip_audit --strict

fix: fmt ## Auto-fix formatting (isort + black; djhtml for Django templates)

fmt: ## Format in place (isort orders imports, black formats, djhtml reindents Django templates)
	$(PYTHON) -m isort --settings-file $(LIB_PYPROJECT) $(SRC) $(SERVER)
	$(PYTHON) -m black --config $(LIB_PYPROJECT) $(SRC) $(SERVER)
	$(if $(SERVER),$(BIN)/djhtml $(SERVER))

fmt-check: ## Check formatting only — no writes
	$(PYTHON) -m isort --check-only --settings-file $(LIB_PYPROJECT) $(SRC) $(SERVER)
	$(PYTHON) -m black --check --config $(LIB_PYPROJECT) $(SRC) $(SERVER)
	$(if $(SERVER),$(BIN)/djhtml --check $(SERVER))

lint: ## pylint (summary view: count by message code) + no-upward-imports
	@out=$$($(PYTHON) -m pylint --rcfile $(LIB_PYPROJECT) $(SRC) 2>&1); status=$$?; \
	  if [ -n "$(SERVER)" ]; then \
	    djout=$$($(PYTHON) -m pylint --rcfile $(LIB_PYPROJECT) --load-plugins=pylint_django $(SERVER) 2>&1); \
	    if [ $$? -ne 0 ]; then status=1; fi; \
	    out=$$(printf '%s\n%s' "$$out" "$$djout"); \
	  fi; \
	  printf '%s\n' "$$out" | grep -oE '\([a-z-]+\)$$' | sort | uniq -c | sort -rn || true; \
	  if [ $$status -ne 0 ]; then printf '%s\n' "$$out" | tail -1; fi; \
	  exit $$status
	@$(BIN)/pre-commit run no-upward-imports-in-business-modules --all-files

test: ## pytest; scope via PYTEST_ARGS=... (exit 5 = no tests = ok)
	@$(PYTHON) -m pytest -c $(LIB_PYPROJECT) $(PYTEST_ARGS); status=$$?; \
	  if [ $$status -eq 5 ]; then echo "(no tests collected)"; exit 0; fi; \
	  exit $$status

coverage: ## pytest with branch coverage; HTML report at htmlcov/index.html
	$(PYTHON) -m pytest -c $(LIB_PYPROJECT) \
	  --cov=$(PKG) --cov-branch \
	  --cov-report=term-missing --cov-report=html

typecheck: ## mypy
	$(PYTHON) -m mypy --config-file $(LIB_PYPROJECT) $(SRC)

arch: ## lint-imports + §1 upward + §3 CLI tree + packaging canon + surface orchestration (+ Django string-relations)
	$(if $(SERVER),PYTHONPATH=$(SERVER) )$(BIN)/lint-imports --config $(LIB_PYPROJECT)
	$(PYTHON) scripts/check_upward_imports.py $$(find $(PKG) $(SERVER) -name '*.py')
	@if [ -n "$$(find $(PKG) -name '__main__.py' -print -quit 2>/dev/null)" ]; then \
	  $(PYTHON) scripts/check_cli_commands.py $(PKG); \
	else \
	  echo "arch: skipping check_cli_commands ($(PKG) has no __main__.py — no CLI surface)"; \
	fi
	$(PYTHON) scripts/check_packaging.py
	$(PYTHON) scripts/check_surface_orchestration.py
	@if { [ -n "$(SERVER)" ] && [ -f "$(SERVER)/manage.py" ]; } || [ -f manage.py ]; then \
	  $(PYTHON) scripts/check_dj_model_ref_as_string.py; \
	else \
	  echo "arch: skipping check_dj_model_ref_as_string (no manage.py found — not a Django project)"; \
	fi
	@$(MAKE) --no-print-directory check-overrides

# Assert this repo has not overridden racecar: no [tool.racecar] table in pyproject and
# a racecar.mk byte-identical to canon (fix racecar, do not override it — see
# upgrade/README.md). Racecar-run-only: the check diffs against the racecar checkout's
# templates/classic/, resolved via RACECAR_ROOT (the installed skill symlink). No-ops
# gracefully when RACECAR_ROOT is unset.
check-overrides: ## Assert the repo has not overridden racecar (pyproject + racecar.mk)
	@if [ -n "$(RACECAR_ROOT)" ]; then \
	  $(PYTHON) "$(RACECAR_ROOT)/scripts/check_racecar_overrides.py" --root .; \
	else \
	  echo "check-overrides: skipping (RACECAR_ROOT unset; install racecar as a skill or pass RACECAR_ROOT=/path/to/racecar)"; \
	fi

docs: ## doc-coherence pre-pass (links / §N / vocab) + subsystem docs + TODO + placement + brief
	$(PYTHON) scripts/check_docs.py
	$(PYTHON) scripts/check_subsystem_docs.py
	$(PYTHON) scripts/check_todo_format.py
	$(PYTHON) scripts/check_file_placement.py
	@if ls docs/summary/*.md >/dev/null 2>&1; then \
	  $(PYTHON) scripts/check_brief.py; \
	else \
	  echo "docs: skipping check_brief (no docs/summary/ brief)"; \
	fi

clean: ## Remove caches, *.pyc, .DS_Store, build artifacts (never the venv)
	bash scripts/clean_files.sh

distclean: clean ## clean + remove the virtualenv
	rm -rf $(VENV)

sync: ## Regenerate racecar.mk + canonical check scripts from racecar
	@test -n "$(RACECAR_ROOT)" || { echo "RACECAR_ROOT unset and no racecar skill symlink found — pass RACECAR_ROOT=/path/to/racecar"; exit 1; }
	$(PYTHON) $(RACECAR_ROOT)/scripts/sync_scripts.py --dest .
