#!/usr/bin/env python3
"""Enforce arch-coherence/PYTHON.md §1: business modules must not import directly from a root package.

Only `__init__.py` files may import from a root package (the environment-layer
channel defined in arch-coherence/README.md "Environment layer exception"). Business modules that need inherited state read
it via their own package's `__init__.py`. The forbidden pattern is a module
reaching UP into the top-level of ITS OWN root package: a file whose tree is
rooted at package R must not do `from R import ...` (unless it is `__init__.py`
or `__main__.py`).

Each file is checked ONLY against the root package that OWNS it — the configured
root whose package tree contains the file (its nearest enclosing top-level
package on disk). A file is never checked against the other configured roots: a
file under root A doing `from B import ...` (B another configured root) is a
CROSS-ROOT dependency, NOT an upward import, and is governed by import-linter
direction/layering contracts — a separate concern this script does not touch.
With a single configured root and all scanned files under it, this is exactly
the original single-root behavior.

The root package name(s) are read from the library pyproject's
`[tool.importlinter]` table — `root_packages` (a list) if present, else the
singular `root_package` (a string). The library pyproject is discovered by the
same shape detection as check_packaging.py: the root `pyproject.toml` for the
`src`/`djapp` shapes, or `pypkg/src/pyproject.toml` for the `pypkg`/`pypkg+djapp`
shapes.

Usage (invoked by pre-commit):
    python scripts/check_upward_imports.py [--root <dir>] <file> [<file> ...]

--root defaults to CWD. Pass the directory containing pyproject.toml explicitly
when invoking from a different working directory (e.g. in a monorepo where the
script lives at the repo root but pyproject.toml is in a sub-package).

Exits 0 if clean, 1 if any violation is found. Files that match no configured
root are skipped.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

from check_packaging import detect_shape


def _root_packages(root: Path) -> list[str]:
    shape, _ = detect_shape(root)
    pyproject = shape.library_pyproject
    if pyproject is None or not pyproject.is_file():
        print("check_upward_imports: pyproject.toml not found", file=sys.stderr)
        sys.exit(2)
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    il = data.get("tool", {}).get("importlinter", {})
    root_packages = il.get("root_packages")
    if isinstance(root_packages, list) and root_packages:
        return [str(r) for r in root_packages]
    root_package = il.get("root_package")
    if isinstance(root_package, str):
        return [root_package]
    print(
        "check_upward_imports: [tool.importlinter].root_package(s) missing from pyproject.toml",
        file=sys.stderr,
    )
    sys.exit(2)


def _owning_root(path: Path, roots: set[str]) -> str | None:
    """Return the configured root package whose tree contains `path`.

    The owning root is the configured root name that appears as a path segment
    identifying the file's package tree (e.g. `pypkg/src/xenocrates/ib/x.py` is
    owned by `xenocrates`; `djapp/apps/accounts/forms.py` by `apps`). Returns
    None if no configured root is on the path. If more than one configured root
    is on the path (nested), the OUTERMOST is the owner — that is the top-level
    package whose top-level `from <root> import ...` would be the upward reach.
    """
    for part in path.parts:
        if part in roots:
            return part
    return None


def _check(path: Path, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if pattern.match(line):
            violations.append((lineno, line.rstrip()))
    return violations


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args, files = parser.parse_known_args(argv)
    roots = set(_root_packages(args.root))
    patterns: dict[str, re.Pattern[str]] = {
        root: re.compile(rf"^\s*from\s+{re.escape(root)}\s+import\s+") for root in roots
    }
    skip_suffixes = ("__main__.py", "__init__.py")
    total = 0
    for arg in files:
        path = Path(arg)
        if path.name in skip_suffixes or not path.is_file():
            continue
        own_root = _owning_root(path, roots)
        if own_root is None:
            continue
        for lineno, line in _check(path, patterns[own_root]):
            print(f"{path}:{lineno}: upward import forbidden: {line}")
            total += 1
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
