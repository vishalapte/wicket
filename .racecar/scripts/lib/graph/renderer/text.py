"""Every graph verb's text view, rendered from the record the verb returns.

`scripts/graph.py` and `python -m racecar.graph` both call a verb for its record and hand
the record here; only this module prints.

The `## Graphs` block is not here: `generate` writes it into README.md, and
`lib.graph._render` makes it.

Complexity: O(n) in the record's rows
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lib import not_a_command
from lib.graph._render import REGENERATE

if TYPE_CHECKING:
    from lib.graph._build import BuildResult


def generate(record: dict[str, Any]) -> str:
    """`generate --docs`: the verdict on the README block, when it goes to stdout."""
    if record["wrote"]:
        return "wrote   README.md (## Graphs section)"
    if record["applied"]:
        return "graph: README.md ## Graphs already current"
    return "graph: OK (README.md ## Graphs is current)"


def stale() -> str:
    """`generate --docs` without `--apply`, on a stale block: what goes to stderr."""
    return f"graph: README.md ## Graphs is stale\n\nRegenerate:  {REGENERATE}"


def wrote(output: Path) -> str:
    """`generate --docs --json -o FILE`: the file the JSON went to."""
    return f"wrote   {output}"


def check(record: dict[str, Any]) -> str:
    """`check`: each error, each shadowed kind, the counts, the depth-0 set, the verdict."""
    errors, contains, peers = record["errors"], record["contains"], record["peers"]
    lines = [f"check: error: {e}" for e in errors]
    lines += [
        f"check: kind {kind!r} is declared in more than one corpus — {loser} is "
        "shadowed by the first joined corpus (info, not a finding)"
        for kind, loser in record["shadowed"]
    ]
    roots = [n for n, p in contains.items() if p is None]
    axioms = sorted(n for n in roots if not peers.get(n))
    derived = sorted(n for n in roots if peers.get(n))
    lines.append(
        f"check: {len(contains)} nodes, {len(roots)} roots, "
        f"{sum(len(v) for v in peers.values())} peer edges"
    )
    lines.append(f"check: depth-0 set: {len(axioms)} axiomatic, {len(derived)} derived")
    lines += [f"check:   {n} <- {', '.join(sorted(peers[n]))}" for n in derived]
    lines.append("check: OK" if not errors else f"check: {len(errors)} errors")
    return "\n".join(lines)


def materialize(record: dict[str, Any]) -> str:
    """`materialize`: whichever of its four views the record is; empty when a list is."""
    view = record["view"]
    if view == "edges":
        return "\n".join(f"{f}\t{r}\t{t}" for f, r, t in record["edges"])
    if view == "orphans":
        return "\n".join(
            f"unclaimed  {path}  — enforces a rule no node states"
            for path in record["unclaimed"]
        )
    if view == "coverage":
        return _coverage(record)
    return _tree(record)


def _coverage(record: dict[str, Any]) -> str:
    """`materialize --coverage`: the undeclared keys, the perimeter, then each gap."""
    undeclared = record["undeclared"]
    lines = [
        f"  UNDECLARED {row['key']:20} read by {', '.join(row['scripts'])[:40]}"
        for row in undeclared
    ]
    settings = record["settings"]
    lines.append(
        f"  ── perimeter: {settings} declared + {len(undeclared)} undeclared "
        f"= {settings + len(undeclared)} ──"
    )
    for gap in record["gaps"]:
        if gap["gap"] == "script":
            lines.append(f"  NO SCRIPT  {gap['node']:22} — declared and unenforced")
        else:
            lines.append(f"  NO TEST    {gap['node']:22} -> {','.join(gap['scripts'])}")
    return "\n".join(lines)


def _tree(record: dict[str, Any]) -> str:
    """Bare `materialize`: the tree, the totals, and the three most-claimed scripts."""
    lines = []
    for row in record["nodes"]:
        if row["kind"] == "rule":
            lines.append(f"\n{row['id']}  {row['title']}")
        elif row["kind"] == "group":
            lines.append(f"    {row['id']}")
        else:
            lines.append(
                f"        {row['class']:14} {row['id']:24} "
                f"{row['checked_by'] or '<- NO CHECK'}"
            )
    lines.append(
        f"\n{len(record['nodes'])} nodes, {record['edges']} edges, "
        f"{record['claimed']} scripts claimed"
    )
    lines += [f"  in-degree {count:2}  {script}" for script, count in record["top"]]
    return "\n".join(lines)


def perimeter(record: dict[str, Any]) -> str:
    """`perimeter`: each derived key the tree lacks, placed or not, then the totals."""
    placeable, unplaced = record["placeable"], record["unplaced"]
    lines = [
        f"  PLACE  {row['key']:22} -> {row['group']:26} "
        f"{', '.join(row['scripts'])[:40]}"
        for row in placeable
    ]
    lines += [
        f"  UNPLACED {row['key']:20} sites {row['sites'][:1]}  "
        f"{', '.join(row['scripts'])[:34]}"
        for row in unplaced
    ]
    lines.append(
        f"\nderived keys absent from the tree: {len(placeable) + len(unplaced)}"
        f"  ({len(placeable)} placeable by table, {len(unplaced)} need a principle)"
    )
    if record["wrote"] is not None:
        lines.append(f"wrote {record['wrote']} nodes")
    return "\n".join(lines)


def build(result: BuildResult) -> str:
    """`build`: what landed where, by kind, then every collision and the backup."""
    lines = [
        f"build: {result.sources_in} sources -> {result.sources_indexed} nodes "
        f"at {result.root} ({100 * result.unmapped_fraction:.0f}% unmapped)"
    ]
    lines += [f"  {kind:20} {count}" for kind, count in sorted(result.by_kind.items())]
    if result.collisions:
        lines.append(
            f"build: {len(result.collisions)} naming collision(s), resolve by hand:"
        )
        lines += [
            f"  {collision.path}: {[str(s) for s in collision.sources]}"
            for collision in result.collisions
        ]
    if result.backup:
        lines.append(f"build: prior tree backed up to {result.backup}")
    return "\n".join(lines)


def refusal(label: str, err: object) -> str:
    """One line naming what could not run and why: `<label>: <reason>`."""
    return f"{label}: {err}"


if __name__ == "__main__":
    not_a_command()
