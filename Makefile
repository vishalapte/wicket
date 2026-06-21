# wicket Makefile: racecar canon targets. `make check` is the canonical
# verification gate; `make help` lists every target.
#
# Tools are invoked as `$(PYTHON) -m <tool>` / `$(BIN)/<tool>` (never bare
# names), so no target requires an *activated* venv — `make check` works from a
# cold shell, sidestepping the GNU Make 3.81 (macOS) PATH-export bug where
# `export PATH :=` is ignored for non-shell execvp lookups.
#
# Requires pip >= 25.1 for `pip install --group <name>` (PEP 735).

SHELL := /bin/bash

# Auto-detect venv (order: .venv, venv, ../venv). Override with `make VENV=...`.
VENV   := $(firstword $(wildcard .venv venv ../venv))
ifdef VENV
  export PATH := $(abspath $(VENV))/bin:$(PATH)
endif
PYTHON := $(if $(VENV),$(VENV)/bin/python,python3)
PIP    := $(PYTHON) -m pip
BIN    := $(if $(VENV),$(VENV)/bin,$(HOME)/.local/bin)

# Project shape: src (see racecar arch-coherence/PACKAGING.md, "Scope").
SRC ?= src
PKG ?= src/wicket
DJAPP ?=
LIB_PYPROJECT   ?= pyproject.toml
DJAPP_PYPROJECT ?=
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

system-deps: ## Install system dependencies outside pip (see scripts/install_system_deps.sh)
	bash scripts/install_system_deps.sh

install-dev: install ## install + PEP 735 dev group + pre-commit hooks (requires pip >= 25.1)
	$(PIP) install -q --group $(LIB_PYPROJECT):dev
	@if git rev-parse --git-dir >/dev/null 2>&1; then $(BIN)/pre-commit install; else echo "install-dev: skipping pre-commit install (not a git repo)"; fi

check: fmt-check lint test ## Fast gate (~30s; pre-commit cadence)

check-full: ## Full gate (parallel; pre-push / CI cadence) — adds typecheck + arch + docs
	@$(MAKE) -j fmt-check lint test typecheck arch docs

lock: ## Regenerate requirements.txt lockfile from runtime deps in pyproject.toml
	$(PYTHON) -m piptools compile --no-emit-index-url --output-file requirements.txt $(LIB_PYPROJECT)

audit: ## pip-audit dependency vulnerability scan (standalone; run weekly / on-demand)
	$(PYTHON) -m pip_audit --strict

fix: fmt ## Auto-fix formatting (isort + black)

fmt: ## Format in place (isort orders imports, then black formats)
	$(PYTHON) -m isort --settings-file $(LIB_PYPROJECT) $(SRC) $(DJAPP)
	$(PYTHON) -m black --config $(LIB_PYPROJECT) $(SRC) $(DJAPP)

fmt-check: ## Check formatting only — no writes
	$(PYTHON) -m isort --check-only --settings-file $(LIB_PYPROJECT) $(SRC) $(DJAPP)
	$(PYTHON) -m black --check --config $(LIB_PYPROJECT) $(SRC) $(DJAPP)

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

arch: ## lint-imports + §1 upward + §3 CLI tree + packaging canon
	$(if $(DJAPP),PYTHONPATH=$(DJAPP) )$(BIN)/lint-imports --config $(LIB_PYPROJECT)
	$(PYTHON) scripts/check_upward_imports.py $$(find $(PKG) $(DJAPP) -name '*.py')
	@if [ -n "$$(find $(PKG) -name '__main__.py' -print -quit 2>/dev/null)" ]; then \
	  $(PYTHON) scripts/check_cli_commands.py $(PKG); \
	else \
	  echo "arch: skipping check_cli_commands ($(PKG) has no __main__.py — no CLI surface)"; \
	fi
	$(PYTHON) scripts/check_packaging.py

docs: ## doc-coherence pre-pass (links / §N / vocab) + TODO + CLAUDE shape + placement
	$(PYTHON) scripts/check_docs.py
	$(PYTHON) scripts/check_todo_format.py
	$(PYTHON) scripts/check_claude_shape.py
	$(PYTHON) scripts/check_file_placement.py

clean: ## Remove caches, *.pyc, .DS_Store, build artifacts (never the venv)
	bash scripts/clean_files.sh

distclean: clean ## clean + remove the virtualenv
	rm -rf $(VENV)

sync: ## Sync canonical racecar check scripts from racecar
	$(PYTHON) $(RACECAR_ROOT)/scripts/sync_scripts.py --dest .
