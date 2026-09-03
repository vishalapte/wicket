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
               ``path`` is among its declared ``pnode`` (the prose is the human
               echo of the machine edge; the two must agree).

A doc with ``pnode: []`` is a root. Exit 0 when clean, 1 on any finding.

Deterministic, stdlib plus PyYAML (already a dev dependency); no model calls.

Complexity: O(V+E) for graph assembly and the acyclic-case DFS walk (V=doc nodes,
E=pnode/see_also edges), but cycle-loop reconstruction in `_cycle_findings` is
unamortized -- many long back-edges into one still-active ancestor degrade it to O(V^2)
worst case (confirmed empirically).
"""

from __future__ import annotations

import re
import sys
from collections import deque
from pathlib import Path

import yaml
from check_docs import AGENT_DOC_NAMES, ignore_patterns
from check_packaging_rules._files import repo_files
from check_packaging_rules._root import find_repo_root

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
    # racecar-create-server regenerates server/docs/api/ from the Interface
    # Manifest on every run. A hand-maintained `pnode` there would be overwritten
    # by the next generation, so the graph does not police generated surface docs
    # -- the same reason docs/summary is exempt.
    Path("server/docs/api"),
}
# The project's own out-of-scope declarations ([tool.pylint.MASTER].ignore-paths),
# shared with check_docs / check_file_placement. An adopter's content trees
# (a `data/` payload, `server/curricula/` fixtures) are markdown CONTENT, not
# project docs, and are exempted here exactly as they are for the other
# doc-coherence checkers -- so the doc graph polices docs, not payload.
IGNORE_PATTERNS = ignore_patterns()
ACCESSED_VIA = re.compile(r"Accessed via \[[^\]]*\]\(([^)]+)\)")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_UNVISITED, _ACTIVE, _DONE = 0, 1, 2


def in_scope(path: Path, root: Path) -> bool:
    """A tracked Markdown doc that participates in the graph.

    An agent-instruction file (`AGENT_DOC_NAMES`) is machine baseline; SKILL.md
    files are skill definitions with their own frontmatter schema. Both are exempt
    (a SKILL.md may still be a pnode *target*, it just does not declare its own
    edge here).
    """
    if path.name in AGENT_DOC_NAMES or path.name == "SKILL.md":
        return False
    rel = path.relative_to(root)
    if any(part.startswith(".") for part in rel.parts):
        return False  # hidden trees: .git, .venv, .pytest_cache, .mypy_cache, ...
    if any(part in EXCLUDED_DIR_PARTS for part in rel.parts):
        return False
    if any(p.search(rel.as_posix()) for p in IGNORE_PATTERNS):
        return False
    return not any(exc in rel.parents for exc in EXCLUDED_PATHS)


def graph_edges(text: str) -> tuple[list[str] | None, list[str]]:
    """Read `pnode` (the parent list) and `see_also` from the frontmatter block.

    Parses only those two values, not the whole block, so a doc whose other
    frontmatter is not strict YAML does not break graph validation. Returns
    (pnode, see_also); pnode is None when the doc has no frontmatter `pnode`.
    """
    match = FRONTMATTER.match(text)
    if not match:
        return None, []
    block = match.group(1)

    lines = block.splitlines()

    def field(key: str) -> list[str] | None:
        """Read one frontmatter list field, in flow or block form.

        Parses ONLY this key's own lines, never the whole frontmatter block.
        DOC_GRAPH.md guarantees that a doc whose *other* frontmatter is not
        strict YAML still validates — several SKILL.md files carry an unquoted
        colon in `description:` — so loading the whole block would let one bad
        sibling key erase `pnode` and report it as missing. Both forms parse:

            pnode: [../README.md]
            pnode:
              - ../README.md
        """
        for i, line in enumerate(lines):
            match = re.match(rf"^{key}:[ \t]*(.*)$", line)
            if not match:
                continue
            inline = match.group(1).strip()
            if inline:
                fragment = f"{key}: {inline}"
            else:
                collected = [
                    nxt for nxt in _until_dedent(lines[i + 1 :]) if nxt.strip()
                ]
                if not collected:
                    return None
                fragment = f"{key}:\n" + "\n".join(collected)
            try:
                value = yaml.safe_load(fragment)
            except yaml.YAMLError:
                return None
            if not isinstance(value, dict):
                return None
            item = value.get(key)
            if isinstance(item, str):  # a bare scalar is a one-element list
                return [item]
            return [str(v) for v in item] if isinstance(item, list) else None
        return None

    return field("pnode"), field("see_also") or []


BEARINGS = ("contract", "doctrine", "record", "orientation", "draft")
"""Legal `bearing` values, heaviest first (DOC_GRAPH.md, "The node's weight").

A scalar sibling of `pnode`, never a member of it: `pnode` is an edge, `bearing`
is a property of the node.
"""


def bearing(text: str) -> str | None:
    """Read the `bearing` scalar from frontmatter, or None when absent.

    Reuses `graph_edges`' one-key-at-a-time discipline: a doc whose *other*
    frontmatter is not strict YAML must still resolve this key. `field()` returns
    a bare scalar as a one-element list, so a well-formed `bearing` arrives as
    `["contract"]`; anything else (a list of two, a mapping) is malformed and
    reported by the caller as an unknown value rather than silently accepted.
    """
    match = FRONTMATTER.match(text)
    if not match:
        return None
    for line in match.group(1).splitlines():
        found = re.match(r"^bearing:[ \t]*(.*)$", line)
        if found:
            return found.group(1).strip() or None
    return None


def _until_dedent(rest: list[str]) -> list[str]:
    """The indented continuation lines of a block-form YAML field."""
    out: list[str] = []
    for line in rest:
        if line.strip() and not line[:1].isspace():
            break
        out.append(line)
    return out


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

        # `bearing` is validated when present and merely counted when absent.
        # Requiring it outright would red-gate every adopter the day this ships,
        # for a key whose whole value is a considered per-doc judgement -- and a
        # gate that forces 170 snap decisions produces 170 wrong ones. The rollout
        # is reported at the end so the gap stays visible instead of forgotten.
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

        via = ACCESSED_VIA.search(text)
        if via:
            declared = resolve(doc, via.group(1), root)
            if declared is not None and declared not in resolved:
                findings.append(
                    f"{rel}: 'Accessed via' points at {via.group(1)} "
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

    Iterative rather than recursive on purpose: an ordinary deep `pnode` hierarchy
    recurses exactly as deep as a cycle would (this function cannot tell "still walking
    a long chain" from "walking a cycle" until one terminates), and Python's default
    recursion limit turned a large-but-acyclic doc tree into an unhandled
    `RecursionError` -- verified to first fail around 995 links deep on CPython 3.12 --
    instead of the clean pass or `pnode cycle: ...` finding this exists to report. An
    explicit stack has no such ceiling; it is bounded by available memory, not by
    Python's call-stack depth.

    `came_from` is a parent-pointer map (DFS-tree predecessor, not `pnode`'s parent --
    the two are opposite directions of the same edge), not a path carried on every
    frame. An earlier version of this fix carried `path from the DFS root to node` on
    each frame and copied it (`path + [parent]`) on every push, which is O(depth) work
    per push and made a single long chain O(depth^2) in both time and memory --
    measured directly: 40GB and two minutes at a 100,000-deep chain, next to the
    RecursionError this fix exists to remove. `came_from` makes each push O(1); the
    loop is reconstructed by walking it backward, O(depth), but only once, only when a
    cycle is actually found -- the same cost the ORIGINAL recursive version's
    `stack.index(parent)` already paid, so this is not a new cost on top of it.

    Each stack frame is `(node, that node's parents still to examine)` -- the direct
    iterative counterpart of the recursive version's `visit(node)` and its loop over
    `edges.get(node, [])`. `remaining` is a `deque`, consumed from the left in O(1)
    (a plain list's `pop(0)` is O(remaining length), which would reintroduce quadratic
    cost for a node with an unusually long parent list); a frame with an empty deque is
    the same "time to mark DONE and backtrack" test the recursive version expressed as
    falling out of its `for` loop.

    `color` (`_UNVISITED`/`_ACTIVE`/`_DONE`) is the standard CLRS white/gray/black DFS
    marking, and a cycle is exactly a back edge: an edge to a node still `_ACTIVE` (on
    the current DFS stack), as opposed to one already `_DONE`. This is not Tarjan's
    SCC algorithm -- Tarjan adds a low-link array to group whole strongly-connected
    components; this function only reports each individual back edge as a cycle.
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
