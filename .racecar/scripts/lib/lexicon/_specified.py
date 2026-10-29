"""The `specified` rule: a line in `surface.jsonl` that no lexicon node declares.

Part of `lib.lexicon`, the package behind `scripts/lexicon.py`. A `proposed` line for a
command that is not built is checked by neither `implemented` nor `check_surface.py`; this
rule compares the spec, read as text, against the lexicon, loading no code.

Complexity: O(S + R), S = spec lines, R = tuples in the list
"""

from __future__ import annotations

from lib.lexicon._graph import Answer, Finding, Graph, undeclared
from lib.lexicon._nodes import corpus_domain, root_noun
from lib.shared import _spec

#: The `group` a root command's line carries. A root command has no noun to be grouped
#: under, and the spec names it `root.<verb>` (arch-python/SURFACES.md §19).
ROOT_GROUP = "root"


def _declared(g: Graph) -> set[tuple[str, str]]:
    """`(noun, verb)` for every command the list names, the root as `ROOT_GROUP`."""
    root = root_noun(g.lexicon)
    return {
        (ROOT_GROUP if row.noun == root else row.noun, row.verb)
        for row in g.rows
        if row.verb is not None
    }


def undeclared_rows(g: Graph) -> list[Answer]:
    """Every spec line whose command the lexicon does not declare.

    Asked only of the corpus's own domain (`corpus_domain`), which declares the package's
    commands. No `surface.jsonl` returns no answer. A spec that does not parse is one
    finding naming the line.
    """
    domain = corpus_domain(g.lexicon)
    if g.selected and domain not in g.selected:
        return []
    found = _spec.find_spec(g.root)
    if found is None:
        return []
    spec, _ = found
    where = str(spec.relative_to(g.root))
    try:
        rows = _spec.read_rows(spec)
    except _spec.SpecError as err:
        return [
            Answer(
                "",
                undeclared(domain, root_noun(g.lexicon), None, g.lexicon),
                (),
                (Finding(where, f"cannot be read, so it was not compared: {err}", ""),),
            )
        ]
    declared = _declared(g)
    out: list[Answer] = []
    for row in rows:
        ident = str(row.get("id") or "")
        noun, _, verb = ident.rpartition(".")
        if not noun or (noun, verb) in declared:
            continue
        lexicon_noun = root_noun(g.lexicon) if noun == ROOT_GROUP else noun
        status = row.get("status") or "exists"
        out.append(
            Answer(
                "",
                undeclared(domain, lexicon_noun, verb, g.lexicon),
                (f"{where}: {ident} ({status})",),
                (
                    Finding(
                        where,
                        f"`{ident}` is a {status} line with no lexicon node. The lexicon "
                        "is where a command is asked for, so either the line should not "
                        "be there or the lexicon should name it -- `lexicon create "
                        f"--tuple {domain}/{lexicon_noun}/{verb}`.",
                        "",
                    ),
                ),
            )
        )
    return out
