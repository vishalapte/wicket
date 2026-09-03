#!/usr/bin/env python3
"""Enforce arch-python/PYTHON.md §2's one permitted deferred import.

PYTHON.md forbids a deferred import — one below module scope — wherever it could
conceal a cycle or a direction violation, and permits exactly one shape: an
import inside the dispatch of a `__main__.py`. A group entry point that imports
its verb modules at module scope resolves the union of every verb's dependency
closure before argparse has looked at `argv`, so `--help`, a bare invocation and
every usage error pay that union and dispatch nothing.

This script grades the SHAPE of that exception, so it is enforced rather than
trusted. It reads `__main__.py` files ONLY and skips every other path it is
handed: outside an entry point the rule is unchanged and pylint's
`import-outside-toplevel` is its gate. Inside one, `racecar.mk` relaxes that
pylint message for the whole file class, and what follows is what takes its
place — narrower than the message it replaces, not wider:

  * the import's enclosing scope is exactly one function, defined at module
    level. A method, a nested function, or a class body is not dispatch;
  * that function is not one of the pure data functions the CLI contract
    declares (`commands`, `subcommands`, `parser`, `pipeline`) nor the listing
    helper `_print_commands` — those describe the node and must stay callable
    without loading a verb, which is the property the audit and the doc
    generator both depend on;
  * the import is not an upward relative one (`from .. import ...`), which
    PYTHON.md §3 forbids in a `__main__.py` at any position.

What this script does NOT check is the edge itself, because nothing here has to:
`import-linter` builds its graph from the module AST, so a deferred import is an
edge at its own line number and every acyclicity and layer contract grades it
exactly as it would the top-level form. Deferring changes when a module is
loaded, never whether the edge is allowed.

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

    A scope is a function, an async function, or a class body — the three things
    that put an import below module level. The list is outermost-first and empty
    for a module-level import.

    `walk` is a hand-rolled AST visitor: a pre-order depth-first traversal over
    `iter_child_nodes`, the same traversal `ast.NodeVisitor.generic_visit` performs
    internally -- though not its dispatch mechanism, which resolves a `visit_<Type>`
    method by name rather than the `isinstance` cascade below. It is inlined rather
    than subclassed here so the scope stack can be threaded through the recursion as
    an argument instead of held on `self`.

    `stack` is a scope stack / symbol-table stack: an explicit chain of enclosing
    scopes passed down the recursion (accumulator-passing style) rather than held as
    mutable state, the standard compiler-construction technique for resolving lexical
    nesting during a tree walk (Aho, Sethi & Ullman).
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
