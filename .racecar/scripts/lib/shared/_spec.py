"""Where a package keeps its surface spec, and the spec's rows.

The spec is `surface.jsonl` at the repo root: one JSON object per line, one line per
command, and the one statement of what each command is and where it is served. The checker
that grades it, the cli face that writes rows into it, the server generator that reads it,
and the checks that run each command all need the same two answers, so they take them here.

A line that is not JSON is refused by name, never skipped.

Complexity: O(n) in lines
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.shared._root import package_dir

SPEC_NAME = "surface.jsonl"


class SpecError(ValueError):
    """A spec line that is not a JSON object, named by file and line."""


def spec_path(root: Path) -> Path:
    """Where the repo keeps its spec, whether or not the file exists yet.

    At the root, not in a package: every shape serves commands, and a `server`-only repo
    has no `src/<pkg>/` to hold one.
    """
    return root / SPEC_NAME


def find_spec(root: Path) -> tuple[Path, str | None] | None:
    """(spec path, the repo's package, or None when it has none) when the
    repo has a spec; None when it has not. The package is what the spec's `cli` column is
    graded against, and a `server`-only repo has no package and no cli."""
    spec = spec_path(root)
    if not spec.is_file():
        return None
    package = package_dir(root)
    return spec, (package.name if package else None)


#: The group a row of the root noun names: the package itself has no noun of its own.
ROOT_GROUP = "root"


def row_id(group: str, verb: str) -> str:
    """A row's key: the noun it groups under, then the verb."""
    return f"{group}.{verb}"


def new_row(ident: str, group: str) -> dict[str, Any]:
    """A row decided and not built: `proposed`, with no face bound and every field that is
    not known yet null."""
    return {
        "id": ident,
        "group": group,
        "base": None,
        "layer": "tangible",
        "kind": None,
        "fn": None,
        "web": [],
        "rest": [],
        "canonical": None,
        "mcp": None,
        "cli": None,
        "params": [],
        "record": [],
        "scope": None,
        "status": "proposed",
    }


def upsert_row(spec: Path, ident: str, group: str, fields: dict[str, Any]) -> bool:
    """Get-or-create the spec and its row `ident`, then set `fields` on the row; every
    field not named keeps its value. True when the file changed.

    `lexicon create` writes a row with what the lexicon knows; `surface create` fills the
    fields its face builds. Neither clears what the other wrote.

    Only this row's line is written, replaced in place or appended; every other line keeps
    its bytes.
    """
    lines: list[str] = []
    if spec.is_file():
        read_rows(spec)  # the whole file must read; it raises if not
        lines = spec.read_text(encoding="utf-8").splitlines(keepends=True)
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("id") == ident:
            if all(row.get(key) == value for key, value in fields.items()):
                return False
            row.update(fields)
            lines[n] = json.dumps(row) + "\n"
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(json.dumps({**new_row(ident, group), **fields}) + "\n")
    spec.write_text("".join(lines), encoding="utf-8")
    return True


def read_rows(spec: Path) -> list[dict[str, Any]]:
    """Every row of the spec, in order.

    Raises `SpecError` on a file that cannot be read as UTF-8 text, a line that is not a JSON
    object, or a row with no string `id`.
    """
    rows: list[dict[str, Any]] = []
    try:
        text = spec.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError(f"{spec}: cannot be read as UTF-8 text — {exc}") from exc
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SpecError(f"{spec}:{n}: not valid JSON — {exc}") from exc
        if not isinstance(row, dict):
            raise SpecError(
                f"{spec}:{n}: a row is a JSON object, not {type(row).__name__}"
            )
        if not isinstance(row.get("id"), str) or not row["id"]:
            raise SpecError(f"{spec}:{n}: a row has no `id`; every row is keyed by one")
        rows.append(row)
    return rows


def command(row: dict[str, Any]) -> tuple[str, str] | None:
    """`(module, verb)` for a row whose `cli` is `python -m <module> <verb>`, else None.

    The value is exactly four words (arch-python/SURFACES.md, the `cli` field): `python`,
    `-m`, the module, the verb. A bare `python -m <module>` answers None, and so does a value
    with anything after the verb.
    """
    words = str(row.get("cli") or "").split()
    if len(words) != 4 or words[:2] != ["python", "-m"]:
        return None
    return words[2], words[3]
