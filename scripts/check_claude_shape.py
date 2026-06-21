#!/usr/bin/env python3
"""Mechanical check for a repo-root CLAUDE.md against the racecar shape.

Enforces the shape defined in `doc-coherence/README.md`, "CLAUDE.md shape": a
repo-root `CLAUDE.md`, if present, must carry the four canonical `##` sections
(Orientation, Architecture, Conventions, Open work). Section names are the
fixed part; the content beneath them is the repo's own, and richer subsections
are welcome.

Nothing here is repo-specific: the script discovers the repo root at runtime.
A repo with no root CLAUDE.md is silent (exit 0) — presence in subsystems is a
separate concern, owned by check_subsystem_docs.py.

Exit 0 if clean, 1 if a root CLAUDE.md is present but missing a section.

Usage:
    python3 <path-to>/check_claude_shape.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _find_repo_root() -> Path:
    start = Path.cwd()
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return start


REPO_ROOT = _find_repo_root()

H2_RE = re.compile(r"^##\s+(.*?)\s*$")

# A required section is satisfied when some `##` heading, normalized to
# lowercase, starts with the canonical phrase. This fixes the slot while
# tolerating a longer title ("Orientation in 60 seconds" satisfies "orientation").
REQUIRED_SECTIONS = ("orientation", "architecture", "conventions", "open work")


def check_claude(path: Path) -> list[str]:
    headings = [
        m.group(1).strip().lower()
        for m in (H2_RE.match(ln) for ln in path.read_text(encoding="utf-8").splitlines())
        if m
    ]
    missing = []
    for phrase in REQUIRED_SECTIONS:
        if not any(h.startswith(phrase) for h in headings):
            missing.append(phrase)
    return missing


def main() -> int:
    claude = REPO_ROOT / "CLAUDE.md"
    if not claude.is_file():
        print("claude-shape: no root CLAUDE.md — nothing to validate")
        return 0
    rel = claude.relative_to(REPO_ROOT).as_posix()
    missing = check_claude(claude)
    if not missing:
        print("claude-shape: OK")
        return 0
    print(f"claude-shape: {rel} missing {len(missing)} required section(s)")
    for phrase in missing:
        print(f"  {rel}  missing `## {phrase.title()}` section (doc-coherence/README.md, CLAUDE.md shape)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
