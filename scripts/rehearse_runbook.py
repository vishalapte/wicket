#!/usr/bin/env python3
"""Rehearse an emitted runbook: run every step that does not write, once, here.

racecar's writing skills do not commit. They hand the owner a script -- one guarded,
idempotent file at `/tmp/rc-*.sh` -- and until this existed, two things graded that script
and between them they left the gap where it actually breaks.

`check_runbook.py` READS it: no `subprocess`, no `exec`, four structural rules. Its own
docstring says what that cannot reach -- `if true; then` sails through. `commit_preflight.sh`
EXECUTES, but its subject is a drafted message and the staged set, so it never sees the
runbook's own guards, its `--check` probes, or its environment. The first time an emitted
runbook's steps ran in the runbook's own environment was therefore when the owner ran it,
and four failed exactly there in one session -- every one of them semantic rather than
structural, invisible to a reader by construction because what was wrong was what the line
DID, not how it was shaped. The minimal case is an environment variable scoped to one
command that two commands need: it grades clean under all four rules and dies on line 8.

This runs it. The rule is the class, not those four: a runbook racecar hands over has had
its non-writing steps executed once, in its own environment, by the tool.

    python3 scripts/rehearse_runbook.py /tmp/rc-issue-racecar-36.sh
    python3 scripts/rehearse_runbook.py --dry-run /tmp/rc-commit-racecar.sh   # render only

THE SAFETY GUARANTEE, in three layers, because a rehearsal that could commit is worse than
no rehearsal at all:

  1. STATIC.  The writing steps never enter the rehearsal script. `git commit`, `push`,
     `merge`, `rebase`, `cherry-pick`, `revert`, `am`, `tag`, `update-ref`, `fetch`,
     `pull`, `gh <thing> create|merge|close|edit|delete`, and every apply step
     `check_runbook.mutates` names -- an in-place `sed` or `perl`, a here-doc that writes,
     `renumber_bump.py` without `--check`, a redirect into the tree -- are replaced, in
     place, by a shell no-op. In place, not deleted: the apply step routinely sits on a
     `case` pattern label (`0.85.0) sed -i ... ;;`), and deleting the line deletes the
     label with it, so the rehearsal would die of its own syntax error rather than the
     runbook's. Line numbers are preserved exactly, which is what lets a failure be
     reported against the runbook's own line.
  2. SHIM.  `git`, `gh`, `sed` and `perl` resolve through a temporary directory placed
     first on `PATH`, whose wrappers refuse the same writing invocations and forward
     everything else to the real tool. This catches what layer 1 cannot see -- a command
     built in a subshell, an `eval`, a `xargs`. A shim firing is REPORTED, because it
     means the static classifier has a gap. The technique has a name: PATH interposition
     (Bundler's binstubs and `direnv` do the same). Each wrapper is a protection proxy
     over the real binary -- refuse on target, forward otherwise -- never a
     reimplementation of git, gh, sed or perl.

     The GIT wrapper refuses by TARGET, not by verb. It resolves what the invocation
     would write to (`-C`, `--git-dir`, else the cwd) and withholds only when that is the
     repository being rehearsed. Keyed on the verb alone it refused every `git commit`
     from every descendant process, which made the last step of every runbook racecar
     emits -- `make check-full` -- unrehearsable: four of its suites build throwaway repos
     and commit into them, because committing is what they are testing. Fail-closed at
     both ends: an unresolvable target, or no guarded root configured, still withholds.
  3. VERIFICATION.  `HEAD`, the index tree and `git status --porcelain` are captured
     before the run. The index is restored exactly afterwards (`git read-tree`), because
     `git add` IS rehearsed -- the preflight below it reads the staged set, so refusing to
     stage would grade a different runbook than the owner will run. Any move of `HEAD` or
     of the working tree is reported as a breach.

     Layer 3 is Tripwire's own shape (Kim & Spafford, 1994): snapshot state, let
     something run, diff to detect unauthorized change. The three layers together are a
     prevent/detect split -- 1 and 2 prevent, 3 detects what neither could.

WHAT THIS DOES NOT PROVE, said out loud because a guard that quietly stops guarding is
worse than one that was never there:

  * The writing steps work. Not running them is the point. `git commit` can still fail on
    a hook the dry run did not model, and `gh pr create` on a permission.
  * Any step whose success DEPENDS on an apply having happened. The rehearsal continues as
    though the apply were already skipped, so a `grep` that verifies the bump landed will
    report a failure the owner's run will not have. Read such a failure against the
    withheld list this prints.
  * A write performed by something the shim cannot see: an absolute path (`/usr/bin/git`),
    a Python or Node script that writes, a program invoked through `env`. Layer 3 detects
    those after the fact; it does not prevent them.
  * A write to ANOTHER repository. That is deliberate -- the rehearsal protects one repo
    and has no standing over the rest of the disk -- but it means a runbook that writes to
    a second checkout is rehearsed as though that write were free. Layer 3 watches the
    guarded repo only.
  * `gh`, `sed` and `perl` are still refused wherever they run, because neither has a
    repository to resolve: `gh` addresses a forge rather than a checkout, and scoping it
    by target would mean a network round trip to find out. They stay fail-closed, so a
    runbook step that shells out to an in-place `sed` on a scratch file outside the repo
    is still withheld.
  * A runbook without `set -e` does not stop at its first failure, so the line reported is
    the last one that failed rather than the first. `check_runbook.py` already refuses that
    shape -- grade before rehearsing.

Exit codes: 0 every step ran, 1 a step failed, 2 unmet (no such file, not a git repo, the
rehearsal itself could not run).

Complexity: O(n), n = script lines/steps
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position
import check_runbook  # noqa: E402

# pylint: enable=wrong-import-position

NAME = "rehearse_runbook"

# The verbs that write history. The apply steps come from `check_runbook.mutates`, which is
# already the one home for "what does this line rewrite in the tree" -- a second list here
# would be a second answer to `sed -i`.
_GIT_WRITES = re.compile(
    r"\bgit\s+(?:-\S+\s+)*"
    r"(commit|push|merge|rebase|cherry-pick|revert|am|tag|update-ref|"
    r"filter-branch|fetch|pull|gc|prune|clean)\b"
)
_GH_WRITES = re.compile(
    r"\bgh\s+(?:-\S+\s+)*\S+\s+(create|merge|close|edit|delete|comment|reopen|ready)\b"
)

# A `case` pattern label sitting in front of the command on the same line. Preserved when
# the command is withheld, or `esac` loses the branch it names. The class excludes `(` so
# `current=$(grep ...)` is not read as a label.
_CASE_LABEL = re.compile(r"^(\s*\(?[^()\s;|&]+(?:\s*\|\s*[^()\s;|&]+)*\)\s+)")
# The terminator of a `case` branch, likewise preserved.
_BRANCH_END = re.compile(r"(\s*;;&?|\s*;&)\s*$")

# Written by the prologue's xtrace and ERR trap onto fd 9, and read back to name the step
# that failed. Two mechanisms because a runbook may install its own ERR trap (the merge
# runbook does, to print a recovery block), and xtrace survives that.
_STEP = re.compile(r"^@@STEP (\d+) ")
_FAIL = re.compile(r"^@@FAIL (\d+) (\d+) (.*)$")

_PROLOGUE = (
    "# --- racecar rehearsal prologue (generated; not the runbook) ---",
    'exec 9>>"$RC_REHEARSE_TRACE"',
    "BASH_XTRACEFD=9",
    "PS4='@@STEP ${LINENO} '",
    "set -E",
    "trap 'echo \"@@FAIL ${LINENO} $? ${BASH_COMMAND}\" >&9' ERR",
    "set -x",
    "# --- end prologue; the runbook follows, line for line ---",
)
OFFSET = len(_PROLOGUE)

_SHIM_HEAD = """#!/usr/bin/env bash
# racecar rehearsal shim -- generated. Refuses the writing invocations of one tool and
# forwards everything else. Present so a writer built in a subshell, which the static pass
# cannot read, still cannot write.
_withhold() {
    printf '%s\\n' "$1" >>"${RC_REHEARSE_LOG:-/dev/null}"
    echo "rehearse: withheld \\`$1\\`" >&2
    exit 0
}
"""

# `-C` and `--git-dir` are captured rather than skipped, because they name the repository
# the invocation would write to, and that is the question this shim has to answer. Keyed on
# the verb alone it refused every `git commit` from every descendant process -- including
# commits into repositories the rehearsal is not protecting and has never heard of. That is
# not a corner case: the last step of every runbook racecar emits is `make check-full`, and
# four of its suites build throwaway repos under tmp_path and commit into them, because
# committing is what they are testing. The gate step could not be rehearsed at all.
_GIT_SHIM = _SHIM_HEAD + """verb=""
cdir=""
gdir=""
skip=0
want=""
for arg in "$@"; do
    if [ -n "$want" ]; then
        if [ "$want" = C ]; then cdir="$arg"; else gdir="$arg"; fi
        want=""
        continue
    fi
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    case "$arg" in
        -C) want=C ;;
        --git-dir) want=G ;;
        --git-dir=*) gdir="${arg#--git-dir=}" ;;
        -c|--work-tree|--namespace|--exec-path|--super-prefix) skip=1 ;;
        -*) ;;
        *) verb="$arg"; break ;;
    esac
done

# True when this invocation would write to the ONE repository being rehearsed.
#
# Fail-closed at both ends: no guarded root configured, or a target that cannot be
# resolved, both answer yes. Refusing something harmless costs a forwarded command; the
# other error writes to the repo the whole tool exists to protect.
_targets_guarded() {
    local top
    [ -n "${RC_REHEARSE_GUARDED:-}" ] || return 0
    if [ -n "$gdir" ]; then
        top=$("@REAL@" --git-dir="$gdir" rev-parse --show-toplevel 2>/dev/null)
    elif [ -n "$cdir" ]; then
        top=$("@REAL@" -C "$cdir" rev-parse --show-toplevel 2>/dev/null)
    else
        top=$("@REAL@" rev-parse --show-toplevel 2>/dev/null)
    fi
    [ -n "$top" ] || return 0
    [ "$top" = "$RC_REHEARSE_GUARDED" ]
}

case "$verb" in
    commit|push|merge|rebase|cherry-pick|revert|am|tag|update-ref|filter-branch|fetch|pull|gc|prune|clean)
        _targets_guarded && _withhold "git $verb" ;;
    reset)
        for arg in "$@"; do
            if [ "$arg" = "--hard" ]; then
                _targets_guarded && _withhold "git reset --hard"
            fi
        done ;;
esac
exec "@REAL@" "$@"
"""

# `gh` is refused by default and allowed by exception: a runbook's only use of it is
# `gh pr create`, so a permissive default buys nothing and risks everything.
_GH_SHIM = _SHIM_HEAD + """words=()
for arg in "$@"; do
    case "$arg" in -*) ;; *) words+=("$arg") ;; esac
done
sub="${words[0]:-}"
act="${words[1]:-}"
case "$sub" in
    version|help|auth|status) exec "@REAL@" "$@" ;;
esac
case "$act" in
    view|list|status|diff|checks) exec "@REAL@" "$@" ;;
esac
_withhold "gh $sub $act"
"""

_INPLACE_SHIM = _SHIM_HEAD + """for arg in "$@"; do
    case "$arg" in
        --in-place*) _withhold "@TOOL@ --in-place" ;;
        --*) ;;
        -*i*) _withhold "@TOOL@ -i (in place)" ;;
    esac
done
exec "@REAL@" "$@"
"""


class Step(NamedTuple):
    """One command the rehearsal refused to run, and why."""

    line: int
    code: str
    reason: str


class Outcome(NamedTuple):
    """What the rehearsal learned, and what it had to say about its own safety."""

    ok: bool
    exit_code: int
    failed_line: int | None
    failed_code: str
    withheld: list[Step]
    refused: list[str]  # writers the shim caught that the static pass missed
    moved: list[str]  # ways the repo moved despite all three layers
    stdout: str
    stderr: str


def writes_history(code: str) -> str | None:
    """What this line writes to history, or None when it writes nothing.

    Read against `check_runbook.bare`, so a command quoted inside a progress message --
    `echo "now run git push"` -- is prose rather than a push. That deliberate blindness is
    what the PATH shim exists to cover.
    """
    text = check_runbook.bare(code)
    found = _GIT_WRITES.search(text)
    if found:
        return f"git {found.group(1)}"
    found = _GH_WRITES.search(text)
    if found:
        return f"gh {found.group(1)}"
    return None


def withhold(
    line: check_runbook.Line, temp_vars: frozenset[str] = frozenset()
) -> str | None:
    """Why this command must not run, or None when it may."""
    return writes_history(line.code) or check_runbook.mutates(line, temp_vars)


def render(text: str) -> tuple[str, list[Step], int]:
    """The script the rehearsal will run, what it withheld, and the line offset.

    Every withheld command is replaced by `:` on its own physical lines, keeping the count
    exact so a trace line maps back to the runbook by subtracting the offset.
    """
    physical = text.splitlines()
    steps: list[Step] = []
    parsed = check_runbook.parse(text)
    temp_vars = check_runbook.temp_variables(parsed)
    for line in parsed:
        reason = withhold(line, temp_vars)
        if not reason:
            continue
        steps.append(Step(line.number, line.code, reason))
        _neutralize(physical, line.number, max(line.end, line.number), reason)
    body = "\n".join(list(_PROLOGUE) + physical)
    return body + "\n", steps, OFFSET


def _neutralize(physical: list[str], first: int, last: int, reason: str) -> None:
    """Replace physical lines `first`..`last` with no-ops, in place.

    The `case` label in front and the `;;` behind are kept: they are the branch's
    structure, not the command, and removing them turns a withheld apply into a syntax
    error the reader would have to diagnose before seeing the real finding.
    """
    head = _CASE_LABEL.match(physical[first - 1])
    prefix = head.group(1) if head else ""
    tail = _BRANCH_END.search(physical[last - 1])
    suffix = tail.group(1) if tail else ""
    note = reason.replace("'", "")
    physical[first - 1] = f"{prefix}: 'rehearse: withheld {note}'" + (
        suffix if last == first else ""
    )
    for index in range(first, last):
        physical[index] = ":" + (suffix if index == last - 1 else "")


def _shims(where: Path) -> None:
    """Write the PATH wrappers. A tool that is not installed gets no shim and no shadow."""
    for tool, template in (
        ("git", _GIT_SHIM),
        ("gh", _GH_SHIM),
        ("sed", _INPLACE_SHIM),
        ("perl", _INPLACE_SHIM),
    ):
        real = shutil.which(tool)
        if not real:
            continue
        shim = where / tool
        shim.write_text(
            template.replace("@REAL@", real).replace("@TOOL@", tool), encoding="utf-8"
        )
        shim.chmod(0o755)


def _git(root: Path, *args: str) -> str | None:
    """One git command against `root`, or None when it could not run."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def is_marker(raw: str) -> bool:
    """True for a line the prologue emitted rather than the runbook."""
    return bool(_FAIL.match(raw)) or "@STEP " in raw[:12]


def _where_it_failed(trace: str, stderr: str) -> tuple[int | None, str]:
    """The line and command the runbook stopped on.

    Two sources, because `BASH_XTRACEFD` arrived in bash 4.1 and macOS ships 3.2: where it
    is honoured the whole trace lands on fd 9, and where it is not the `set -x` output goes
    to stderr while the ERR trap's own `>&9` still reaches the file. Reading both is what
    makes the reported line the same on either shell.

    The ERR trap is authoritative and the xtrace is the fallback, because a runbook may
    install an ERR trap of its own and replace ours -- the merge runbook does, to print a
    recovery block.
    """
    for raw in reversed((trace + "\n" + stderr).splitlines()):
        failed = _FAIL.match(raw)
        if failed:
            return int(failed.group(1)), failed.group(3)
    stream = trace if _STEP.search(trace) else stderr
    line: int | None = None
    code = ""
    for raw in stream.splitlines():
        # `@@@STEP` -- PS4 repeated by nesting depth -- is a subshell, whose LINENO is the
        # enclosing line. The unnested entry for that line follows, so skipping it loses
        # nothing and keeps a command substitution from claiming the failure.
        step = _STEP.match(raw)
        if step:
            line, code = int(step.group(1)), raw[step.end() :]
    return line, code


def _run(
    work: Path, script: str, where: Path, timeout: int
) -> tuple[int, str, str, str, list[str]]:
    """Run the rendered script under the shims, returning what came back out.

    Split from `rehearse` so the sandbox it builds -- the shim directory, the trace fd, the
    withheld log, the environment -- is one thing with one lifetime, rather than a dozen
    names loose in the caller.
    """
    (work / "shims").mkdir()
    _shims(work / "shims")
    (work / "run.sh").write_text(script, encoding="utf-8")
    trace, log = work / "trace", work / "withheld"
    trace.touch()
    log.touch()
    env = dict(os.environ)
    env["PATH"] = f"{work / 'shims'}{os.pathsep}{env.get('PATH', '')}"
    env["RC_REHEARSE_TRACE"] = str(trace)
    env["RC_REHEARSE_LOG"] = str(log)
    # The one repository the shims have standing over. Read by `_targets_guarded`.
    env["RC_REHEARSE_GUARDED"] = str(where)
    # Not fidelity, safety: a withheld commit opens no editor, but a step that reached one
    # anyway would hang forever rather than fail, and a credential prompt likewise.
    env["GIT_EDITOR"] = "true"
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        done = subprocess.run(
            ["bash", str(work / "run.sh")],
            cwd=where,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 124, "", f"the rehearsal exceeded {timeout}s"
    return (
        code,
        out,
        err,
        trace.read_text(encoding="utf-8", errors="replace"),
        [entry for entry in log.read_text(encoding="utf-8").splitlines() if entry],
    )


def rehearse(text: str, cwd: Path, timeout: int = 1800) -> Outcome:
    """Run the runbook's non-writing steps in `cwd` and report the first that fails."""
    script, steps, offset = render(text)
    root = _git(cwd, "rev-parse", "--show-toplevel")
    if root is None:
        return Outcome(
            False, 2, None, "", steps, [], [f"{cwd} is not a git repository"], "", ""
        )
    where = Path(root)
    before = (_git(where, "rev-parse", "HEAD"), _git(where, "status", "--porcelain"))
    index = _git(where, "write-tree")

    with tempfile.TemporaryDirectory(prefix="rc-rehearse-") as area:
        code, out, err, raw_trace, refused = _run(Path(area), script, where, timeout)

    if index:
        _git(where, "read-tree", index)
    after = (_git(where, "rev-parse", "HEAD"), _git(where, "status", "--porcelain"))
    moved = []
    if before[0] != after[0]:
        moved.append(f"HEAD moved from {before[0]} to {after[0]}")
    if before[1] != after[1]:
        moved.append("the working tree is not what it was before the rehearsal")

    line, code_at = (None, "") if code == 0 else _where_it_failed(raw_trace, err)
    if line is not None:
        line -= offset
    return Outcome(
        code == 0 and not moved, code, line, code_at, steps, refused, moved, out, err
    )


def _report(path: Path, outcome: Outcome) -> None:
    """Print one runbook's result in the `file:line:` shape every racecar checker uses."""
    for breach in outcome.moved:
        print(f"{NAME}: error: {path}: [repo-moved] {breach}")
    for entry in outcome.refused:
        print(
            f"{NAME}: warning: {path}: [shim-caught] `{entry}` reached the shell and was "
            f"refused there; the static pass did not withhold it"
        )
    if outcome.ok:
        print(f"{NAME}: OK ({path}, {len(outcome.withheld)} writing step(s) withheld)")
        return
    where = f"{path}:{outcome.failed_line}:" if outcome.failed_line else f"{path}:"
    print(
        f"{NAME}: error: {where} the runbook stops here (exit {outcome.exit_code})"
        + (f": {outcome.failed_code}" if outcome.failed_code else "")
    )
    # The withheld list belongs beside the failure, not behind a second command: a step
    # that fails BECAUSE an apply above it was withheld is the one reading a rehearsal
    # gets wrong, and the reader cannot tell without knowing what did not run.
    for step in outcome.withheld:
        print(f"{NAME}:   withheld {path}:{step.line}: {step.reason}")
    for stream, label in ((outcome.stdout, "stdout"), (outcome.stderr, "stderr")):
        # The prologue's own trace is plumbing, and on bash 3.2 it lands here rather than
        # on fd 9. Printing it would bury the runbook's output under its own instrument.
        tail = [
            entry for entry in stream.splitlines() if entry and not is_marker(entry)
        ][-12:]
        for entry in tail:
            print(f"{NAME}:   {label}| {entry}")


def main(argv: list[str]) -> int:
    """Rehearse each named runbook, reporting the first step of each that fails."""
    parser = argparse.ArgumentParser(
        description="Run an emitted runbook's non-writing steps, once, before the owner does."
    )
    parser.add_argument("script", nargs="+", type=Path, help="runbook(s) to rehearse")
    parser.add_argument(
        "--cwd", type=Path, default=Path.cwd(), help="repository to rehearse in"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="seconds before the rehearsal is abandoned",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the rehearsal script and what it withholds; execute nothing",
    )
    args = parser.parse_args(argv)

    failures = 0
    for path in args.script:
        if not path.is_file():
            print(f"{NAME}: error: {path}: no such file")
            return 2
        text = path.read_text(encoding="utf-8")
        if args.dry_run:
            script, steps, _ = render(text)
            for step in steps:
                print(f"{NAME}: {path}:{step.line}: withholding {step.reason}")
            print(script)
            continue
        outcome = rehearse(text, cwd=args.cwd, timeout=args.timeout)
        if outcome.exit_code == 2 and outcome.moved:
            print(f"{NAME}: error: {outcome.moved[0]}")
            return 2
        _report(path, outcome)
        failures += 0 if outcome.ok else 1
    if failures:
        print(f"{NAME}: {failures} runbook(s) would not run to the end")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
