# templates/classic/Makefile — copy to project root (no rename needed).
#
# Canonical Makefile for racecar Python projects. `make help` lists every
# target. `make check` is the canonical verification gate.
#
# Tools are invoked as `$(PYTHON) -m <tool>` / `$(BIN)/<tool>` (never bare
# names), so no target requires an *activated* venv — `make check` works from a
# cold shell, sidestepping the GNU Make 3.81 (macOS) PATH-export bug where
# `export PATH :=` is ignored for non-shell execvp lookups.
#
# Requires pip >= 25.1 for `pip install --group <name>` (PEP 735).

# Auto-detect venv (order: .venv, venv, ../venv). Override with `make VENV=...`.
VENV   := $(firstword $(wildcard .venv venv ../venv))
ifdef VENV
  export PATH := $(abspath $(VENV))/bin:$(PATH)
endif
PYTHON := $(if $(VENV),$(VENV)/bin/python,python3)
PIP    := $(PYTHON) -m pip
BIN    := $(if $(VENV),$(VENV)/bin,$(HOME)/.local/bin)

# Project shape — see arch-coherence/PACKAGING.md §"Scope". Pick exactly one
# and set the matching variables:
#
#   shape: src
#     SRC=src                    PKG=src/<pkg>         DJAPP=
#     LIB_PYPROJECT=pyproject.toml
#     DJAPP_PYPROJECT=
#
#   shape: pypkg
#     SRC=pypkg/src              PKG=pypkg/src/<pkg>   DJAPP=
#     LIB_PYPROJECT=pypkg/src/pyproject.toml
#     DJAPP_PYPROJECT=
#
#   shape: pypkg+djapp
#     SRC=pypkg/src              PKG=pypkg/src/<pkg>   DJAPP=djapp
#     LIB_PYPROJECT=pypkg/src/pyproject.toml
#     DJAPP_PYPROJECT=djapp/pyproject.toml
#
#   shape: djapp
#     SRC=djapp (or .)           PKG=<app>             DJAPP=
#     LIB_PYPROJECT=pyproject.toml
#     DJAPP_PYPROJECT=
#     note: DJAPP is empty — SRC already covers the Django source tree.
#           Django is detected for arch checks via manage.py presence, not DJAPP.

# Root of the library/app source tree — passed to fmt, lint, typecheck.
SRC ?= src
# Package path within SRC for import-graph checks (upward imports, CLI tree).
# Defaults to SRC; set to a more specific path when SRC contains non-package dirs.
PKG ?= src/wicket
# Additional source directory to format/lint beyond SRC. Only non-empty for
# shape pypkg+djapp, where Django code lives separately from the library (DJAPP=djapp).
# Leave empty for all other shapes — including djapp, where SRC already is the
# Django tree.
DJAPP ?=
# Path to the library pyproject (tool config source and lockfile input).
LIB_PYPROJECT   ?= pyproject.toml
# Path to the djapp pyproject (shape pypkg+djapp only). Empty otherwise.
DJAPP_PYPROJECT ?=
# Library install directory (where pip -e installs from).
LIB_DIR    := $(if $(filter pyproject.toml,$(LIB_PYPROJECT)),.,$(patsubst %/pyproject.toml,%,$(LIB_PYPROJECT)))

# Extra pytest args, e.g. make test PYTEST_ARGS="-k foo -q"
PYTEST_ARGS ?=

RACECAR_ROOT ?= $(HOME)/dev/vishalapte/racecar

.DEFAULT_GOAL := help
.PHONY: help venv install install-dev lock check check-full fix fmt fmt-check lint \
        test coverage typecheck arch audit docs clean distclean sync system-deps

help: ## Show this help
	@awk 'BEGIN{FS=":.*?## ";n=0} \
	  function p(n,k,v, i,j,t){for(i=0;i<n;i++)for(j=i+1;j<n;j++)if(k[i]>k[j]){t=k[i];k[i]=k[j];k[j]=t;t=v[i];v[i]=v[j];v[j]=t};for(i=0;i<n;i++)printf v[i]} \
	  /^##@/{p(n,k,v);n=0;delete k;delete v;printf "\n\033[1m%s\033[0m\n",substr($$0,5)} \
	  /^[a-zA-Z_-]+:.*?## /{k[n]=$$1;v[n]=sprintf("  \033[36m%-14s\033[0m %s\n",$$1,$$2);n++} \
	  END{p(n,k,v)}' $(MAKEFILE_LIST)

$(VENV):
	python3 -m venv .venv

venv: ## Create .venv if missing (always at .venv/)
	@test -n "$(VENV)" || $(MAKE) .venv VENV=.venv

install: venv system-deps ## Bootstrap: editable library install + system deps
	$(PIP) install -q --upgrade 'pip>=25.1'
	$(PIP) install -q -e $(LIB_DIR)
	@if [ -n "$(DJAPP_PYPROJECT)" ]; then \
	  $(PIP) install -q --group $(DJAPP_PYPROJECT):runtime; \
	fi

system-deps: ## Install system dependencies outside pip (see scripts/install_system_deps.sh)
	bash scripts/install_system_deps.sh

install-dev: install ## install + PEP 735 dev group + pre-commit hooks (requires pip >= 25.1)
	$(PIP) install -q --group $(LIB_PYPROJECT):dev
	@if git rev-parse --git-dir >/dev/null 2>&1; then $(BIN)/pre-commit install; else echo "install-dev: skipping pre-commit install (not a git repo)"; fi
	@if grep -qi '"django' $(LIB_PYPROJECT); then $(PIP) install -q --group $(LIB_PYPROJECT):django; fi


check: fmt-check lint test ## Fast gate (~30s; pre-commit cadence)

check-full: ## Full gate (parallel; pre-push / CI cadence) — adds typecheck + arch + docs
	@$(MAKE) -j fmt-check lint test typecheck arch docs

lock: ## Regenerate requirements.txt lockfile from runtime deps in pyproject.toml
	$(PYTHON) -m piptools compile --no-emit-index-url --output-file requirements.txt $(LIB_PYPROJECT)

audit: ## pip-audit dependency vulnerability scan (standalone; run weekly / on-demand)
	$(PYTHON) -m pip_audit --strict

fix: fmt ## Auto-fix formatting (isort + black; djhtml for Django templates)

fmt: ## Format in place (isort orders imports, black formats, djhtml reindents Django templates)
	$(PYTHON) -m isort --settings-file $(LIB_PYPROJECT) $(SRC) $(DJAPP)
	$(PYTHON) -m black --config $(LIB_PYPROJECT) $(SRC) $(DJAPP)
	$(if $(DJAPP),$(BIN)/djhtml $(DJAPP))

fmt-check: ## Check formatting only — no writes
	$(PYTHON) -m isort --check-only --settings-file $(LIB_PYPROJECT) $(SRC) $(DJAPP)
	$(PYTHON) -m black --check --config $(LIB_PYPROJECT) $(SRC) $(DJAPP)
	$(if $(DJAPP),$(BIN)/djhtml --check $(DJAPP))

lint: ## pylint (summary view: count by message code) + no-upward-imports
	@out=$$($(PYTHON) -m pylint --rcfile $(LIB_PYPROJECT) $(SRC) $(DJAPP) 2>&1); status=$$?; \
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

arch: ## lint-imports + §1 upward + §3 CLI tree + packaging canon + faces gap (+ Django string-relations)
	$(if $(DJAPP),PYTHONPATH=$(DJAPP) )$(BIN)/lint-imports --config $(LIB_PYPROJECT)
	$(PYTHON) scripts/check_upward_imports.py $$(find $(PKG) $(DJAPP) -name '*.py')
	@if [ -n "$$(find $(PKG) -name '__main__.py' -print -quit 2>/dev/null)" ]; then \
	  $(PYTHON) scripts/check_cli_commands.py $(PKG); \
	else \
	  echo "arch: skipping check_cli_commands ($(PKG) has no __main__.py — no CLI surface)"; \
	fi
	$(PYTHON) scripts/check_packaging.py
	$(PYTHON) scripts/check_face_orchestration.py
	@if { [ -n "$(DJAPP)" ] && [ -f "$(DJAPP)/manage.py" ]; } || [ -f manage.py ]; then \
	  $(PYTHON) scripts/check_dj_model_ref_as_string.py; \
	else \
	  echo "arch: skipping check_dj_model_ref_as_string (no manage.py found — not a Django project)"; \
	fi

docs: ## doc-coherence pre-pass (links / §N / vocab) + subsystem docs + TODO + CLAUDE shape + placement + brief
	$(PYTHON) scripts/check_docs.py
	$(PYTHON) scripts/check_subsystem_docs.py
	$(PYTHON) scripts/check_todo_format.py
	$(PYTHON) scripts/check_claude_shape.py
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

sync: ## Sync canonical racecar check scripts from racecar
	$(PYTHON) $(RACECAR_ROOT)/scripts/sync_scripts.py --dest .
