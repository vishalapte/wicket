#!/usr/bin/env python3
"""The graphs a repo declares, read from `docs/graphs/`.

The readers are code; which graphs exist, and how they connect, is declared by the repo
being read. A repo that declares none gets an empty answer.

Complexity: O(G) in declared graphs -- one frontmatter parse and one import each.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable

# The flat delivered checkers are siblings in `scripts/`, which `lib/__init__.py`
# puts on the path before any module here is imported.
from lib.shared import _frontmatter

#: Where a repo declares its graphs: the data tier of `docs/graphs/`, beside the ontology and
#: topology. Not `docs/lexicon/`, which holds words.
DECLARED_REL = "docs/graphs/declared"

#: What `kind: graph` requires, mirrored from the delivered kind node so a malformed
#: declaration is reported rather than raising a KeyError three frames away.
REQUIRED = ("node", "edge", "source", "reader")


def declarations(root: Path) -> list[dict[str, Any]]:
    """Every `kind: graph` node under `root`, in declaration order by name.

    A repo with no `docs/graphs/` gets an empty list. Sorted by name so a rendering is
    stable across filesystems.
    """
    directory = root / DECLARED_REL
    if not directory.is_dir():
        return []
    found = []
    for node in sorted(directory.glob("*.md")):
        if node.name == "README.md":
            continue
        data = _frontmatter.load(node)
        if data.get("kind") != "graph":
            continue
        data["_node"] = node
        found.append(data)
    return found


def malformed(declared: list[dict[str, Any]]) -> list[str]:
    """Every declaration missing a field its kind requires, as findings.

    Checked here because this reader runs in repos that may never run the ontology check,
    and a missing `reader:` would otherwise surface as an import error naming no node.
    """
    return [
        f"{data['_node'].name}: `kind: graph` requires `{field}:` and it is absent"
        for data in declared
        for field in REQUIRED
        if not data.get(field)
    ]


def reader_problem(reader: str) -> str | None:
    """Why `resolve` would fail on this value, or None when it would not.

    `unresolvable` turns the answer into a finding; `build` skips the row instead of letting
    an `AttributeError` from `importlib` end the whole projection.
    """
    module, _, function = reader.partition(":")
    if not function:
        return f"`reader: {reader}` is not `module:function`"
    try:
        found = importlib.import_module(module)
    except ImportError as exc:
        return (
            f"`reader: {reader}` names module `{module}`, which does not import ({exc})"
        )
    if not hasattr(found, function):
        return (
            f"`reader: {reader}` names `{function}`, which `{module}` does not define"
        )
    return None


def unresolvable(declared: list[dict[str, Any]]) -> list[str]:
    """Every declaration whose `reader:` names code that is not there, as findings.

    Only declarations that have a reader are asked; `malformed` reports a missing one.
    """
    out = []
    for data in declared:
        reader = str(data.get("reader") or "")
        if not reader:
            continue
        problem = reader_problem(reader)
        if problem is not None:
            out.append(f"{data['_node'].name}: {problem}")
    return out


def resolve(reader: str) -> Callable[[Path, dict[str, Any]], Any]:
    """`module:function` to the callable it names, as `reader(root, declaration)`.

    The declaration is the second argument, so a reader is named for its mechanism rather
    than for one graph: `source:` and its `#fragment` come from the node, so `read_pnode`
    serves every containment graph and `read_jsonl` every row-per-node one.
    See [`READERS.md`](../../../docs/graphs/READERS.md).

    Resolved explicitly, never by convention from `name:`, so a wrong declaration raises an
    error naming it instead of a graph reporting zero.
    """
    module, _, function = reader.partition(":")
    if not function:
        raise ValueError(f"reader must be `module:function`, got {reader!r}")
    found: Callable[[Path, dict[str, Any]], Any] = getattr(
        importlib.import_module(module), function
    )
    return found
