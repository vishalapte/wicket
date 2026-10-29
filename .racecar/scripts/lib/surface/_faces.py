"""The `faces` answer: which faces this repo has, read from the repo, and which of them a
verb acts on.

`--all` means these, never racecar's list: `cli` where the lexicon declares a noun with
verbs, and each face a `surface.jsonl` row binds.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from lib.shared import _spec

from . import _conform
from ._error import SurfaceError

#: The faces a spec column can bind, in the order `faces` reports them.
_SPEC_FACES = ("rest", "mcp", "web")


def faces(root: Path) -> list[str]:
    """The faces this repo has: `cli` where the lexicon declares a noun with verbs, and each
    of `rest`, `mcp` and `web` that `surface.jsonl` binds on at least one row.

    Having no face is a fact about the repo, never an error.
    """
    have: list[str] = []
    try:
        if _conform.declared(root):
            have.append("cli")
    except SurfaceError:
        return have
    spec = _spec.spec_path(root)
    if spec.is_file():
        rows_ = _spec.read_rows(spec)
        have += [f for f in _SPEC_FACES if any(row.get(f) for row in rows_)]
    return have


def default_api() -> Any:
    """This package, the api a verb's `main` calls when its caller names none."""
    return importlib.import_module(__package__ or "lib.surface")


def acting_on(
    root: Path, surfaces: list[str] | None, api: Any
) -> tuple[list[str], list[str]]:
    """The faces a verb acts on, and the notes to say about them; prints nothing.

    Each `--surface` named, or every face this repo has. A face named and not built gets a
    note, so an empty result is not read as a face that was graded and found clean; so does
    a repo with no face at all. `renderer.text.notes` renders them.
    """
    if surfaces:
        named = list(dict.fromkeys(surfaces))
        return named, [api.not_built(face) for face in named if face not in api.BUILT]
    have: list[str] = list(api.faces(root))
    return have, [] if have else ["this repo has no face to act on"]
