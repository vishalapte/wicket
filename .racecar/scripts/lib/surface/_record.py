"""What a verb's record carries as it leaves `main`: its exit code, and its paths and lines
said the way a reader of that record needs them.

Not the text view. `renderer.text` makes the lines a person reads; these shape the record
itself, so `--json` and the text view print the same values. `rel` rewrites every path a
record holds relative to the repo it is about, and `face_line` names the face a line came
from when a run covers more than one -- which `create` writes into its record, not only into
its text.

Exit codes are the vocabulary every racecar command uses: 0 nothing left, 1 findings, 2 the
request could not be carried out.

Complexity: O(R) in the record's values
"""

from __future__ import annotations

from pathlib import Path

OK = 0
FINDINGS = 1
UNMET = 2


def rel(root: Path, value: object) -> object:
    """Every path in a result, said relative to the repo it is about."""
    prefix = f"{root}/"
    if isinstance(value, str):
        return value.replace(prefix, "")
    if isinstance(value, list):
        return [rel(root, v) for v in value]
    if isinstance(value, dict):
        return {k: rel(root, v) for k, v in value.items()}
    return value


def face_line(face: str, line: str, faces: int) -> str:
    """A line, prefixed with its face when the run covers more than one."""
    return f"{face}: {line}" if faces > 1 else line
