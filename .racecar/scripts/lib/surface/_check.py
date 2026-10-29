"""The `check` verb: what differs from the canonical form or the lexicon, and with `against`
what an edit changed that a user would see.

Part of `lib.surface`; `scripts/surface.py` parses the command line and calls `main`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from lib import not_a_command

from . import _conform, _transcript
from ._faces import acting_on, default_api
from ._record import FINDINGS, OK, rel
from ._vocab import face, named
from .renderer import text


def run(
    root: Path,
    surface: str,
    noun: str | None = None,
    verb: str | None = None,
    *,
    against: str | None = None,
) -> list[dict[str, str]]:
    """What is wrong with one face: `{"surface", "kind", "finding"}` each.

    `against` names a git ref: every command line the edit since it can reach is run on both
    trees, and each whose transcript differs is a `transcript-differs` finding. A face other
    than the cli has nothing this package grades.
    """
    if face(surface) != "cli":
        return []
    found = _conform.check(root, surface, noun, verb)
    if against is not None:
        found += _transcript.findings(root, against, noun)
    return named(surface, found)


def main(
    root: Path,
    surfaces: list[str] | None,
    *,
    noun: str | None = None,
    verb: str | None = None,
    against: str | None = None,
    as_json: bool = False,
    api: Any = None,
) -> int:
    """Run `check` on each face through `api`, then print the findings; exit 1 on any."""
    api = api or default_api()
    faces, said = acting_on(root, surfaces, api)
    found = [
        f for one in faces for f in api.check(root, one, noun, verb, against=against)
    ]
    for line in text.notes(said):
        print(line, file=sys.stderr)
    shown = rel(root, found)
    assert isinstance(shown, list)
    if as_json:
        print(json.dumps(shown, indent=2))
    else:
        for line in text.check(shown, len(faces)):
            print(line)
        print(text.tally(len(shown), "finding(s)"), file=sys.stderr)
    return FINDINGS if shown else OK


if __name__ == "__main__":
    not_a_command()
