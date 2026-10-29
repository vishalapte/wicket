"""The `derive` verb: infer a candidate ontology from a corpus, and write it.

Part of `lib.ontology`; `scripts/ontology.py` parses the command line and calls `main`. The
same inference `identify` runs (`_kinds.derive`), written to `--meta` as the
`ontology.yaml` shape `_kinds.load_ontology` reads.

Exit: 0 always -- a proposal is a report, and an empty one says so.

Complexity: O(S) in sources sampled, as `_kinds` states it
"""

from __future__ import annotations

from pathlib import Path

from lib import not_a_command
from lib.ontology._kinds import (
    ONTOLOGY_FILENAME,
    Proposal,
    derive,
    render_ontology_yaml,
)
from lib.ontology.renderer import text
from lib.shared._as_json import print_json


def run(data: Path, meta: Path) -> Proposal:
    """Infer the proposal from `data` and write it to `meta`; the record is the proposal.

    `meta` names a directory (existing, or a path with no suffix), which receives an
    `ontology.yaml`, or a file, written as given. Either way its parent is created.
    """
    found = derive(data)
    meta_path = meta
    if meta_path.is_dir() or not meta_path.suffix:
        meta_path.mkdir(parents=True, exist_ok=True)
        meta_path = meta_path / ONTOLOGY_FILENAME
    else:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(render_ontology_yaml(data.name, found), encoding="utf-8")
    return found


def main(data: Path, meta: Path, *, as_json: bool = False) -> int:
    """Infer and write the proposal, then print it, as text or as JSON."""
    record = run(data, meta)
    if as_json:
        print_json(record)
    else:
        print(text.proposal(record))
    return 0


if __name__ == "__main__":
    not_a_command()
