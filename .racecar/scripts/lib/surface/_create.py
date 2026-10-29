"""The `create` verb: declare a noun or verb, then build its cli where absent.

Part of `lib.surface`; `scripts/surface.py` parses the command line and calls `main`. The
building is `_cli`'s; `run` routes the face and refuses what only the cli takes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from lib import not_a_command

from . import _cli
from ._error import SurfaceError
from ._faces import acting_on, default_api
from ._record import FINDINGS, OK, face_line, rel
from ._vocab import face, not_built
from .renderer import text


def run(
    root: Path,
    surface: str,
    noun: str,
    verb: str | None = None,
    params: list[str] | None = None,
    *,
    kind: str | None = None,
) -> dict[str, list[str]]:
    """Declare, then build, on one face: `{"declared", "notes", "refused"}`.

    The cli is built here; any other face is noted as not built by this package. Raises
    `SurfaceError` when `params` or `kind` are given for a face that is not the cli.
    """
    if face(surface) == "cli":
        return _cli.create(root, surface, noun, verb, params, kind=kind)
    if params or kind is not None:
        raise SurfaceError(
            "--param and --kind declare a cli verb; the rest and mcp faces declare "
            "nothing, since the spec is the owner's"
        )
    return {"declared": [], "notes": [not_built(surface)], "refused": []}


def main(
    root: Path,
    surfaces: list[str] | None,
    noun: str,
    *,
    verb: str | None = None,
    params: list[str] | None = None,
    kind: str | None = None,
    every: bool = False,
    as_json: bool = False,
    api: Any = None,
) -> int:
    """Run `create` on each face through `api`, then print what it declared, noted and
    refused; exit 1 on a refusal.

    One record for the run: each face's lists joined, each line naming its face when the
    run covers more than one.
    """
    api = api or default_api()
    faces, said = acting_on(root, surfaces, api)
    made = [
        (
            one,
            api.create(
                root,
                one,
                noun,
                verb,
                # --param and --kind declare a cli verb; under --all the other faces take
                # neither.
                params if one == "cli" or not every else None,
                kind=kind if one == "cli" or not every else None,
            ),
        )
        for one in faces
    ]
    for line in text.notes(said):
        print(line, file=sys.stderr)
    result: dict[str, list[str]] = {"declared": [], "notes": [], "refused": []}
    for one, lines_by_key in made:
        for key, lines in lines_by_key.items():
            result[key] += [face_line(one, line, len(made)) for line in lines]
    shown = rel(root, result)
    assert isinstance(shown, dict)
    code = FINDINGS if shown["refused"] else OK
    if as_json:
        print(json.dumps(shown, indent=2))
        return code
    for line in text.create(shown):
        print(line)
    if shown["refused"]:
        print(text.create_refused(len(shown["refused"])), file=sys.stderr)
    return code


if __name__ == "__main__":
    not_a_command()
