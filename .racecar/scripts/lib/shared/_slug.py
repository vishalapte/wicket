"""Where a repo's llm-summary brief lives — resolved from what the repo STATES.

`llm-summary/SPEC.md` §"Location" puts the bundle at `docs/summary/$REPO.md`. The directory
basename is a property of the checkout and moves under a git worktree or a clone under
another name, so the slug is resolved against candidates, in order, and the first one that
exists wins:

  1. the directory basename;
  2. `[project].name`, which travels with the repository.

Where neither exists the caller reports both candidates.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._root import project_name

BRIEF_DIR = ("docs", "summary")


def slugify(name: str) -> str:
    """Return `$repo` for a name: lowercased, non-slug characters replaced with `-`."""
    return re.sub(r"[^a-z0-9_-]", "-", name.lower())


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
