"""The `generate` verb: project this repo's graphs into the README's `## Graphs` block.

Every count comes from the artifact that already owns it. Which graph feeds which is
authored in each graph node's `edges:`; every edge names the file that implements it,
and a missing file is a finding.

Part of `lib.graph`; `scripts/graph.py` parses the command line and calls `main`. `run`
grades the declarations and either reads the graphs (`--json`) or renders, compares and,
under `--apply`, writes the block; it returns what it found as a record and raises
`Refused` for every reason it declines. `renderer.text` is how the record reads.

Exit: 0 current or rewritten, 1 stale under `strict`, any author error in `docs/graphs/`
or a declared graph whose source is there and could not be read, 2 a usage error.

Complexity: O(n) in tracked markdown plus one CLI-tree audit; the audit dominates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from lib import not_a_command
from lib.graph._declared import declarations, malformed, unresolvable
from lib.graph._edges import edge_findings, inter_edges
from lib.graph._readers import ReadError, build, read_all
from lib.graph._render import (
    GRAPHS_BEGIN,
    GRAPHS_END,
    graph_names,
    narrow,
    render_graphs_section,
)
from lib.graph.renderer import text
from lib.shared._readme_block import is_stale, splice
from lib.shared._root import find_repo_root


class Refused(Exception):
    """A reason `generate` declines: the lines it says on stderr, and the exit code."""

    def __init__(self, lines: list[str], code: int) -> None:
        super().__init__("\n".join(lines))
        self.lines = lines
        self.code = code


def _refuse(
    root: Path | None,
    readme: Path,
    *,
    docs: bool,
    path: Path | None,
    as_json: bool,
    output: Path | None,
) -> None:
    """Every reason this verb declines before it reads anything, raised as `Refused`."""
    if not docs:
        raise Refused(
            [
                "graph generate: say what to generate -- --docs is the only artefact "
                "this verb knows"
            ],
            2,
        )
    if root is None:
        raise Refused(["graph: no repo root found"], 1)
    if path is not None and path.resolve() != readme.resolve():
        # Refused by name rather than silently skipped.
        raise Refused(
            [
                f"graph generate: {path} declares no producer for this command; "
                f"today {readme.name} is the only artifact it writes"
            ],
            2,
        )
    if output is not None and not as_json:
        # A usage error: `-o` only applies under `--json`.
        raise Refused(
            [
                "graph generate: --output requires --json; there is no non-JSON report to "
                "write to a file"
            ],
            2,
        )


def _graphs(root: Path, only: str | None) -> list[dict[str, Any]]:
    """Every declared graph: its authored shape, its nodes, and its edges, in full.

    `edges: None` means the set cannot be derived, distinct from `[]`.
    """
    reads = read_all(root)
    rows = []
    for data in declarations(root):
        name = str(data["name"])
        if only is not None and name != only:
            continue
        read = reads.get(name)
        rows.append(
            {
                "name": name,
                "node": data["node"],
                "edge": data["edge"],
                "source": data["source"] if read is not None else "absent",
                "reader": data["reader"],
                "nodes": list(read.nodes) if read is not None else [],
                "edges": (
                    None
                    if read is None or read.edges is None
                    else [list(e) for e in read.edges]
                ),
            }
        )
    return rows


def _grade(root: Path) -> list[str]:
    """Every author error in `docs/graphs/`, before anything reads a graph.

    `edge_findings`: each declared inter-graph edge names a file that exists.
    `malformed`: each declaration carries the fields its kind requires.
    `unresolvable`: each `reader:` names code that is there.
    """
    declared = declarations(root)
    return edge_findings(root) + malformed(declared) + unresolvable(declared)


def _write_block(
    root: Path, readme: Path, *, graph: str | None, apply: bool
) -> dict[str, bool]:
    """The prose path: render the `## Graphs` block, compare it, and write it under
    `apply`. Returns `stale` (the block differed), `wrote` (it was rewritten), and
    `applied` (writing was asked for).
    """
    graphs = build(root)
    if graph is not None:
        if graph not in graph_names(graphs):
            raise Refused(
                [
                    f"graph generate: no graph named {graph!r}; this repo declares "
                    + ", ".join(repr(n) for n in graph_names(graphs))
                ],
                2,
            )
        existing = readme.read_text(encoding="utf-8") if readme.is_file() else ""
        graphs = narrow(graphs, existing, graph)

    section = render_graphs_section(graphs, inter_edges(root))
    stale = is_stale(readme, GRAPHS_BEGIN, GRAPHS_END, section)
    wrote = apply and stale
    if wrote:
        existing = readme.read_text(encoding="utf-8") if readme.is_file() else ""
        readme.write_text(
            splice(existing, GRAPHS_BEGIN, GRAPHS_END, section), encoding="utf-8"
        )
    return {"stale": stale, "wrote": wrote, "applied": apply}


def run(
    root: Path | None = None,
    *,
    docs: bool = False,
    apply: bool = False,
    path: Path | None = None,
    graph: str | None = None,
    as_json: bool = False,
    output: Path | None = None,
) -> dict[str, Any]:
    """`generate --docs`: grade the declarations, then read the graphs or render the block.

    `root` absent is the repo holding the working directory. Under `as_json` the record is
    `{"graphs": [...]}`, one row per declared graph, and nothing is written. Without it the
    record is the block's verdict, `{stale, wrote, applied}`, and `apply` rewrites a stale
    block. `output` is read here only to refuse it without `as_json`. Raises `Refused` for
    every reason the verb declines.
    """
    root = root if root is not None else find_repo_root(Path.cwd())
    readme = (root or Path.cwd()) / "README.md"
    _refuse(root, readme, docs=docs, path=path, as_json=as_json, output=output)
    assert root is not None

    findings = _grade(root)
    if findings:
        raise Refused([f"graph: {finding}" for finding in findings], 1)

    try:
        if as_json:
            return {"graphs": _graphs(root, graph)}
        return _write_block(root, readme, graph=graph, apply=apply)
    except ReadError as exc:
        # Refused: a graph nobody could read; nothing is written.
        raise Refused([f"graph: {exc}"], 1) from exc


def main(
    root: Path | None = None,
    *,
    docs: bool = False,
    apply: bool = False,
    strict: bool = False,
    path: Path | None = None,
    graph: str | None = None,
    as_json: bool = False,
    output: Path | None = None,
) -> int:
    """Run `generate --docs` and print its record: the graphs as JSON, or the verdict.

    `--apply` and `--strict` compose: the block is rewritten and the run exits non-zero.
    """
    try:
        record = run(
            root,
            docs=docs,
            apply=apply,
            path=path,
            graph=graph,
            as_json=as_json,
            output=output,
        )
    except Refused as refused:
        for line in refused.lines:
            print(line, file=sys.stderr)
        return refused.code

    if as_json:
        payload = json.dumps(record["graphs"], indent=2, sort_keys=False)
        if output is None:
            print(payload)
            return 0
        output.write_text(payload + "\n", encoding="utf-8")
        print(text.wrote(output))
        return 0

    if record["stale"] and not record["wrote"]:
        print(text.stale(), file=sys.stderr)
    else:
        print(text.generate(record))
    return 1 if (record["stale"] and strict) else 0


if __name__ == "__main__":
    not_a_command()
