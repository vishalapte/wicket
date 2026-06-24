""".gitignore validation."""

from __future__ import annotations

import re
from pathlib import Path

from ._findings import Finding


def check_gitignore(root: Path) -> list[Finding]:
    """Require .gitignore and the canon ignore entries it must carry."""
    path = root / ".gitignore"
    if not path.exists():
        return [
            Finding("Blocker", ".gitignore", "missing-file", ".gitignore is required")
        ]
    text = path.read_text(encoding="utf-8")
    findings: list[Finding] = []
    if not re.search(r"^\.venv/?\s*$", text, re.MULTILINE):
        findings.append(
            Finding(
                "Blocker",
                ".gitignore",
                "missing-venv-rule",
                ".venv/ must be gitignored (PACKAGING.md §4)",
            )
        )
    if not re.search(r"^__pycache__/?\s*$", text, re.MULTILINE):
        findings.append(
            Finding(
                "Finding",
                ".gitignore",
                "missing-pycache-rule",
                "__pycache__/ should be gitignored",
            )
        )
    return findings
