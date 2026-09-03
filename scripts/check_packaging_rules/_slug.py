"""Where a repo's llm-summary brief lives — resolved from what the repo STATES.

`llm-summary/SPEC.md` §"Location" puts the bundle at `docs/summary/$REPO.md`, and
`$REPO` was the uppercased directory basename. A basename is not something the repo
states: it is a property of the checkout, and two ordinary situations move it while
the repository is unchanged.

  - A **git worktree**. `git worktree add .claude/worktrees/issue-23` produces a
    checkout of the same repo in a directory named after the branch, and every
    brief-aware checker then looks for `docs/summary/ISSUE-23.md`. That is what
    made `racecar-issue`'s own Definition of Done unreachable: the skill mandates a
    worktree and requires `make check-full` green, and `check_brief` is the last
    step of `make docs`.
  - A **clone under another name** — `git clone … racecar-fork`, a vendored copy, a
    CI checkout at `/src`.

So the slug is resolved against candidates, in order, and the FIRST ONE THAT EXISTS
wins:

  1. the directory basename — today's answer, kept first so no repo that resolves
     today resolves differently tomorrow;
  2. `[project].name` — the one home for what the package is called, which travels
     with the repository rather than with the checkout.

Order by existence rather than by preference because the two disagree legitimately: a
repo directory named `foo-standards` publishing a package named `foo` has its brief at
whichever of the two it wrote, and racecar does not get to rename an adopter's file by
changing its mind about derivation. Where neither exists there is nothing to read, and
the caller reports both candidates so the fix is one `mv` rather than a guess.

Shared, so the rule has one home: `check_brief.py` (the gate) and
`check_description.py` (which reads the brief as a prose site) both resolve through
this, and a third reader would too.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

BRIEF_DIR = ("docs", "summary")


def slugify(name: str) -> str:
    """Return `$repo` for a name: lowercased, non-slug characters replaced with `-`."""
    return re.sub(r"[^a-z0-9_-]", "-", name.lower())


def project_name(root: Path) -> str | None:
    """Return `[project].name`, or None when the repo declares none.

    Tolerant of an unparseable or absent pyproject: this resolves a filename, and a
    malformed manifest is a finding for the packaging checker, not a reason for the
    brief checker to die before it reports anything.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None
    name = data.get("project", {}).get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def brief_slugs(root: Path) -> tuple[str, ...]:
    """Return the candidate `$REPO` slugs for `root`, in resolution order."""
    names = [root.name]
    declared = project_name(root)
    if declared is not None:
        names.append(declared)
    slugs: dict[str, None] = {slugify(name).upper(): None for name in names}
    return tuple(slugs)


def brief_candidates(root: Path) -> tuple[Path, ...]:
    """Return the paths `docs/summary/$REPO.md` could take, in resolution order."""
    return tuple(root.joinpath(*BRIEF_DIR, f"{slug}.md") for slug in brief_slugs(root))


def discover_brief(root: Path) -> Path | None:
    """Return the repo's brief, or None when no candidate exists."""
    return next((path for path in brief_candidates(root) if path.is_file()), None)
