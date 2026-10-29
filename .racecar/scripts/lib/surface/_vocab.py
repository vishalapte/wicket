"""The face vocabulary every surface verb shares: which faces exist, which this package
builds, and the two helpers each verb uses to check and label its records.

`cli` is built here. `rest` and `mcp` are one server rendered from `surface.jsonl` by
racecar's own generator, which is not delivered, so a verb given one says where it is built.
`api`, `bin` and `web` are in the vocabulary and built by nothing yet. A leaf: it imports no
other module of this package but the error and the form's vocabulary.
"""

from __future__ import annotations

from typing import Any

from ._error import SurfaceError
from ._form import SURFACES

#: The faces this delivered package builds.
BUILT = ("cli",)
#: The faces racecar's server generator builds from the spec; not delivered.
SERVED = ("rest", "mcp")


def not_built(name: str) -> str:
    """The one line every verb gives for a face this package does not build."""
    if name in SERVED:
        return (
            f"{name}: built by racecar's server generator, which is not delivered; run "
            f"`python -m racecar.surface <verb> --surface {name}` from a racecar checkout"
        )
    return f"{name}: not built by racecar.surface; nothing to do"


def face(name: str) -> str:
    """`name`, or a refusal naming the set when it is outside the vocabulary."""
    if name not in SURFACES:
        raise SurfaceError(f"--surface {name}: a face is one of {', '.join(SURFACES)}")
    return name


def named(surface: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`records`, each saying which face it is about."""
    return [{"surface": surface, **record} for record in records]
