"""Shared helpers: TOML loading, path rendering, dist-name extraction."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from ._findings import Finding


def _toml_load(path: Path, label: str) -> tuple[dict[str, Any] | None, list[Finding]]:
    """Read and parse a TOML file.

    Return (data_or_None, findings). `label` is the audit-rendered filename.
    """
    if not path.exists():
        return None, [
            Finding("Blocker", label, "missing-file", "required file is missing")
        ]
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), []
    except tomllib.TOMLDecodeError as exc:
        return None, [Finding("Blocker", label, "parse-error", str(exc))]


def _rel_for_audit(root: Path, path: Path) -> str:
    """Render a file path relative to root for audit output."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _dist_name(requirement: str) -> str:
    """Extract the bare distribution name from a PEP 508 requirement string.

    "djhtml", "djhtml>=3.0", "drf-spectacular[sidecar]>=0.27" -> "djhtml" /
    "djhtml" / "drf-spectacular". Used to match dependency entries against canon
    without tripping over version specifiers, extras, or markers.
    """
    return re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0].strip().lower()
