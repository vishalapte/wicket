"""The `list` verb: one row per noun and verb, or with `kind` the words of that kind.

Part of `lib.lexicon`; `scripts/lexicon.py` parses the command line and calls `main`.

Complexity: O(P) in lexicon nodes -- one listing, rendered once
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib import not_a_command
from lib.lexicon._corpora import Lexicon, LexiconError
from lib.lexicon._emit import emit
from lib.lexicon._graph import listing, words
from lib.lexicon._nodes import OK, declared_kinds
from lib.lexicon.renderer import text


def run(
    lexicon: Lexicon, selected: list[str], *, kind: list[str] | None = None
) -> list[dict[str, Any]]:
    """`[{domain, noun, verb, params}, ...]`, or with `kind` the words of those kinds.

    Built from `listing()`, which every other surface also prints. Bare `list` is the
    commands a surface carries; `--kind` is every node of those kinds, including meaning
    nodes that address no command, so `--kind verb` can return more rows. Raises
    `LexiconError` on a kind the lexicon does not declare.
    """
    if not kind:
        return listing(lexicon, selected)
    known = declared_kinds(lexicon)
    unknown = [k for k in kind if k not in known]
    if unknown:
        raise LexiconError(
            f"no such kind: {', '.join(unknown)} — this lexicon declares "
            f"{', '.join(sorted(known)) or '(none)'}"
        )
    return words(lexicon, selected, list(kind))


def main(
    lexicon: Lexicon,
    selected: list[str],
    *,
    kind: list[str] | None = None,
    as_json: bool = False,
    output: Path | None = None,
) -> int:
    """Run `list` and print its record, as text or as JSON."""
    table = run(lexicon, selected, kind=kind)
    if as_json or output is not None:
        emit(table, output)
    elif kind:
        print(text.words(table, list(kind)))
    else:
        print(text.listing(table))
    return OK


if __name__ == "__main__":
    not_a_command()
