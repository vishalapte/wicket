""".pre-commit-config.yaml validation."""

from __future__ import annotations

import re
from pathlib import Path

from ._constants import REQUIRED_PRECOMMIT_HOOKS
from ._findings import Finding

_PRECOMMIT_ID_RE = re.compile(r"^\s*-\s*id\s*:\s*([\w-]+)\s*$", re.MULTILINE)


def check_precommit(root: Path) -> list[Finding]:
    """Require .pre-commit-config.yaml and every canon hook id within it."""
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
    text = path.read_text(encoding="utf-8")
    found = set(_PRECOMMIT_ID_RE.findall(text))
    findings: list[Finding] = []
    missing = REQUIRED_PRECOMMIT_HOOKS - found
    for hook in sorted(missing):
        findings.append(
            Finding(
                "Blocker",
                ".pre-commit-config.yaml",
                f"missing-hook:{hook}",
                "required hook is not configured",
            )
        )
    return findings
