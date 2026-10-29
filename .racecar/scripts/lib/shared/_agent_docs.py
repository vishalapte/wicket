"""The filenames that carry agent instructions for the directory they sit in.

`AGENTS.md` is the cross-tool convention and owns the content; `CLAUDE.md` is Claude
Code's own auto-loaded name, and at a repo root holds only the line `@AGENTS.md`. The
order is `(content, import)`, and `check_required_docs.py` reads it by position.

Complexity: O(1)
"""

from __future__ import annotations

AGENT_DOC_NAMES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")
