#!/usr/bin/env python3
"""Mechanical check: a racecar repo owns the three required repo-root docs.

The required-docs manifest has one home — ``docs-orchestrator/ORCHESTRATION.md``
("Required-docs manifest"). Three files, and every other doc is optional: its absence is
never a finding.

  1. ``README.md`` — exists, non-empty, opens with a YAML frontmatter block carrying a
     ``pnode`` key (the doc-graph root edge; DOC_GRAPH.md).
  2. ``AGENTS.md`` — the agent baseline and resolver, which owns the content: exists,
     non-empty, carries >= 1 ``## `` heading.
  3. ``CLAUDE.md`` — a regular file whose only non-blank line is ``@AGENTS.md``. Claude
     Code auto-reads ``CLAUDE.md``, and the import hands it ``AGENTS.md``'s bytes, once.
     A symlink is refused (Claude Code would load every file ``AGENTS.md`` imports
     twice), and so is any other content (P-02).

Advisory (info, never an error, so a repo that has not opted in stays green):
  - README frontmatter declares no ``content_blind`` policy. The docs
    orchestrator asks once and writes it (CONTENT_BLINDNESS.md, "Declaration").

Output:
  - One line per finding: ``check_required_docs: <severity>: <message>``.
  - Summary: ``check_required_docs: OK`` (exit 0) or
    ``check_required_docs: N errors`` (exit 1). Info notes do not fail.

Usage:
    python3 <path-to>/check_required_docs.py [--root <path>]

Complexity: O(1), three repo-root paths checked, not a treewalk over the repo's files.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from lib.shared import _frontmatter, _markdown
from lib.shared._agent_docs import AGENT_DOC_NAMES
from lib.shared._report import Findings, emit
from lib.shared._root import find_repo_root

# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def frontmatter_keys(text: str) -> set[str] | None:
    """Return the top-level frontmatter key names, or None if there is no block."""
    block = _frontmatter.block(text)
    if block is None:
        return None
    keys: set[str] = set()
    for line in block.splitlines():
        key = re.match(r"^([A-Za-z_][\w-]*):", line)
        if key:
            keys.add(key.group(1))
    return keys


def frontmatter_scalar(text: str, key: str) -> str | None:
    """Return one top-level frontmatter scalar, stripped of quotes; None if absent.

    A one-line regex, not a YAML parse; this module has no yaml dependency.
    """
    block = _frontmatter.block(text)
    if block is None:
        return None
    for line in block.splitlines():
        found = re.match(rf"^{re.escape(key)}:\s*(.+?)\s*$", line)
        if found:
            return found.group(1).strip("\"'")
    return None


# The positions a repo may declare in the deploy chain (docs/lexicon/repo/README.md).
# `command` is absent: the origin supplies the chain and declares no mode, so no checker
# needs to name racecar.
RACECAR_MODES = ("control", "mission")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_readme(repo_root: Path, f: Findings) -> None:
    """Verify the root README exists with frontmatter, and note the CB policy."""
    readme = repo_root / "README.md"
    if not readme.is_file():
        f.error("missing: README.md (the human storefront and doc-graph root)")
        return
    text = readme.read_text(encoding="utf-8")
    if not text.strip():
        f.error("empty: README.md")
        return
    keys = frontmatter_keys(text)
    if keys is None:
        f.error(
            "README.md has no YAML frontmatter block (expected a `pnode` key; "
            "see doc-coherence/DOC_GRAPH.md)"
        )
        return
    if "pnode" not in keys:
        f.error("README.md frontmatter is missing the `pnode` key (DOC_GRAPH.md)")
    if "content_blind" not in keys:
        f.info(
            "README.md declares no `content_blind` policy; the docs orchestrator "
            "asks once and writes it (see docs-orchestrator/CONTENT_BLINDNESS.md)"
        )
    _check_racecar_mode(text, keys, f)


def _check_racecar_mode(text: str, keys: set[str], f: Findings) -> None:
    """Validate `racecar_mode` IF PRESENT. Absence is legal and says nothing.

    The repo that supplies the chain has no position to declare, so absence is legal. A
    present value must name a real position: `racecar_mode: mision` is an error.
    """
    if "racecar_mode" not in keys:
        return
    mode = frontmatter_scalar(text, "racecar_mode")
    if mode not in RACECAR_MODES:
        f.error(
            f"README.md declares `racecar_mode: {mode}`, which is not one of "
            f"{' | '.join(RACECAR_MODES)} (docs/lexicon/repo/README.md). The key is "
            "optional, but a present one must name a real position in the chain."
        )


#: The one line `CLAUDE.md` holds.
IMPORT_LINE = f"@{AGENT_DOC_NAMES[0]}"


def check_agent_doc(repo_root: Path, f: Findings) -> None:
    """Verify `AGENTS.md` owns the content and `CLAUDE.md` is the one-line import of it."""
    primary, pointer = AGENT_DOC_NAMES
    content = repo_root / primary
    if not content.is_file():
        f.error(
            f"missing: {primary} (the agent baseline and resolver, which owns the content)"
        )
    else:
        text = content.read_text(encoding="utf-8")
        if not text.strip():
            f.error(f"empty: {primary}")
        elif not _markdown.parse(text).headings(2):
            f.error(f"no H2 heading: {primary}")
    link = repo_root / pointer
    if link.is_symlink():
        f.error(
            f"{pointer} is a symlink; make it a regular file holding the one line "
            f"`{IMPORT_LINE}`. Claude Code loads the files {primary} imports twice "
            "through a symlink, and once through the import."
        )
    elif not link.is_file():
        f.error(f"missing: {pointer} (a file holding the one line `{IMPORT_LINE}`)")
    else:
        lines = [x.strip() for x in link.read_text(encoding="utf-8").splitlines()]
        if [x for x in lines if x] != [IMPORT_LINE]:
            f.error(
                f"{pointer} must hold only the line `{IMPORT_LINE}`; anything else is a "
                f"second home for what {primary} says"
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the required-docs check."""
    parser = argparse.ArgumentParser(
        description="Check the repo root owns README.md, AGENTS.md and CLAUDE.md."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root to scan. Default: discovered via .git walk-up from CWD.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Verify the repo-root doc spine; return an exit code."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    repo_root = args.root.resolve() if args.root else find_repo_root()

    f = Findings()
    check_readme(repo_root, f)
    check_agent_doc(repo_root, f)
    return emit(f, "check_required_docs")


if __name__ == "__main__":
    sys.exit(main())
