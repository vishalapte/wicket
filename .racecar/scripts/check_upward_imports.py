#!/usr/bin/env python3
"""Enforce arch-python/PYTHON.md §1: business modules must not import directly
from a root package.

Only `__init__.py` files may import from a root package (the environment-layer
channel defined in arch-python/README.md "Environment layer exception").
Business modules that need inherited state read it via their own package's
`__init__.py`. The forbidden pattern is a module
reaching UP into the top-level of ITS OWN root package: a file whose tree is
rooted at package R must not do `from R import ...` (unless it is `__init__.py`
or `__main__.py`).

Every form that binds R or reads its namespace is caught: `from R import x`, `import R`,
`import R as x` and `import R.sub`, which binds R. `import R.sub as alias` binds only the
alias, as `from R.sub import x` does, and passes.

Line regex, not AST (R-01). A comma-joined `import a, b` is matched at any position in
the list.

Each file is checked only against the root package that owns it (its nearest enclosing
configured root). A file under root A doing `from B import ...` is a cross-root
dependency, governed by import-linter contracts, not by this script.

The root package name(s) are read from the library pyproject's
`[tool.importlinter]` table — `root_packages` (a list) if present, else the
singular `root_package` (a string); `root_packages` below is the one reader of
that table, and `check_dj_model_ref_as_string.py` imports it. The library
pyproject is the root `pyproject.toml` in every shape.

Usage (invoked by pre-commit):
    python scripts/check_upward_imports.py [--root <dir>] <file> [<file> ...]

--root defaults to CWD. Pass the directory containing pyproject.toml explicitly
when invoking from a different working directory (e.g. in a monorepo where the
script lives at the repo root but pyproject.toml is in a sub-package).

Exits 0 if clean, 1 if any violation is found. Files that match no configured
root are skipped.

Complexity: O(files x lines per file)
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from check_packaging import detect_shape


class ConfigError(Exception):
    """The repo is not set up for this check, so there is nothing to say about it.
    Raised rather than exited, because `findings` runs inside other programs.
    """


def root_packages(data: dict[str, Any]) -> list[str]:
    """The root package names a parsed pyproject's `[tool.importlinter]` declares.

    The one reader; `check_dj_model_ref_as_string.py` imports it. Accepts the list
    `root_packages` and the singular `root_package`; the plural wins when both are
    present. A key that is present and malformed is refused by name.
    """
    il = data.get("tool", {}).get("importlinter", {})
    plural = il.get("root_packages")
    if plural is not None:
        if (
            not isinstance(plural, list)
            or not plural
            or not all(isinstance(r, str) and r for r in plural)
        ):
            raise ConfigError(
                "[tool.importlinter].root_packages must be a non-empty list of strings"
            )
        return list(plural)
    singular = il.get("root_package")
    if singular is not None:
        if not isinstance(singular, str) or not singular:
            raise ConfigError(
                "[tool.importlinter].root_package must be a non-empty string"
            )
        return [singular]
    raise ConfigError("[tool.importlinter].root_package(s) missing from pyproject.toml")


def _root_packages(root: Path) -> list[str]:
    shape, _ = detect_shape(root)
    pyproject = shape.library_pyproject
    if pyproject is None or not pyproject.is_file():
        raise ConfigError("pyproject.toml not found")
    return root_packages(tomllib.loads(pyproject.read_text(encoding="utf-8")))


def _owning_root(path: Path, roots: set[str]) -> str | None:
    """Return the configured root package whose tree contains `path`.

    The owning root is the configured root name that appears as a path segment
    identifying the file's package tree (e.g. `src/widgets/ib/x.py` is
    owned by `widgets`; `server/apps/accounts/forms.py` by `apps`). Returns
    None if no configured root is on the path. If more than one configured root
    is on the path (nested), the OUTERMOST is the owner — that is the top-level
    package whose top-level `from <root> import ...` would be the upward reach.
    """
    for part in path.parts:
        if part in roots:
            return part
    return None


def _pattern(root: str) -> re.Pattern[str]:
    r"""Both statements that reach up into `root`'s top-level, as one line pattern.
    Anchored at the start of the line, so an indented deferred import is caught and a
    commented-out one is not.

    `from R import ` — the root spelled whole, bounded on the right by the `import`
    keyword, so `from Rful import x` cannot reach it.

    `import R` — the root not followed by a word character or a dot, so `import R` and
    `import R as x` match and `import Rful` does not. `import R.sub` matches, and
    `import R.sub as alias` does not, because it binds only the alias. The optional prefix
    walks a comma-joined list so `import os, R` is caught too. `import vendor.R` does not
    match.
    """
    name = re.escape(root)
    return re.compile(
        rf"^\s*(?:from\s+{name}\s+import\s+"
        rf"|import\s+(?:[\w.]+(?:\s+as\s+\w+)?\s*,\s*)*{name}"
        rf"(?:(?![\w.])|(?:\.\w+)+(?![\w.])(?!\s+as\b)))"
    )


def _check(path: Path, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if pattern.match(line):
            violations.append((lineno, line.rstrip()))
    return violations


def findings(root: Path, files: list[str]) -> list[str]:
    """Every forbidden upward import in `files`, one `path:line: message` per hit.
    Prints nothing and returns the list; `main` renders exactly this.

    `__main__.py`, `__init__.py` and files under a `migrations/` directory (Django writes
    those; `arch-python/DJANGO.md`, "Files under `migrations/`") are skipped.

    A relative path in `files` is read relative to `root`. The message names the path as
    it was given.
    """
    roots = set(_root_packages(root))
    patterns: dict[str, re.Pattern[str]] = {r: _pattern(r) for r in roots}
    skip_suffixes = ("__main__.py", "__init__.py")
    out: list[str] = []
    for arg in files:
        path = Path(arg)
        if path.name in skip_suffixes:
            continue
        probe = path if path.is_absolute() else root / path
        if not probe.is_file():
            continue
        # The owning root is read off the path inside the repo, so a directory above the
        # repo sharing a root's name cannot be taken as the owner.
        try:
            inside = probe.relative_to(root)
        except ValueError:
            inside = path
        if "migrations" in inside.parts[:-1]:
            continue
        own_root = _owning_root(inside, roots)
        if own_root is None:
            continue
        for lineno, line in _check(probe, patterns[own_root]):
            out.append(f"{path}:{lineno}: upward import forbidden: {line}")
    return out


def render(hits: list[str]) -> str:
    """The report `main` prints, built from `findings`. One home for the format.
    Empty when there is nothing to report.
    """
    return "\n".join(hits)


def main(argv: list[str]) -> int:
    """Scan the given files for forbidden upward imports; return an exit code."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args, files = parser.parse_known_args(argv)
    try:
        hits = findings(args.root, files)
    except ConfigError as exc:
        print(f"check_upward_imports: {exc}", file=sys.stderr)
        return 2
    if hits:
        print(render(hits))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
