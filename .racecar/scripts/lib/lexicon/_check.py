"""The `check` verb: grade the lexicon against itself and against the tree it projects onto.

Part of `lib.lexicon`; `scripts/lexicon.py` parses the command line and calls `main`. The
checks themselves are `_checks`, run by `_loop`; this composes one run and reports it.

Exit: 0 clean, 1 a blocking finding (any finding under `strict`), 2 the check cannot run.

Complexity: O(P log P + F*D + T*L), as `scripts/lexicon.py` states it for the whole tool
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from lib import not_a_command
from lib.lexicon._audit import has_cli
from lib.lexicon._corpora import Lexicon, LexiconError
from lib.lexicon._emit import emit
from lib.lexicon._graph import graph
from lib.lexicon._loop import checked, exit_code
from lib.lexicon._nodes import (
    FINDINGS,
    OK,
    UNMET,
    declared_domains,
    declared_kinds,
    shadowed_kinds,
)
from lib.lexicon._status import document
from lib.lexicon.renderer import text


def run(
    root: Path,
    lexicon: Lexicon,
    selected: list[str],
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """The check's record: the document `--json` prints, plus what the text view reads.

    `{domains, rows, nouns, findings, answers}` as `_status.document` builds it, and
    `graded` (false when nothing is declared in any named domain, so there was nothing to
    grade), `stubbed` (paths `apply` wrote), `shadowed` (`[kind, path]` for a kind two
    corpora declare) and `not_graded` (the halves this run skipped, which OK does not cover).
    Raises `LexiconError` when the check cannot run.
    """
    if not set(selected) & set(declared_domains(lexicon)):
        return {
            "domains": list(selected),
            "rows": [],
            "nouns": [],
            "findings": [],
            "answers": [],
            "graded": False,
            "stubbed": [],
            "shadowed": [],
            "not_graded": [],
        }
    # The list, read once.
    listed = graph(root, lexicon, selected)
    # The whole check, composed once, so every route runs exactly this.
    result = checked(listed, apply=apply)
    skipped = []
    if not declared_kinds(lexicon):
        skipped.append("projection (no ontology declared)")
    if not (listed.flag_canon or listed.flag_local):
        skipped.append("params (no canon to hold them to)")
    elif not has_cli(root):
        skipped.append("flags (no package under src/ holds a `__main__.py`)")
    return {
        **document(listed, result),
        "graded": True,
        "stubbed": list(result.made),
        "shadowed": [
            [kind, str(loser.relative_to(root))]
            for kind, loser in sorted(shadowed_kinds(lexicon).items())
        ],
        "not_graded": skipped,
    }


def main(
    root: Path,
    lexicon: Lexicon,
    selected: list[str],
    *,
    apply: bool = False,
    answers: bool = False,
    strict: bool = False,
    as_json: bool = False,
    output: Path | None = None,
) -> int:
    """Grade the lexicon against itself and against the tree it projects onto."""
    try:
        record = run(root, lexicon, selected, apply=apply)
    except LexiconError as err:
        print(text.cannot_run(err), file=sys.stderr)
        return UNMET
    code = FINDINGS if exit_code(record["findings"], strict) else OK
    # `--output` implies `--json`: naming a file asks for the record.
    if as_json or output is not None:
        # Stdout carries exactly one document; notes go to stderr.
        notes = text.check_notes(record, answers=answers)
        if notes:
            print(notes, file=sys.stderr)
        emit(record, output)
        return code
    print(text.check(record, answers=answers, strict=strict))
    return code


if __name__ == "__main__":
    not_a_command()
