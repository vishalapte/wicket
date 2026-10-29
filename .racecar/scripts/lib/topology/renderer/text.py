"""Every topology verb's text view, rendered from the record the verb returns.

`scripts/topology.py` and `python -m racecar.graph.topology` both render through this module.
`--json` prints the record itself.

Complexity: O(n) in the record's nodes
"""

from __future__ import annotations

from typing import Any

from lib import not_a_command


def check(record: dict[str, Any]) -> str:
    """`check`: each error, the node and root counts, the depth-0 set, then the verdict."""
    errors, contains, peers = record["errors"], record["contains"], record["peers"]
    lines = [f"topology.check: error: {e}" for e in errors]
    roots = [n for n, p in contains.items() if p is None]
    axioms = sorted(n for n in roots if not peers.get(n))
    derived = sorted(n for n in roots if peers.get(n))
    lines.append(
        f"topology.check: {len(record['nodes'])} nodes, {len(roots)} roots, "
        f"{sum(len(v) for v in peers.values())} peer edges"
    )
    lines.append(
        f"topology.check: depth-0 set: {len(axioms)} axiomatic, {len(derived)} derived"
    )
    lines += [
        f"topology.check:   {n} <- {', '.join(sorted(peers[n]))}" for n in derived
    ]
    lines.append(
        "topology.check: OK" if not errors else f"topology.check: {len(errors)} errors"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    not_a_command()
