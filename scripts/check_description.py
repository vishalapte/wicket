#!/usr/bin/env python3
"""Report prose that states what this repo is without quoting the description home.

One fact, one home (PRINCIPLES.md P-02). What a repo *is* is declared once, in the
library pyproject's `[project].description`; every other statement of it is a copy.
Some copies are unavoidable — an agent baseline and a shareable brief must read as
prose, and neither can interpolate a TOML value at the moment a human or a model
reads it — but an ungated copy is exactly how one claim becomes five different ones.
racecar's own description drifted this way: five sites each asserted what racecar
was, nothing named which one won, and the oldest went stale describing a repo that
had changed underneath it.

Sites checked, each skipped when absent so the rule stays generic:

  - ``AGENTS.md``/``CLAUDE.md`` — the agent baseline's opening definition (the
                                  primary one; a pointer file states nothing)
  - ``docs/summary/<REPO>.md``  — the racecar-llm-summary brief's Purpose section

A site that can read the home at runtime holds no copy and is deliberately NOT
listed: ``racecar.lib.install`` parses `[project].description` when it renders
the adopter pointer block, so there is nothing there to drift.

The match is a case-insensitive substring over whitespace-collapsed text. Two
variations are legitimate and neither is drift: a description enters prose as a
fragment ("racecar is an opinionated engineering canon for AI-built software: ..."),
so the leading character's case changes; and prose gets wrapped, so the sentence
arrives split across lines. Collapsing whitespace tolerates the wrap without hiding
a rewrite — word order, punctuation and vocabulary are still compared exactly, and
those are what a rewrite changes. Matching raw text instead made reflowing a
paragraph indistinguishable from redefining the repo, which pressured the prose to
stay on one long line to keep a checker happy.

**This checker cannot fail. It reports, and that is all it does.** A mismatch is
drift worth naming, not a defect: nothing is broken, and "This project moves pricing
data nightly" against a description of "Batch ETL for the pricing warehouse" is
prose, not rot. There is deliberately no opt-in to failing, because the knob that
offered one (`[tool.racecar.description].enforce`) put a gate on the wording of
sentences — which is what OWNERSHIP.md leaves to the owner, and which no mechanical
check can grade. A build that goes red over a paraphrase teaches its author to write
for the checker, and prose written for a checker is worse prose. Where a wrong
version breaks a release, a loosely-worded sentence breaks nothing; the report says
so and the owner decides.

Abstains when the repo declares no `[project].description`: a repo with no home has
no copies to hold to it, and a checker that cannot resolve its subject reports that
rather than inventing a verdict.

Usage:
    python3 scripts/check_description.py

Complexity: O(n)
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from check_packaging_rules._root import find_repo_root
from check_packaging_rules._slug import discover_brief

# Precedence-ordered agent-instruction filenames. THE HOME IS
# `scripts/check_docs.py` (`AGENT_DOC_NAMES`); this is a deliberate
# mirror, for the same reason check_docs mirrors rather than imports
# check_packaging's detect_shape — the two sit in different lens directories in
# racecar's own tree, so the cross-lens import would not resolve. Keep in step with
# the home; scripts/tests/test_check_docs.py's
# `test_agent_doc_name_mirrors_agree_with_the_home` fails when they diverge.
AGENT_DOC_NAMES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")


def declared_description(root: Path) -> str | None:
    """Return `[project].description`, or None when the repo declares no home."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None
    description = data.get("project", {}).get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return None


def pyproject_unreadable(root: Path) -> str | None:
    """Return why `pyproject.toml` could not be parsed, or None if it parsed or is absent.

    `declared_description` folds a malformed file into the same `None` it returns for a
    file that simply declares no description -- the right behaviour for a caller that only
    wants the string, wrong for `main()`'s message, which otherwise reports "no
    [project].description; nothing to report on" for a file that failed to parse at all.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return f"pyproject.toml is not valid TOML ({exc})"
    except UnicodeDecodeError as exc:
        return f"pyproject.toml is not valid UTF-8 ({exc})"
    return None


def _collapsed(text: str) -> str:
    """Lowercase with whitespace runs collapsed to single spaces.

    Applied to both sides of the comparison so a wrapped line reads as the sentence it
    is. Only whitespace is normalized: word order, punctuation and vocabulary still
    have to match, and those are what a rewrite changes.
    """
    return " ".join(text.lower().split())


def prose_sites(root: Path) -> list[Path]:
    """Return the prose sites that must quote the home, dropping any that are absent.

    Absence is not a finding. An agent-instruction file is expected in a governed repo
    but is not this checker's to require (`check_required_docs.py` owns that manifest),
    and a repo that publishes no llm-summary brief has no brief to check.

    Only the PRIMARY agent doc is a prose site. A repo may hold a second one that
    merely points at the first — racecar's own `CLAUDE.md` does — and a pointer states
    nothing about what the repo is, so demanding the description of it would report
    drift for the crime of being short.
    """
    agent = next(
        (root / name for name in AGENT_DOC_NAMES if (root / name).is_file()), None
    )
    candidates = (
        agent,
        discover_brief(root),
    )
    return [path for path in candidates if path is not None and path.is_file()]


def problems(root: Path) -> list[str]:
    """Return one message per prose site that fails to quote the description home."""
    description = declared_description(root)
    if description is None:
        return []
    needle = _collapsed(description)
    found = []
    for site in prose_sites(root):
        if needle not in _collapsed(site.read_text(encoding="utf-8")):
            found.append(
                f"{site.relative_to(root)} states what this repo is without quoting "
                f"[project].description ({description!r}). Prose may wrap the "
                f"description, but a paraphrase is a second home for one fact and the "
                f"two drift apart."
            )
    return found


def main() -> int:
    """CLI entry. Always 0 — this checker reports and never gates."""
    root = find_repo_root()
    unreadable = pyproject_unreadable(root)
    if unreadable is not None:
        print(f"check_description: info: {unreadable}; nothing to report on")
        return 0
    if declared_description(root) is None:
        print("check_description: info: no [project].description; nothing to report on")
        return 0
    found = problems(root)
    if not found:
        print("check_description: OK")
        return 0
    for message in found:
        print(f"check_description: warning: {message}")
    print(
        f"check_description: {len(found)} warning(s). Reword the prose, reword the "
        "description home, or leave it — the wording is the owner's call, so this "
        "never fails a build."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
