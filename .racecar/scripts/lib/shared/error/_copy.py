"""Whether a package's copy of the error schema is there and current: the stale-copy check.

A package's `lib/error/_schema.py` is a copy of `scripts/error.json`, made by `surface
upgrade`. Sync replaces racecar's files in `.racecar/` but cannot write the package, so after
a sync the two can disagree. Only sync asks; no check reports it.

The package is read, never imported: `_schema.py` is parsed and its `SCHEMA` literal
evaluated, so a package that does not import still gets an answer. The reference is the
`error.json` beside this module's own `scripts/` tree: the racecar checkout the sync runs
from (a local clone, or the temporary one `sync_remote.py` makes).

Not copied into a package: the program never asks this question.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import NamedTuple

from .._root import package_dir

#: The error schema this tree delivers.
REFERENCE = Path(__file__).resolve().parents[3] / "error.json"

#: Each answer. `no_cli` and `current` are silent; the other three are a WARNING.
STATES = ("no_cli", "missing", "unreadable", "stale", "current")

_FIX = "Run `python3 .racecar/scripts/surface.py upgrade --surface cli`."


def _replace(error_dir: Path) -> str:
    """The command that puts the delivered modules over a stale or unreadable copy.

    `upgrade` writes only a missing file, so replacing a copy is the owner's command, printed
    whole.
    """
    return (
        "Replace it with the delivered copy: `cp .racecar/scripts/lib/shared/error/"
        f"_schema.py .racecar/scripts/lib/shared/error/_packet.py {error_dir}/`."
    )


_COST = (
    "`surface create` refuses new nouns, and a later `cli.py` that imports `lib/error/` "
    "will fail at import."
)


class State(NamedTuple):
    """One answer: which state, the copy it is about, and the line to show (None if silent)."""

    state: str
    path: Path | None
    line: str | None


def _reference_id(reference: Path) -> str:
    return str(json.loads(reference.read_text(encoding="utf-8"))["$id"])


def _copy_id(path: Path) -> str:
    """The `$id` of the `SCHEMA` literal in `path`; ValueError naming what is wrong."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as err:
        raise ValueError(f"does not parse ({err.__class__.__name__})") from err
    for node in tree.body:
        target: ast.expr
        value: ast.expr | None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if (
            not (isinstance(target, ast.Name) and target.id == "SCHEMA")
            or value is None
        ):
            continue
        try:
            schema = ast.literal_eval(value)
        except ValueError as err:
            raise ValueError("`SCHEMA` is not a literal") from err
        identifier = schema.get("$id") if isinstance(schema, dict) else None
        if isinstance(identifier, str):
            return identifier
        raise ValueError("`SCHEMA` has no string `$id`")
    raise ValueError("defines no `SCHEMA`")


def error_schema_state(root: Path, reference: Path = REFERENCE) -> State:
    """Where `root`'s package copy of the error schema stands against `reference`."""
    pkg = package_dir(root)
    if pkg is None or not (pkg / "__main__.py").is_file():
        return State("no_cli", None, None)
    path = pkg / "lib" / "error" / "_schema.py"
    shown = path.relative_to(root) if path.is_relative_to(root) else path
    if not path.is_file():
        line = f"WARNING: {shown} is missing, so {pkg.name} does not conform: {_COST} {_FIX}"
        return State("missing", path, line)
    try:
        held = _copy_id(path)
    except ValueError as err:
        line = f"WARNING: {shown} cannot be read: {err}. {_replace(shown.parent)}"
        return State("unreadable", path, line)
    wanted = _reference_id(reference)
    if held != wanted:
        line = (
            f"WARNING: {shown} is `{held.rsplit(':', 1)[-1]}`, `.racecar/` is "
            f"`{wanted.rsplit(':', 1)[-1]}`. {_replace(shown.parent)}"
        )
        return State("stale", path, line)
    return State("current", path, None)
