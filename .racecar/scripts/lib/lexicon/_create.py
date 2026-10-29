"""The `create` verb: declare a noun, verb or param, or put one reported tuple into canon.

Part of `lib.lexicon`; `scripts/lexicon.py` parses the command line and calls `main`.

Complexity: O(P) in lexicon nodes for `tuples`, which reads the list once; O(1) writes
per declared entry otherwise
"""

from __future__ import annotations

import sys
from pathlib import Path

from lib import not_a_command
from lib.lexicon._corpora import Lexicon, LexiconError
from lib.lexicon._emit import emit
from lib.lexicon._graph import graph
from lib.lexicon._nodes import OK, UNMET
from lib.lexicon._scaffold import (
    CREATE_FORMS,
    declare,
    parse_tuple,
    plan_tuple,
    write_tuple,
)
from lib.lexicon.renderer import text


def run(
    root: Path,
    lexicon: Lexicon,
    selected: list[str],
    *,
    noun: str | None = None,
    verb: str | None = None,
    params: list[str] | None = None,
    tuples: list[str] | None = None,
    apply: bool = False,
) -> dict[str, list[str]]:
    """Declare what the flags name: `--noun [--verb [--param]]`, or one `--tuple`.

    The record is `{"declared": paths}` for the first form, and `{"would_create": paths}`
    or, with `apply`, `{"created": paths}` for the second. The lexicon writes entries, never
    code, so `create` with neither form is refused. Raises `LexiconError` on a refusal.
    """
    if noun or verb or params:
        if not noun:
            raise LexiconError("--verb/--param needs --noun")
        written = declare(lexicon, noun, verb, params or [], root=root)
        return {"declared": list(written)}
    if not tuples:
        raise LexiconError(CREATE_FORMS)
    listed = graph(root, lexicon, selected)
    wanted = [parse_tuple(spec, lexicon) for spec in tuples]
    made = [
        path
        for row in wanted
        for path in [write_tuple(row, listed) if apply else plan_tuple(row)]
        if path
    ]
    return {"created" if apply else "would_create": made}


def main(
    root: Path,
    lexicon: Lexicon,
    selected: list[str],
    *,
    noun: str | None = None,
    verb: str | None = None,
    params: list[str] | None = None,
    tuples: list[str] | None = None,
    apply: bool = False,
    as_json: bool = False,
    output: Path | None = None,
) -> int:
    """Run `create` and print its record, as text or as JSON."""
    try:
        record = run(
            root,
            lexicon,
            selected,
            noun=noun,
            verb=verb,
            params=params,
            tuples=tuples,
            apply=apply,
        )
    except LexiconError as err:
        print(text.refusal("lexicon create", err), file=sys.stderr)
        return UNMET
    if as_json or output is not None:
        emit(record, output)
    else:
        print(text.create(record))
    return OK


if __name__ == "__main__":
    not_a_command()
