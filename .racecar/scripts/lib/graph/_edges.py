#!/usr/bin/env python3
"""The arrows between graphs, read from the declarations, and the check that closes them.

Authored in each node's `edges:` field, a correspondence (a checker comparing two
structures), not a derivation. `edge_findings` closes them against the tree. They point
backward, like every other edge in a racecar graph: a node says what it is checked
against; the forward view is computed by walking.

Complexity: O(E) in declared edges -- one stat per claimed implementation.
"""

from __future__ import annotations

from pathlib import Path

# The flat delivered checkers are siblings in `scripts/`, which `lib/__init__.py`
# puts on the path before any module here is imported.
from lib.graph._declared import declarations

#: One declared correspondence: `(from, to, implementation, what it asserts)`.
Correspondence = tuple[str, str, str, str]


def inter_edges(root: Path) -> list[Correspondence]:
    """Every declared correspondence as `(from, to, implementation, what it asserts)`.

    Flipped on the way out: a node declares what it is checked against, and the picture
    draws the arrow the other way round.
    """
    return [
        (
            str(edge["against"]),
            str(data["name"]),
            str(edge["via"]),
            str(edge["asserts"]),
        )
        for data in declarations(root)
        for edge in data.get("edges", []) or []
    ]


def edge_findings(root: Path) -> list[str]:
    """Every declared inter-graph edge whose implementing file is missing."""
    return [
        f"{src} -> {dst}: {impl} does not exist"
        for src, dst, impl, _why in inter_edges(root)
        if not (root / impl).exists()
    ]
