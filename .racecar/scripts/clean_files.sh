#!/usr/bin/env bash
# clean_files.sh — remove build artefacts and caches.
# Called by `make clean`. Safe to run from any project root.
set -euo pipefail

UNAME_S=$(uname -s)

echo "Removing __pycache__ directories"
find . \
  -path ./.git -prune -o \
  -path './.venv' -prune -o \
  -path './venv' -prune -o \
  -path '../venv' -prune -o \
  -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo "Removing .pyc/.pyo files"
find . \
  -path ./.git -prune -o \
  -path './.venv' -prune -o \
  -path './venv' -prune -o \
  -path '../venv' -prune -o \
  -type f -name '*.py[co]' -delete 2>/dev/null || true

echo "Removing .egg-info directories"
find . \
  -path ./.git -prune -o \
  -path './.venv' -prune -o \
  -path './venv' -prune -o \
  -path '../venv' -prune -o \
  -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true

if [ "$UNAME_S" = "Darwin" ]; then
  echo "Removing .DS_Store files"
  find . \
    -path ./.git -prune -o \
    -path './.venv' -prune -o \
    -path './venv' -prune -o \
    -path '../venv' -prune -o \
    -type f -name '.DS_Store' -delete 2>/dev/null || true
fi

echo "Removing build artifacts and caches"
rm -rf .pytest_cache .ruff_cache .mypy_cache .import_linter_cache \
  build dist .coverage coverage.xml htmlcov
