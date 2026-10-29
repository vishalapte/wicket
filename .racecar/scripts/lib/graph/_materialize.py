"""Materialize the graph on demand; nothing is cached on disk.

The tree is the only stored representation: one directory per node, its fields in
that node's README frontmatter, its edges in `pnode`. Everything else — the edge
list, the reverse index, in-degree, coverage — is computed fresh on every read (P-02).

Usage:
    python -m racecar.graph materialize            # the tree, with coverage
    python -m racecar.graph materialize --edges    # the edge list
    python -m racecar.graph materialize --orphans  # scripts no node claims

Part of `lib.graph`; `scripts/graph.py` parses the command line and calls `main`. `run`
walks the tree once and returns the one view asked for as a record; `renderer.text` is how
each view reads.

Complexity: O(N) in corpus nodes -- one walk, three views over it.
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path
from typing import Any

from lib import not_a_command
from lib.graph._perimeter import derive
from lib.graph.renderer import text
from lib.shared._as_json import run_json
from lib.topology._walk import META_ROOT, ROOT, load


def _edges(
    nodes: dict[str, dict[str, Any]], root: Path = META_ROOT
) -> list[tuple[str, str, str]]:
    """(from, relation, to). Derived, never stored."""
    out: list[tuple[str, str, str]] = []
    for nid, n in nodes.items():
        if n["serves"]:
            out.append((nid, "serves", n["serves"]))
        for ref in n.get("pnode") or []:
            own_path = n["path"]
            base = own_path.parent if own_path.is_file() else own_path
            resolved = (base / ref).resolve()
            peer = resolved.parent if resolved.name == "README.md" else resolved
            try:
                if peer == root:
                    peer_id = None
                elif peer.is_dir():
                    peer_id = peer.relative_to(root).as_posix()
                else:
                    peer_id = (peer.parent.relative_to(root) / peer.stem).as_posix()
            except ValueError:
                peer_id = None  # outside `root` entirely -- never a declared node
            if peer_id and peer_id in nodes and peer_id != n["serves"]:
                out.append((nid, "peer", peer_id))
        for impl, scripts in (n.get("checked_by") or {}).items():
            for s in scripts if isinstance(scripts, list) else [scripts]:
                out.append((nid, f"checked_by:{impl}", s))
        if n.get("site"):
            out.append((nid, "site", n["site"]))
    return out


def _claimed(n: dict[str, Any]) -> list[str]:
    """The scripts one node's `checked_by` names, every implementation's list flattened."""
    return sum(
        (
            v if isinstance(v, list) else [v]
            for v in (n.get("checked_by") or {}).values()
        ),
        [],
    )


def _coverage(nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The `--coverage` view: declared against enforced, and enforced against tested."""
    # The denominator is derived, not the tree: a key the tree never named is still a
    # declaration a checker reads.
    derived = derive()
    undeclared = {
        k
        for k in derived
        if k.replace("-", "_")
        not in {v["name"].replace("-", "_") for v in nodes.values()}
        and k.replace("-", "_")
        not in {
            re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", str(v.get("site", "")))[-1]
            for v in nodes.values()
            if v.get("site")
        }
    }

    # node -> script -> test. Three legs; a gap in any one is a different defect.
    tests = {
        q.name: q.read_text()
        for r in (
            "scripts/tests",
            "llm-summary/tests",
            "doc-coherence/tests",
            "arch-python/tests",
            "docs-orchestrator/tests",
        )
        for q in (ROOT / r).glob("test_*.py")
    }
    settings = [n for n in nodes.values() if n["kind"] == "setting"]
    gaps: list[dict[str, Any]] = []
    for nid, n in sorted(nodes.items()):
        if n["kind"] != "setting":
            continue
        scripts = _claimed(n)
        # the key is the last identifier in the site, not the last token: a
        # site like `pyproject [project].version` names `version`
        ids = re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", str(n.get("site", "")))
        key = ids[-1] if ids else ""
        proven = [
            t
            for t, src in tests.items()
            if key and (f'"{key}"' in src or f"'{key}'" in src or f"{key}:" in src)
        ]
        if not scripts:
            gaps.append({"node": nid, "gap": "script", "scripts": []})
        elif not proven:
            gaps.append({"node": nid, "gap": "test", "scripts": scripts})
    return {
        "view": "coverage",
        "undeclared": [
            {"key": k, "scripts": sorted(derived[k]["scripts"])}
            for k in sorted(undeclared)
        ],
        "settings": len(settings),
        "gaps": gaps,
    }


def run(
    data: Path | None = None,
    *,
    edges: bool = False,
    orphans: bool = False,
    coverage: bool = False,
) -> dict[str, Any]:
    """The tree, its edges, its orphans, or its coverage: one view of one walk, as a record.

    `data` points the bare walk and `--edges` at an arbitrary tree; the topology stays
    racecar's own vocabulary (see `lib.topology._walk.load`). `--coverage` and `--orphans`
    always grade against racecar's own shipped checkers.

    `view` names which of the four the record is. Raises `NotADirectoryError` when `data`
    is not a directory.
    """
    tax = (data if data is not None else META_ROOT).resolve()
    if not tax.is_dir():
        raise NotADirectoryError(f"{tax} is not a directory")
    nodes = load(tax)
    e = _edges(nodes, tax)
    if edges:
        return {"view": "edges", "edges": [list(edge) for edge in e]}

    by_script = collections.defaultdict(list)
    for f, r, t in e:
        if r.startswith("checked_by:"):
            by_script[t].append(f)

    if coverage:
        return _coverage(nodes)

    if orphans:
        claimed = set(by_script)
        return {
            "view": "orphans",
            "unclaimed": [
                f"{r}/{p.name}"
                for r in ("scripts",)
                for p in (ROOT / r).glob("check_*.py")
                if p.name not in claimed
            ],
        }

    rows: list[dict[str, Any]] = []
    for nid, n in sorted(nodes.items()):
        if n["kind"] == "rule":
            rows.append({"id": nid, "kind": "rule", "title": n.get("title", "")})
        elif n["kind"] == "group":
            rows.append({"id": nid, "kind": "group"})
        else:
            rows.append(
                {
                    "id": nid,
                    "kind": n["kind"],
                    "class": n.get("class", "?"),
                    "checked_by": ",".join(_claimed(n)),
                }
            )
    top = sorted(by_script.items(), key=lambda kv: -len(kv[1]))[:3]
    return {
        "view": "tree",
        "nodes": rows,
        "edges": len(e),
        "claimed": len(by_script),
        "top": [[s, len(ns)] for s, ns in top],
    }


def _show(data: Path | None, *, edges: bool, orphans: bool, coverage: bool) -> int:
    """Run the view and print it as text; 0, or 1 when `data` is not a directory."""
    try:
        record = run(data, edges=edges, orphans=orphans, coverage=coverage)
    except (NotADirectoryError, FileNotFoundError) as err:
        print(text.refusal("materialize: error", err), file=sys.stderr)
        return 1
    rendered = text.materialize(record)
    # An empty edge or orphan list prints nothing.
    if rendered:
        print(rendered)
    return 0


def main(
    data: Path | None = None,
    *,
    edges: bool = False,
    orphans: bool = False,
    coverage: bool = False,
    as_json: bool = False,
) -> int:
    """Print the tree, its edges, its orphans, or its coverage; under `as_json` the text
    goes to stderr and stdout carries the exit code, the result this verb declares."""
    return run_json(
        as_json,
        lambda: _show(data, edges=edges, orphans=orphans, coverage=coverage),
    )


if __name__ == "__main__":
    not_a_command()
