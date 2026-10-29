"""Which Python runs a repo's own code: its virtualenv's, where it has one.

A probe that imports a repo's code (a checker reading its CLI tree, a transcript running its
commands) must import against the repo's own dependencies, not racecar's, or a package the
repo installed reads as missing.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def repo_venv(root: Path, environ: Mapping[str, str] | None = None) -> Path:
    """The venv `racecar.mk` picks for `root`, which may not exist yet.

    `RC_VENV` from the environment, else the first of `.venv`, `venv`, `../venv` that
    exists, else `.venv` (arch-python/MAKEFILE.md §1). A `VENV` on make's command line is
    make's alone: no other process can tell it from one exported for another tool.
    """
    named = (os.environ if environ is None else environ).get("RC_VENV")
    if named:
        return root / named
    for name in (".venv", "venv", "../venv"):
        if os.path.lexists(root / name):
            return root / name
    return root / ".venv"


def repo_python(root: Path) -> str:
    """The repo venv's interpreter, where it runs; else the one running this.

    The venv is `repo_venv`'s, as `racecar.mk`'s `RC_PYTHON` uses it: a venv whose
    interpreter is missing or points at a removed Python counts as none.
    """
    python = repo_venv(root) / "bin" / "python"
    return str(python) if python.exists() else sys.executable
