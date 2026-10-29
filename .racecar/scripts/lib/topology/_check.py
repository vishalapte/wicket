"""The `check` verb: check a corpus's tier shape against the topology it declares.

`scripts/topology.py` parses the command line and calls `main`. `_walk.check` does the grade;
this verb resolves the default declaration and `renderer.text` renders the record.

Exit: 0 the tier shape holds, 1 any error.

Complexity: O(N), N = nodes walked, as `_walk` states it
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib import not_a_command
from lib.shared._as_json import print_json
from lib.topology._walk import check
from lib.topology.renderer import text


def run(
    data: Path | None = None,
    meta: Path | None = None,
    domains: list[str] | None = None,
) -> dict[str, Any]:
    """The topology at `data` as a graph, graded against the topology declared at `meta`.

    `data` absent is racecar's own `architecture/`; `meta` absent is `data`'s own
    `topology/` subdirectory, `architecture/topology` when `data` is absent too. The record
    is `_walk.check`'s: `nodes`, `contains` (each node's parent id, None for a root),
    `peers` (each node's declared peer ids) and `errors`, empty when the tier shape holds.
    `domains` grades one projection.
    """
    meta_path = (
        meta if meta is not None else (data or Path("architecture")) / "topology"
    )
    return check(data, meta_path, domains)


def main(
    data: Path | None = None,
    meta: Path | None = None,
    domains: list[str] | None = None,
    *,
    as_json: bool = False,
) -> int:
    """Check the tier shape and print the record, as text or as JSON; 0 clean, 1 any error."""
    record = run(data, meta, domains)
    code = 1 if record["errors"] else 0
    if as_json:
        print_json(record)
        return code
    print(text.check(record))
    return code


if __name__ == "__main__":
    not_a_command()
