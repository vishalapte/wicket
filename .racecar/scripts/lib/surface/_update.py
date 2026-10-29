"""The `update` verb: what `upgrade` would change, and what it leaves; writes nothing.

Part of `lib.surface`; `scripts/surface.py` parses the command line and calls `main`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from lib import not_a_command

from . import _conform
from ._faces import acting_on, default_api
from ._record import FINDINGS, OK, rel
from ._vocab import face, named
from .renderer import text


def run(
    root: Path, surface: str, noun: str | None = None, verb: str | None = None
) -> list[dict[str, str]]:
    """What `upgrade` would change on one face, and what it leaves; writes nothing.

    `{"surface", "action", "kind", "text"}` each, with `why` on a refusal. A face other than
    the cli has nothing this package changes.
    """
    if face(surface) != "cli":
        return []
    return named(surface, _conform.update(root, surface, noun, verb))


def main(
    root: Path,
    surfaces: list[str] | None,
    *,
    noun: str | None = None,
    verb: str | None = None,
    as_json: bool = False,
    api: Any = None,
) -> int:
    """Run `update` on each face through `api`, then print what is due; exit 1 on any."""
    api = api or default_api()
    faces, said = acting_on(root, surfaces, api)
    due = [r for one in faces for r in api.update(root, one, noun, verb)]
    for line in text.notes(said):
        print(line, file=sys.stderr)
    shown = rel(root, due)
    assert isinstance(shown, list)
    if as_json:
        print(json.dumps(shown, indent=2))
    else:
        for line in text.update(shown, len(faces)):
            print(line)
        print(text.tally(len(shown), "change(s) due"), file=sys.stderr)
    return FINDINGS if shown else OK


if __name__ == "__main__":
    not_a_command()
