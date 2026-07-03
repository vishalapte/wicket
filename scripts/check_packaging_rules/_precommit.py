""".pre-commit-config.yaml validation."""

from __future__ import annotations

import re
from pathlib import Path

from ._constants import RETIRED_MAKE_VARS, REQUIRED_PRECOMMIT_HOOKS
from ._findings import Finding

_PRECOMMIT_ID_RE = re.compile(r"^\s*-\s*id\s*:\s*([\w-]+)\s*$", re.MULTILINE)


def check_precommit(root: Path) -> list[Finding]:
    """Require .pre-commit-config.yaml, every canon hook id, and no stale make-var references.

    The file is repo-owned (delivered create-if-missing, not content-synced), so a racecar
    rename can leave a stale hook body behind. Beyond hook presence, flag any reference to a
    retired make variable -- the staleness that a hook-id-only check misses."""
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
    for old, new in sorted(RETIRED_MAKE_VARS.items()):
        if re.search(rf"\b{re.escape(old)}\b", text):
            findings.append(
                Finding(
                    "Blocker",
                    ".pre-commit-config.yaml",
                    f"stale-make-var:{old}",
                    f"references the retired make variable {old}; use {new} "
                    "(re-copy the hook from templates/classic/pre-commit-config.yaml)",
                )
            )
    return findings
