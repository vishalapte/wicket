#!/usr/bin/env python3
"""check_doc_graph: validate the documentation node graph.

Every in-scope Markdown doc (see DOC_GRAPH.md) declares its parent once, in a
``pnode`` frontmatter list. Children and peers are derived by scanning, never
stored. This checker assembles the graph from every doc's ``pnode`` and holds
it to three rules:

- **types**    every ``pnode`` (and optional ``see_also``) entry resolves to an
               existing in-scope Markdown file.
- **dag**      the graph assembled from all ``pnode`` edges is acyclic.
- **consistency**  where a doc's body carries an ``Accessed via [X](path)`` link,
               ``path`` is among its declared ``pnode``.

A doc with ``pnode: []`` is a root. Exit 0 when clean, 1 on any finding.

Deterministic, stdlib plus PyYAML; no model calls.

Complexity: O(V+E) for graph assembly and the acyclic-case DFS walk (V=doc nodes,
E=pnode/see_also edges); cycle-loop reconstruction in `_cycle_findings` is O(V^2) worst
case.
"""

from __future__ import annotations

import re
import sys
from collections import deque
from pathlib import Path
from typing import Any

from check_docs import ignore_patterns
from lib.shared import _frontmatter, _markdown
from lib.shared._agent_docs import AGENT_DOC_NAMES
from lib.shared._files import is_hidden_path, repo_files
from lib.shared._root import find_repo_root

# Directories whose Markdown is not part of the doc graph: vendored templates,
# generated mirror trees, the deliberately-broken demo, the llm-summary briefs
# (which carry a different frontmatter schema), and the tool trees.
EXCLUDED_DIR_PARTS = {
    ".git",
    "node_modules",
    ".venv",
    "templates",
    "examples",
}
EXCLUDED_PATHS = {
    Path("docs/summary"),
    # racecar-create-server regenerates server/docs/api/ from the Interface Manifest.
    Path("server/docs/api"),
}
# The project's own out-of-scope declarations ([tool.pylint.MASTER].ignore-paths),
# shared with check_docs / check_file_placement.
IGNORE_PATTERNS = ignore_patterns()
"""The cwd's patterns, kept for callers that import this name directly.

`in_scope` does NOT use it. Bound at import, this is whatever the process's cwd was when
the module first loaded, which is correct for a checker run as `python check_doc_graph.py`
from the repo root and silently wrong for anything importing it in-process: the same
`in_scope(path, root)` call then answers differently depending on where the interpreter
started. Measured: 1 pattern imported from this repo, 0 imported from elsewhere, and that
one pattern excludes 7 docs -- so a doc count computed in a test session that had chdir'd
came out 270 against the real 263.
"""

_PATTERNS_BY_ROOT: dict[Path, tuple[re.Pattern[str], ...]] = {}


def _ignore_for(root: Path) -> tuple[re.Pattern[str], ...]:
    """`ignore_patterns` for `root`, compiled once per root."""
    if root not in _PATTERNS_BY_ROOT:
        _PATTERNS_BY_ROOT[root] = ignore_patterns(root)
    return _PATTERNS_BY_ROOT[root]


_UNVISITED, _ACTIVE, _DONE = 0, 1, 2


def accessed_via(text: str) -> str | None:
    """The target of the doc's first `Accessed via [X](target)` link, or None."""
    doc = _markdown.parse(text)
    lines = {line.no: line.text for line in doc.text()}
    for link in doc.links():
        if f"Accessed via [{link.text}](" in lines[link.no]:
            return link.target
    return None


def in_scope(path: Path, root: Path) -> bool:
    """A tracked Markdown doc that participates in the graph.

    An agent-instruction file (`AGENT_DOC_NAMES`) and SKILL.md files are exempt (a
    SKILL.md may still be a pnode target).
    """
    if path.name in AGENT_DOC_NAMES or path.name == "SKILL.md":
        return False
    rel = path.relative_to(root)
    if is_hidden_path(rel):
        return False  # hidden trees: .git, .venv, .pytest_cache, .mypy_cache, ...
    if any(part in EXCLUDED_DIR_PARTS for part in rel.parts):
        return False
    if any(p.search(rel.as_posix()) for p in _ignore_for(root)):
        return False
    return not any(exc in rel.parents for exc in EXCLUDED_PATHS)


def graph_edges(text: str) -> tuple[list[str] | None, list[str]]:
    """Read `pnode` (the parent list) and `see_also` from the frontmatter block.

    Each key is read by `_frontmatter.value` from its own lines, so other non-strict YAML
    in the frontmatter does not break it. Returns (pnode, see_also); pnode is None when
    the doc has no frontmatter `pnode`.
    """
    return _as_list(_frontmatter.value(text, "pnode")), (
        _as_list(_frontmatter.value(text, "see_also")) or []
    )


def _as_list(item: Any) -> list[str] | None:
    """A list field's entries; a bare scalar is a one-element list."""
    if isinstance(item, str):
        return [item]
    return [str(v) for v in item] if isinstance(item, list) else None


BEARINGS = ("contract", "doctrine", "record", "orientation", "draft")
"""Legal `bearing` values, heaviest first (DOC_GRAPH.md, "The node's weight").

A scalar sibling of `pnode`, never a member of it: `pnode` is an edge, `bearing`
is a property of the node.
"""


def bearing(text: str) -> str | None:
    """Read the `bearing` scalar from frontmatter, or None when absent.

    Read by `_frontmatter.value`, like `graph_edges`. A non-scalar is returned as its text
    and reported by the caller as an unknown value.
    """
    found = _frontmatter.value(text, "bearing")
    if found is None or found == "":
        return None
    return found if isinstance(found, str) else str(found)


def resolve(doc: Path, ref: str, root: Path) -> Path | None:
    """Resolve a pnode/link ref (relative to the doc's directory) to a repo path.

    None when the ref escapes the repository root (an invalid edge).
    """
    try:
        return (doc.parent / ref).resolve().relative_to(root.resolve())
    except ValueError:
        return None


def main() -> int:
    """Assemble the doc graph from every in-scope doc's pnode and validate it."""
    root = find_repo_root()
    docs = sorted(p for p in repo_files(root, "*.md") if in_scope(p, root))
    findings: list[str] = []
    unweighted: list[Path] = []
    edges: dict[Path, list[Path]] = {}

    for doc in docs:
        rel = doc.relative_to(root)
        text = doc.read_text(encoding="utf-8")

        # `bearing` is validated when present and counted when absent; the count is
        # reported at the end.
        weight = bearing(text)
        if weight is None:
            unweighted.append(rel)
        elif weight not in BEARINGS:
            findings.append(
                f"{rel}: unknown `bearing` value {weight!r}; "
                f"expected one of {', '.join(BEARINGS)}"
            )

        pnode, see_also = graph_edges(text)
        if pnode is None:
            findings.append(f"{rel}: missing or malformed `pnode` frontmatter")
            continue

        resolved: list[Path] = []
        for ref in pnode:
            target = resolve(doc, ref, root)
            if target is None or not (root / target).is_file():
                findings.append(f"{rel}: pnode target does not exist: {ref}")
                continue
            resolved.append(target)
        edges[rel] = resolved

        for ref in see_also:
            target = resolve(doc, ref, root)
            if target is None or not (root / target).is_file():
                findings.append(f"{rel}: see_also target does not exist: {ref}")

        via = accessed_via(text)
        if via is not None:
            declared = resolve(doc, via, root)
            if declared is not None and declared not in resolved:
                findings.append(
                    f"{rel}: 'Accessed via' points at {via} "
                    f"but it is not in pnode {pnode}"
                )

    findings.extend(_cycle_findings(edges))

    if unweighted:
        print(
            f"check_doc_graph: {len(unweighted)} of {len(docs)} doc(s) declare no "
            "`bearing` (info, not a finding; see DOC_GRAPH.md)"
        )

    if findings:
        print(f"check_doc_graph: {len(findings)} finding(s)")
        for line in findings:
            print(f"  {line}")
        return 1
    roots = [str(d) for d, parents in edges.items() if not parents]
    print(
        f"check_doc_graph: OK ({len(edges)} docs, roots: {', '.join(roots) or 'none'})"
    )
    return 0


def _cycle_findings(edges: dict[Path, list[Path]]) -> list[str]:
    """Report each pnode cycle once, by an explicit-stack DFS on the parent relation.

    Iterative, so a deep acyclic hierarchy cannot hit Python's recursion limit.
    `came_from` is a parent-pointer map (DFS-tree predecessor); the loop is reconstructed
    from it only when a cycle is found. Each stack frame is `(node, deque of parents
    still to examine)`; an empty deque marks the node DONE. `color` is CLRS
    white/gray/black marking; a cycle is a back edge to an `_ACTIVE` node.
    """
    findings: list[str] = []
    color: dict[Path, int] = {}
    came_from: dict[Path, Path] = {}

    for start in edges:
        if color.get(start, _UNVISITED) != _UNVISITED:
            continue
        color[start] = _ACTIVE
        stack: list[tuple[Path, deque[Path]]] = [(start, deque(edges.get(start, [])))]
        while stack:
            node, remaining = stack[-1]
            if not remaining:
                color[node] = _DONE
                stack.pop()
                continue
            parent = remaining.popleft()
            parent_color = color.get(parent, _UNVISITED)
            if parent_color == _ACTIVE:
                loop = [node]
                cur = node
                while cur != parent:
                    cur = came_from[cur]
                    loop.append(cur)
                loop.reverse()
                loop.append(parent)
                findings.append("pnode cycle: " + " -> ".join(str(p) for p in loop))
            elif parent_color == _UNVISITED:
                color[parent] = _ACTIVE
                came_from[parent] = node
                stack.append((parent, deque(edges.get(parent, []))))
    return findings


if __name__ == "__main__":
    sys.exit(main())
