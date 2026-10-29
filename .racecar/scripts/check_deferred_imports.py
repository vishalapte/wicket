#!/usr/bin/env python3
"""Enforce arch-python/PYTHON.md §2's one permitted deferred import.

PYTHON.md forbids a deferred import (one below module scope) except inside the dispatch of
a `__main__.py`, so `--help`, a bare invocation and usage errors do not load every verb's
dependencies.

This script checks the shape of that exception. It reads `__main__.py` files only and skips
every other path; elsewhere pylint's `import-outside-toplevel` applies. `racecar.mk` relaxes
that pylint message for `__main__.py`, and these rules replace it:

  * the import's enclosing scope is exactly one function, defined at module
    level. A method, a nested function, or a class body is not dispatch;
  * that function is not one of the pure data functions the CLI contract
    declares (`commands`, `subcommands`, `parser`, `pipeline`) nor the listing
    helper `_print_commands`; those must stay callable without loading a verb;
  * the import is not an upward relative one (`from .. import ...`), which
    PYTHON.md §3 forbids in a `__main__.py` at any position.

The edge itself is checked by `import-linter`, which sees a deferred import as an edge like
any other.

Usage (invoked by `make arch`):
    python scripts/check_deferred_imports.py <file> [<file> ...]

Exits 0 if clean, 1 if any violation is found, 2 on a usage or parse error.

Complexity: O(n), n = AST nodes per file, times files.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ENTRY_POINT = "__main__.py"

# The four pure data functions of the CLI contract (arch-python/CLI.md) plus the
# listing helper. Declaration and rendering, never dispatch.
DECLARATIONS = frozenset(
    {"commands", "subcommands", "parser", "pipeline", "_print_commands"}
)


def _scopes(tree: ast.Module) -> list[tuple[ast.stmt, list[str]]]:
    """Return every import in `tree`, paired with its enclosing scope names.

    A scope is a function, an async function, or a class body. The list is outermost-first
    and empty for a module-level import. `walk` is a pre-order depth-first traversal that
    passes the scope stack down as an argument.
    """
    found: list[tuple[ast.stmt, list[str]]] = []

    def walk(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                found.append((child, list(stack)))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, stack + [f"{child.name}()"])
            elif isinstance(child, ast.ClassDef):
                walk(child, stack + [f"class {child.name}"])
            else:
                walk(child, stack)

    walk(tree, [])
    return found


def _target(node: ast.stmt) -> str:
    """Render the import as it reads in the file, for the finding line."""
    if isinstance(node, ast.ImportFrom):
        names = ", ".join(a.name for a in node.names)
        return f"from {'.' * node.level}{node.module or ''} import {names}"
    assert isinstance(node, ast.Import)
    return "import " + ", ".join(a.name for a in node.names)


def check_file(path: Path) -> list[str]:
    """Return one finding per violation in `path`; skip anything not an entry point."""
    if path.name != ENTRY_POINT or not path.is_file():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        print(f"{path}: could not parse: {exc}", file=sys.stderr)
        sys.exit(2)

    findings: list[str] = []
    for node, stack in _scopes(tree):
        if not stack:
            continue
        where = f"{path}:{node.lineno}: {_target(node)}"
        if len(stack) > 1 or not stack[0].endswith("()"):
            findings.append(
                f"{where} — deferred in {' -> '.join(stack)}. The one permitted "
                "deferral is a module-level dispatch function of a __main__.py "
                "(PYTHON.md §2)"
            )
            continue
        name = stack[0][:-2]
        if name in DECLARATIONS:
            findings.append(
                f"{where} — deferred in {name}(), which the CLI contract declares "
                "pure data. It must stay callable without loading a verb "
                "(PYTHON.md §2)"
            )
            continue
        if isinstance(node, ast.ImportFrom) and node.level > 1:
            findings.append(
                f"{where} — an entry point never imports upward, deferred or not "
                "(PYTHON.md §3)"
            )
    return findings


def main(argv: list[str]) -> int:
    """Grade each entry point named in `argv`; return an exit code."""
    if not argv:
        print(
            "usage: check_deferred_imports.py <file> [<file> ...]",
            file=sys.stderr,
        )
        return 2
    total = 0
    for arg in argv:
        for finding in check_file(Path(arg)):
            print(finding)
            total += 1
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
