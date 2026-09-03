"""Repo opt-in: the agent-instruction file declares racecar."""

from __future__ import annotations

from pathlib import Path

from ._findings import Finding

# Root-level files that, by convention, carry a repo's agent instructions; the
# racecar opt-in lives in one of them. Precedence-ordered mirror of
# `scripts/check_docs.py`'s `AGENT_DOC_NAMES`, which is THE HOME — the
# cross-lens import does not resolve (check_docs records the same constraint for
# check_packaging's detect_shape). Order does not change this check's verdict, which
# passes when ANY present file names racecar; it is kept aligned so the two lists read
# as the one fact they are. scripts/tests/test_check_docs.py's
# `test_agent_doc_name_mirrors_agree_with_the_home` fails on divergence.
AGENT_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")


def check_optin(root: Path) -> list[Finding]:
    """Advise that an existing agent-instruction file declares racecar.

    A repo with an AGENTS.md (or CLAUDE.md) is writing instructions for the agent;
    if it applies racecar, that file should say so, or a clone without the
    author's global ~/.claude block sees nothing tying it to racecar. The check
    fires only on a file that exists but never names racecar — racecar does not
    scaffold a per-repo CLAUDE.md and does not demand one (a repo with no agent
    file is the owner's choice), so an absent file is silent, not a finding. This
    is a presence check on the declaration, never a path check: racecar is located
    by each developer's own install, not a hard-coded path. Advisory (Finding)."""
    present = [name for name in AGENT_INSTRUCTION_FILES if (root / name).is_file()]
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
