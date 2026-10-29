"""The topology: declare a corpus's tier shape, walk the corpus by it, and grade the walk.

Delivered whole into an adopter's `.racecar/scripts/lib/topology/`. `scripts/topology.py` and
`racecar.graph.topology` are the command lines over it.

  `_check`  check a corpus's tier shape against the topology it declares

`_walk` holds the declaration, the walk and the grade (`check`, which prints nothing).
`lib.ontology` and `lib.graph` import the walk from there.

Complexity: O(1) -- re-exports only
"""

from __future__ import annotations

from lib.topology._walk import (
    META_ROOT,
    ROOT,
    TOPOLOGY_DIRNAME,
    TOPOLOGY_FILENAME,
    Tier,
    Topology,
    check,
    load,
    load_topology_spec,
    project,
    resolve,
    structural_findings,
)

__all__ = [
    "META_ROOT",
    "ROOT",
    "TOPOLOGY_DIRNAME",
    "TOPOLOGY_FILENAME",
    "Tier",
    "Topology",
    "check",
    "load",
    "load_topology_spec",
    "project",
    "resolve",
    "structural_findings",
]
