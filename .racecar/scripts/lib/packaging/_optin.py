"""Repo opt-in: the agent-instruction file declares racecar."""

from __future__ import annotations

from pathlib import Path

from ..shared._agent_docs import AGENT_DOC_NAMES
from ._findings import Finding


def check_optin(root: Path) -> list[Finding]:
    """Advise that an existing agent-instruction file declares racecar.

    Fires only on an AGENTS.md (or CLAUDE.md) that exists and never names racecar; an
    absent file is `check_required_docs.py`'s finding. Checks the name, not a path.
    A Finding."""
    present = [name for name in AGENT_DOC_NAMES if (root / name).is_file()]
    if not present:
        return []
    for name in present:
        text = (root / name).read_text(encoding="utf-8", errors="replace")
        if "racecar" in text.lower():
            return []
    return [
        Finding(
            "Finding",
            present[0],
            "missing-racecar-optin",
            f"{present[0]} does not reference racecar — repo not portably opted in",
        )
    ]
