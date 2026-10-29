"""The `upgrade` verb: make every mechanical change, and report what is left.

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
    root: Path, surface: str, noun: str | None = None, verb: str | None = None
) -> dict[str, list[dict[str, str]]]:
    """Make the mechanical changes on one face; report what is left.

    `{"changed": [{"surface", "status", "detail"}], "remaining": [change records]}`. A node
    a change reached is not done while one of its commands prints differently than it did
    at `HEAD`: each difference is left as judgment. A face other than the cli has nothing
    this package changes.
    """
    if face(surface) != "cli":
        return {"changed": [], "remaining": []}
    done = _conform.upgrade(root, surface, noun, verb)
    if done["changed"]:
        done["remaining"] += _transcript.unchanged_or_left(root, noun)
    return {key: named(surface, value) for key, value in done.items()}


def main(
    root: Path,
    surfaces: list[str] | None,
    *,
    noun: str | None = None,
    verb: str | None = None,
    as_json: bool = False,
    api: Any = None,
) -> int:
    """Run `upgrade` on each face through `api`, then print what changed and what is left;
    exit 1 when anything is left.

    One record for the run: each face's `changed` and `remaining` joined.
    """
    api = api or default_api()
    faces, said = acting_on(root, surfaces, api)
    done = [api.upgrade(root, one, noun, verb) for one in faces]
    for line in text.notes(said):
        print(line, file=sys.stderr)
    merged: dict[str, list[dict[str, str]]] = {"changed": [], "remaining": []}
    for one in done:
        for key, found in one.items():
            merged[key] += found
    result = rel(root, merged)
    assert isinstance(result, dict)
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        for line in text.upgrade(result, len(done)):
            print(line)
        print(text.upgrade_tally(result), file=sys.stderr)
    warning = text.upgrade_warning(result)
    if warning:
        print(warning, file=sys.stderr)
    return FINDINGS if result["remaining"] else OK


if __name__ == "__main__":
    not_a_command()
