"""The `identify` verb: infer a candidate ontology from a corpus, read-only.

Part of `lib.ontology`; `scripts/ontology.py` parses the command line and calls `main`. The
inference is `_kinds.derive`, the same pass `derive` runs; this verb stops at the proposal
and writes nothing.

Exit: 0 always -- a proposal is a report, and an empty one says so.

Complexity: O(S) in sources sampled, as `_kinds` states it
"""

from __future__ import annotations

from pathlib import Path

from lib import not_a_command
from lib.ontology._kinds import Proposal, derive
from lib.ontology.renderer import text
from lib.shared._as_json import print_json


def run(data: Path) -> Proposal:
    """The candidate ontology `data` implies: `{kinds, dag, total}`, inferred from presence."""
    return derive(data)


def main(data: Path, *, as_json: bool = False) -> int:
    """Infer the proposal and print it, as text or as JSON."""
    record = run(data)
    if as_json:
        print_json(record)
    else:
        print(text.proposal(record))
    return 0


if __name__ == "__main__":
    not_a_command()
