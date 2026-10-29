"""The ontology: what kinds of thing a corpus contains, and what each must carry.

`scripts/ontology.py` is one command line over this and `racecar.graph.ontology` is another,
so a caller imports what it needs instead of loading a script by path to reach a function.

Delivered whole into an adopter's `.racecar/scripts/lib/ontology/`, like `lib.lexicon`.

One verb, one module, each with a `main` that `scripts/ontology.py` dispatches to:

  `_identify`  infer a candidate ontology from a corpus, read-only
  `_derive`    infer it, and write it to `--meta`
  `_check`     score a corpus against the ontology declared at `--meta`

and beneath them `_kinds`: reading a declared ontology, sampling a corpus, scoring the one
against the other, and inferring a proposal. `lib.graph` imports it from there.

Public names are re-exported here so `from lib.ontology import load_ontology` works.

Complexity: O(1) -- re-exports only
"""

from __future__ import annotations

from lib.ontology._kinds import (
    ONTOLOGY_FILENAME,
    Fit,
    Proposal,
    acyclic_kinds,
    check,
    derive,
    load_ontology,
    ontology_shadowed,
    relation_findings,
    render_ontology_yaml,
    sample,
)

__all__ = [
    "ONTOLOGY_FILENAME",
    "Fit",
    "Proposal",
    "acyclic_kinds",
    "check",
    "derive",
    "load_ontology",
    "ontology_shadowed",
    "relation_findings",
    "render_ontology_yaml",
    "sample",
]
