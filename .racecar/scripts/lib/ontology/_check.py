"""The `check` verb: score a corpus against the ontology declared at `--meta`.

Part of `lib.ontology`; `scripts/ontology.py` parses the command line and calls `main`. The
scoring is `_kinds.check`; the record carries the declared ontology beside the corpus's fit.

Exit: 0 the declared ontology fits, 1 it does not, 2 the check cannot run (a cyclic
`depends_on`, or `--domain` against a corpus that declares no `partition:`).

Complexity: O(S) in sources sampled, as `_kinds` states it
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import Any

from lib import not_a_command
from lib.ontology._kinds import check, load_ontology
from lib.ontology.renderer import text
from lib.shared._as_json import print_json

#: The check could not run, as distinct from a fit that failed.
UNMET = 2


@dataclasses.dataclass(frozen=True)
class Checked:
    """The declared ontology graph at `meta`, and how the corpus at `data` fits it."""

    ontology: dict[str, Any]
    best: str | None
    unmapped_fraction: float
    total: int
    by_kind: dict[str, int]


def run(data: Path, meta: Path, domains: list[str] | None = None) -> Checked:
    """Score `data` against `meta`'s ontology, optionally one projection of it.

    `domains` scores one projection of a partitioned corpus; projections are never
    averaged. Raises `ValueError` when the check cannot run.
    """
    fit = check(data, meta, domains)
    return Checked(
        dict(load_ontology(meta) or {}),
        fit.best,
        fit.unmapped_fraction,
        fit.total,
        dict(fit.by_kind),
    )


def main(
    data: Path,
    meta: Path,
    domains: list[str] | None = None,
    *,
    as_json: bool = False,
) -> int:
    """Score the corpus and print the record, as text or as JSON."""
    try:
        record = run(data, meta, domains)
    except ValueError as err:
        print(text.refusal("check", err), file=sys.stderr)
        return UNMET
    code = 0 if record.best is not None else 1
    if as_json:
        print_json(record)
    else:
        print(text.check(record, domains))
    return code


if __name__ == "__main__":
    not_a_command()
