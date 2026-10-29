"""The package noun: the artifact racecar builds, and the rail a commit to it goes through.

`scripts/package.py` is one command line over this and `racecar.package` is another, so
both routes run the same verbs and print through the same `renderer.text`.

One verb, one module, each with a `run` that returns the verb's record and a `main` that
`scripts/package.py` dispatches to:

  `_commit`   render a commit's runbook; `main` runs it
  `_audit`    check the commits already made
  `_create`   refuses on this route: its scaffolders and templates are racecar's, not
              delivered, and `python -m racecar.package create` runs them

and the helpers beneath them, each named for its job:

  `_commit_history`  audit the commit log against COMMITS.md, which no hook can see
  `_runbook`         render the runbook a commit skill hands the owner
  `_error`           the exit codes, and the one refusal a verb raises

Complexity: O(1) -- re-exports only
"""

from __future__ import annotations

from lib.package._commit_history import audit_history, resolve_revs
from lib.package._error import FINDINGS, OK, UNMET, PackageError
from lib.package._runbook import Bump, Commit, Plan, render

__all__ = [
    "FINDINGS",
    "OK",
    "UNMET",
    "Bump",
    "Commit",
    "PackageError",
    "Plan",
    "audit_history",
    "render",
    "resolve_revs",
]
