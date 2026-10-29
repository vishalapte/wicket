#!/usr/bin/env python3
"""Report prose that states what this repo is without quoting the description home.

What a repo is is declared once, in the library pyproject's `[project].description` (P-02).
This checker reports each prose site that does not quote it.

Sites checked, each skipped when absent:

  - ``AGENTS.md``/``CLAUDE.md`` — the agent baseline's opening definition (the
                                  primary one; a pointer file states nothing)
  - ``docs/summary/<REPO>.md``  — the racecar-llm-summary brief's Purpose section

The match is a case-insensitive substring over whitespace-collapsed text, so a fragment
with a changed leading case, or a sentence wrapped across lines, still matches. Word order,
punctuation and vocabulary are compared exactly.

This checker never fails: it reports, and the owner decides (OWNERSHIP.md).

Abstains when the repo declares no `[project].description`.

Usage:
    python3 scripts/check_description.py

Complexity: O(n)
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from lib.shared._agent_docs import AGENT_DOC_NAMES
from lib.shared._root import find_repo_root
from lib.shared._slug import discover_brief


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

    `declared_description` returns None for both a malformed file and a missing description;
    this lets `main()` tell them apart.
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

    Applied to both sides of the comparison so a wrapped line reads as the sentence it is.
    """
    return " ".join(text.lower().split())


def prose_sites(root: Path) -> list[Path]:
    """Return the prose sites that must quote the home, dropping any that are absent.

    Absence is not a finding: `check_required_docs.py` owns the required-docs manifest.
    Only the primary agent doc is a prose site; a pointer file such as racecar's own
    `CLAUDE.md` states nothing about the repo.
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


def findings(root: Path) -> list[dict[str, str]]:
    """Everything this check has to say about `root`. Prints nothing.

    Keys are `level` — `warning` for a prose site that does not quote the description
    home, `info` for a repo there is nothing to report on — and `message`. An empty
    list means every prose site quotes it.
    """
    unreadable = pyproject_unreadable(root)
    if unreadable is not None:
        return [{"level": "info", "message": f"{unreadable}; nothing to report on"}]
    if declared_description(root) is None:
        return [
            {
                "level": "info",
                "message": "no [project].description; nothing to report on",
            }
        ]
    return [{"level": "warning", "message": m} for m in problems(root)]


def render(records: list[dict[str, str]]) -> str:
    """The report `main` prints, built from `findings`."""
    lines = [f"check_description: {r['level']}: {r['message']}" for r in records]
    warnings = sum(1 for r in records if r["level"] == "warning")
    if warnings:
        lines.append(
            f"check_description: {warnings} warning(s). Reword the prose, reword the "
            "description home, or leave it — the wording is the owner's call, so this "
            "never fails a build."
        )
    return "\n".join(lines)


def failed(records: list[dict[str, str]]) -> int:
    """Always 0: this check never fails a build.

    It takes the records it ignores so every checker answers this question the same way.
    """
    del records
    return 0


def main() -> int:
    """CLI entry. Always 0."""
    records = findings(find_repo_root())
    if not records:
        print("check_description: OK")
        return 0
    print(render(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
