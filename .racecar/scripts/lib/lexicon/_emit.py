"""The one JSON document a lexicon verb produces, to stdout or to the file `--output` names.

Part of `lib.lexicon`. `check` and `list` both write it, so it lives once, below them.

Complexity: O(n) in the document's size
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from lib.lexicon.renderer import text


def emit(document: Any, destination: Path | None) -> None:
    """Write the JSON document to stdout, or to the single file `--output` names.

    `-o`/`--output` names a single file (`docs/lexicon/param/output.md`); `--target` is
    the directory form. The confirmation line goes to stderr, so stdout carries only the
    document.
    """
    payload = json.dumps(document, indent=2)
    if destination is None:
        print(payload)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload + "\n", encoding="utf-8")
    print(text.wrote(destination), file=sys.stderr)
