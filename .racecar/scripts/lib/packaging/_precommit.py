""".pre-commit-config.yaml validation."""

from __future__ import annotations

import re
from pathlib import Path

from ..shared._constants import (
    PACKAGE_ONLY_PRECOMMIT_HOOKS,
    REQUIRED_PRECOMMIT_HOOKS,
    RETIRED_MAKE_VARS,
)
from ._findings import Finding
from ._shape import Shape

_PRECOMMIT_ID_RE = re.compile(r"^\s*-\s*id\s*:\s*([\w-]+)\s*$", re.MULTILINE)


def check_precommit(root: Path, shape: Shape | None = None) -> list[Finding]:
    """Require .pre-commit-config.yaml, every canon hook id, and no stale make-var references.

    The file is repo-owned (created if missing, never synced), so it can carry a retired
    make variable; that is flagged too.

    For the `django` shape the package-only hooks (import-linter, validate-pyproject,
    no-upward-imports) are not required."""
    path = root / ".pre-commit-config.yaml"
    if not path.exists():
        return [
            Finding(
                "Blocker",
                ".pre-commit-config.yaml",
                "missing-file",
                "required; copy from templates/classic/pre-commit-config.yaml",
            )
        ]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [
            Finding("Blocker", ".pre-commit-config.yaml", "encoding-error", str(exc))
        ]
    found = set(_PRECOMMIT_ID_RE.findall(text))
    findings: list[Finding] = []
    required = REQUIRED_PRECOMMIT_HOOKS
    if shape is not None and shape.name == "django":
        required = required - PACKAGE_ONLY_PRECOMMIT_HOOKS
    missing = required - found
    for hook in sorted(missing):
        findings.append(
            Finding(
                "Blocker",
                ".pre-commit-config.yaml",
                f"missing-hook:{hook}",
                "required hook is not configured",
            )
        )
    for old, new in sorted(RETIRED_MAKE_VARS.items()):
        # A make reference, not the bare word: `PYTHON` also names `arch-python/PYTHON.md`.
        if re.search(rf"print-{re.escape(old)}\b|\$[({{]{re.escape(old)}[)}}]", text):
            findings.append(
                Finding(
                    "Blocker",
                    ".pre-commit-config.yaml",
                    f"stale-make-var:{old}",
                    f"references the retired make variable {old}; "
                    + (f"use {new} " if new else "racecar.mk derives it now ")
                    + "(re-copy the hook from templates/classic/pre-commit-config.yaml)",
                )
            )
    return findings
