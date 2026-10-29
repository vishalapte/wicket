"""The `list` verb: each declared noun and verb, and whether the cli binds it.

Part of `lib.surface`; `scripts/surface.py` parses the command line and calls `main`. The
package re-exports `run` as `rows`, because a module-level `list` shadows the builtin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from lib import not_a_command

from . import _conform
from ._faces import acting_on, default_api
from ._record import OK
from ._vocab import face, named
from .renderer import text


def run(root: Path, surface: str, noun: str | None = None) -> list[dict[str, str]]:
    """Each declared noun and verb, and whether one face binds it.

    `{"surface", "noun", "verb", "built", "noun_state"}` each. A face other than the cli has
    nothing this package lists.
    """
    if face(surface) != "cli":
        return []
    return named(surface, _conform.rows(root, surface, noun))


def main(
    root: Path,
    surfaces: list[str] | None,
    *,
    noun: str | None = None,
    as_json: bool = False,
    api: Any = None,
) -> int:
    """Run `list` on each face through `api`, then print the rows; always exit 0."""
    api = api or default_api()
    faces, said = acting_on(root, surfaces, api)
    found = [r for one in faces for r in api.rows(root, one, noun)]
    for line in text.notes(said):
        print(line, file=sys.stderr)
    if as_json:
        print(json.dumps(found, indent=2))
        return OK
    for line in text.listing(found, len(faces)):
        print(line)
    return OK


if __name__ == "__main__":
    not_a_command()
