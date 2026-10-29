"""The `commit` verb: the one way racecar makes a commit.

Part of `lib.package`; `scripts/package.py` parses the command line and calls `main`, and
`racecar.package.api.commit` returns what `run` returns.

`--plan plan.json`, or `-m` for one commit of what you staged, renders a runbook that tries
every commit in a throwaway clone and lands only if every hook and the gate pass. The
runbook assigns a version through the delivered `bump_version.py`. `run` renders it and
never runs it, so the api cannot commit. `main` writes it and runs it, and whoever runs that
command authorizes the commit: the owner at a terminal, or `/racecar-commit` behind the
owner's permission prompt (OWNERSHIP.md). `--dry-run` runs the whole trial and lands nothing.

The repo facts a runbook guards on (HEAD, the branch, the root) are read here; a plan that
names them is refused, so a stale copy cannot get in.

Exit: the runbook's own code; 2 for a plan or staged set the runbook cannot be built from.

Complexity: O(1) plus the runbook's run
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from lib import not_a_command
from lib.package._error import UNMET, PackageError
from lib.package._runbook import Plan, render
from lib.package.renderer import text

#: What the verb reads from the repo, and a plan may therefore not state.
_FACTS = ("head_sha", "branch", "cwd")


def _git(root: Path, *args: str) -> list[str]:
    """`git -C root <args>`'s stdout lines; refused with its stderr when git fails."""
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode:
        raise PackageError(f"commit: git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.splitlines()


def _staged(root: Path, message: str) -> dict[str, Any]:
    """A one-commit plan: `message` over the staged paths, removals included.

    Refuses a staged path that also has unstaged edits, because the runbook commits a
    path's working-tree content, not its index.
    """
    files = _git(root, "diff", "--cached", "--name-only", "--no-renames")
    if not files:
        raise PackageError(
            "commit: nothing staged; stage this commit's files or pass --plan"
        )
    mixed = _git(root, "diff", "--name-only", "--no-renames", "--", *files)
    if mixed:
        raise PackageError(
            "commit: staged paths also have unstaged edits, and the runbook commits the "
            f"working tree; stage or drop them first: {', '.join(mixed)}"
        )
    return {"commits": [{"message": message, "files": files}]}


def runbook(root: Path, plan: dict[str, Any]) -> dict[str, object]:
    """Render `plan`'s runbook against the repo at `root`: `{label, branch, head_sha,
    commits, runbook}`. Reads HEAD and the branch itself; never runs anything."""
    named = [fact for fact in _FACTS if fact in plan]
    if named:
        raise PackageError(
            f"commit: the plan names {', '.join(named)}; the verb reads them from the repo"
        )
    branch = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    if not branch:
        raise PackageError("commit: HEAD is detached; check out the branch to land on")
    facts = {
        "head_sha": _git(root, "rev-parse", "HEAD")[0],
        "branch": branch[0],
        "cwd": str(root),
        "label": plan.get("label") or re.sub(r"[^A-Za-z0-9._-]+", "-", branch[0]),
    }
    try:
        built = Plan.from_dict({**plan, **facts})
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError(f"commit: the plan does not read: {exc!r}") from exc
    return {**facts, "commits": len(built.commits), "runbook": render(built)}


def run(
    root: Path, *, plan: dict[str, Any] | None = None, message: str | None = None
) -> dict[str, object]:
    """The runbook record for `plan`, or for `message` over the staged paths.

    Prints nothing and runs nothing. Raises `PackageError` when the runbook cannot be built.
    """
    return runbook(root, plan if plan is not None else _staged(root, message or ""))


def _execute(root: Path, script: str, *, dry_run: bool, no_edit: bool) -> int:
    """Write the runbook beside the other `rc-commit-*` scripts and run it here."""
    path = Path(tempfile.gettempdir()) / f"rc-commit-{root.name}.sh"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    print(text.runbook_written(path), flush=True)
    flags = [
        flag for flag, on in (("--no-edit", no_edit), ("--dry-run", dry_run)) if on
    ]
    return subprocess.run(["bash", str(path), *flags], cwd=root, check=False).returncode


def main(
    root: Path,
    *,
    plan_file: Path | None = None,
    message: str | None = None,
    dry_run: bool = False,
    no_edit: bool = False,
) -> int:
    """Render the runbook for `plan_file` or `message` and run it here."""
    try:
        plan = (
            json.loads(plan_file.read_text(encoding="utf-8"))
            if plan_file is not None
            else None
        )
        record = run(root, plan=plan, message=message)
    except (OSError, ValueError) as exc:
        print(text.unreadable(exc), file=sys.stderr)
        return UNMET
    except PackageError as exc:
        print(exc, file=sys.stderr)
        return exc.code
    return _execute(root, str(record["runbook"]), dry_run=dry_run, no_edit=no_edit)


if __name__ == "__main__":
    not_a_command()
