"""The surface worker: build the cli face of a repo from its lexicon, and bring an existing
cli into the canonical form.

Delivered, so every repo that syncs racecar builds and conforms its own nouns and verbs:
`scripts/surface.py` is the command line over this package, and `racecar.surface` wraps it
the way `racecar.graph` wraps `lib.graph`. It reads and writes declarations only through the
delivered `lib.lexicon`, and imports nothing from racecar's package.

One verb, one module, each with `run` (one face's record, printing nothing) and `main` (every
face the run acts on, printed through `renderer.text` or as the record under `--json`):

  `_create`      declare a noun or verb, then build it where absent
  `_check`       what differs from the form or the lexicon; `against` what an edit changed
  `_update`      what `upgrade` would change, and what it leaves
  `_upgrade`     make the mechanical changes; report what is left
  `_list`        each declared noun and verb, and whether the cli binds it
  `_check_json`  run each read command with `--json`
  `_generate`    render the face's docs: the README's `## CLI` block

and beneath them `_cli` (building), `_conform` (planning), `_form` (the canonical form),
`_edit` (source edits), `_invocations` and `_transcript` (the before-and-after comparison),
`_vocab` and `_faces` (the faces, and which of them a run acts on), `_record` (exit codes,
and the paths and lines a record carries), and `renderer.text` (every line a verb prints).

Each verb's `run` is re-exported under the name the api gives it, so this package is the
api a verb's `main` calls when its caller names none.

Complexity: O(1) -- re-exports only
"""

from __future__ import annotations

from ._check import run as check
from ._check_json import run as check_json
from ._cli import KINDS
from ._create import run as create
from ._error import SurfaceError
from ._faces import faces
from ._form import SURFACES, package_of
from ._generate import run as generate
from ._invocations import for_face as invocations
from ._list import run as rows
from ._update import run as update
from ._upgrade import run as upgrade
from ._vocab import BUILT, SERVED, face, named, not_built

__all__ = [
    "BUILT",
    "KINDS",
    "SERVED",
    "SURFACES",
    "SurfaceError",
    "check",
    "check_json",
    "create",
    "face",
    "faces",
    "generate",
    "invocations",
    "named",
    "not_built",
    "package_of",
    "rows",
    "update",
    "upgrade",
]
