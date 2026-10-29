#!/usr/bin/env python3
"""Grade an emitted runbook before it runs.

racecar's writing skills commit only through a runbook -- one guarded, idempotent script at
`/tmp/rc-*.sh` -- run behind the owner's permission prompt (shared/OWNERSHIP.md). Every
runbook racecar writes must follow four rules:

  shell-safety            `set -euo pipefail`, so step 3 failing does not let step 4 run.
  head-guard              A comparison of `git rev-parse HEAD` against a literal sha,
                          before anything mutates. This is what makes it run-once.
  unguarded-apply         Every mutation before a commit sits inside an `if` or a `case`.
                          The idiom is skip / apply / refuse: already at NEW, skip; at OLD,
                          apply; neither, refuse and say so.
  fallible-after-commit   After the last commit, the only command that may fail is the gate
                          itself. Bookkeeping (moving a ref, writing a note, pruning a
                          worktree) goes after the gate and non-fatal. Publishing an
                          already-graded tree is permitted after the gate, and only after
                          it: `git switch`, `git merge --ff-only`, `git merge-base`,
                          `git branch -D` and `git update-ref`.

`unguarded-apply` is structural: it asserts a mutation is reached through a state test,
never that the test is the right one. The mutation set is an in-place `sed` or `perl`, a
here-doc Python that writes, a redirect into the repo, and `bump_version.py` without
`--check`. Git's own index and worktree restores (`add`, `reset`, `checkout`, `stash`) are
not mutations here.

    python3 scripts/check_runbook.py /tmp/rc-issue-racecar-19.sh

Complexity: O(n), n = lines in the runbook script
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

NAME = "check_runbook"

_SHELL_SAFETY = re.compile(r"^\s*set\s+-euo\s+pipefail\b")
# A recorded sha, long or short: `racecar-commit-merge` guards on N abbreviated branch tips.
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_REV_PARSE_REF = re.compile(r"git\s+rev-parse\b(?![^\n]*--(show-|git-dir|is-))")
# `pf_commit` is commit_preflight.sh's commit: in the worktree, every hook, to a fixpoint.
_COMMIT = re.compile(r"\bgit\s+commit\b|^\s*pf_commit\b")
_GATE = re.compile(r"\bmake\s+(-\S+\s+)*check(-full)?\b")

_OPENS_BLOCK = re.compile(r"^\s*(if|case)\b")
_CLOSES_BLOCK = re.compile(r"^\s*(fi|esac)\b")
_SET_MINUS_E = re.compile(r"^\s*set\s+\+e\b")
_SET_PLUS_E = re.compile(r"^\s*set\s+-e")
_TOLERATED = re.compile(r"\|\|\s*(true|:)\s*$")

# `name() {` -- a shell function. Its body runs where it is called, not where it is defined.
_FUNCTION = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\(\)\s*\{?\s*$")
_CLOSES_FUNCTION = re.compile(r"^\}")

# A heredoc opener, capturing the terminator whether or not it is quoted.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _heredoc_terminator(code: str) -> str | None:
    """The terminator a heredoc opener on `code` declares, or None.

    Used by `parse()` on both a fresh line and a `\\`-continuation chain's joined line.
    """
    opener = _HEREDOC.search(code)
    return opener.group(2) if opener else None


# Lines that are structure rather than a command, so they can neither fail nor mutate.
_STRUCTURAL = re.compile(
    r"^\s*(fi|esac|done|else|elif\b|;;|\}|\{|then|do|"
    r"[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{?\s*)$"
)

# Commands that report or set, and touch nothing; permitted after a commit without
# `|| true`. `exit` cannot fail: after the last commit it is a deliberate refusal.
_INERT = re.compile(
    r"^\s*(printf|echo|cat|trap|set|exit|:|true|[A-Za-z_][A-Za-z0-9_]*=)"
)
# `_INERT` reads only the first word, so a line is inert only if it chains nothing after
# it: `echo x && git push` is a push. Searched in `bare()` code. A line ending `|| true`
# is already accepted by `guarded()`.
_CHAINED = re.compile(r"&&|[;|]")
# A whole `if [ ... ]; then` line reads and changes nothing. A bare `[ ` at line start is
# not inert: `[ -f x ] && git push` is a push.
_INERT_TEST = re.compile(r"^\s*if\s+\[[^;&|]*\]\s*;\s*then\s*$")

# Publishing an already-graded tree: the commands that move a ref and cannot produce a tree
# the gate did not see. `--ff-only` is required; a plain merge can create a new tree.
# `git update-ref <ref> <new> <old>` moves the ref only if it still holds <old>.
# Permitted only after the gate has run, never merely after the last commit.
_PUBLISH = re.compile(
    r"^\s*git\s+(switch|checkout)\b"
    r"|^\s*git\s+merge\s+--ff-only\b"
    r"|^\s*git\s+merge-base\b"
    r"|^\s*git\s+branch\s+-D\b"
    r"|^\s*(if\s+!\s+)?git\s+(-C\s+\S+\s+)?(update-ref|fetch)\b"
)

# Quoted spans are prose: `->` in `0.8.0 -> 0.9.0` is not a redirect.
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")

_MUTATORS = (
    (re.compile(r"\bsed\s+(-\S+\s+)*-i\b|\bsed\s+-i"), "an in-place sed"),
    (re.compile(r"\bperl\s+-\S*i\S*\b"), "an in-place perl"),
    # `>&2` and `2>&1` are fd plumbing, not a write, and `> /tmp/...` leaves the tree alone.
    (re.compile(r"(?<!\d)>>?\s*(?![&/]|\"?\$)"), "a redirect into the tree"),
)

# A variable bound to `$(mktemp ...)`. `cp`/`mv` into one of these is a snapshot; into any
# other destination it is a write into the tree.
# Matched against the raw line, never `bare()`, which collapses `"$RC/bump_version.py"`
# to `$Q`.
_RAW_MUTATORS = ((re.compile(r"\bbump_version\.py\b(?!.*--check)"), "bump_version.py"),)
# A line that is nothing but literal quoted words (`bare()` leaves only `Q` tokens) is
# data, such as an element of the `FILES_N=( ... )` array a generated runbook stages from.
_LITERAL_WORDS = re.compile(r"Q(\s+Q)*")

_MKTEMP_ASSIGN = re.compile(r'^([A-Za-z_]\w*)="?\$\(\s*mktemp\b')
_CP_MV = re.compile(r"^\s*(cp|mv)\b")
_CP_MV_DEST = re.compile(r'(?:cp|mv)\s+.*[\s"]\$([A-Za-z_]\w*)"?\s*$')

# A here-doc Python is a mutation only when its body writes. Beyond the pathlib/builtin
# spellings, shutil.copy*/os.replace/json.dump also write.
_PY_WRITES = re.compile(
    r"write_text\(|write_bytes\(|\.write\(|\bopen\([^)]*[\"'][wax]"
    r"|shutil\.copy\w*\(|os\.replace\(|json\.dump\("
)


class Finding(NamedTuple):
    line: int
    rule: str
    message: str


class Line(NamedTuple):
    """One command of the script, with everything position-dependent already resolved."""

    number: int
    code: str
    depth: int  # `if` / `case` nesting the line is reached through
    errexit: bool  # whether a failure here would stop the script
    heredoc: str  # the body of a here-doc opened on this line
    function: str  # the shell function this line is defined inside, or ""
    # The last physical line this command occupies, including `\`-continuations and any
    # here-doc body. Read by consumers that rewrite the script, not by `check`.
    end: int = 0


def temp_variables(lines: list[Line]) -> frozenset[str]:
    """Names this script binds to `$(mktemp ...)`, anywhere in the script."""
    return frozenset(
        m.group(1) for line in lines if (m := _MKTEMP_ASSIGN.match(line.code))
    )


def _extend_continuation(
    pending: list[str], code: str
) -> tuple[list[str], bool, str | None]:
    """Append `code` to a `\\`-continuation chain; return (pending, still-open, joined).

    `joined` is the finished command text once the chain closes, else None.
    """
    pending.append(code)
    if code.endswith("\\"):
        return pending, True, None
    joined = " ".join(fragment.rstrip("\\") for fragment in pending)
    return [], False, joined


def parse(text: str) -> list[Line]:
    """Flatten the script to commands, folding each here-doc into its opening line.

    A here-doc body is data, not commands. A single-pass, line-oriented scanner.
    """
    out: list[Line] = []
    terminator: str | None = None
    body: list[str] = []
    # Only the current nesting depth is needed, so a counter suffices.
    depth = 0
    errexit = True
    function = ""
    continued = False
    # Fragments of the `\`-continued command being read, joined once when the chain closes.
    pending: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if terminator is not None:
            if raw.strip() == terminator:
                out[-1] = out[-1]._replace(heredoc="\n".join(body), end=number)
                terminator, body = None, []
            else:
                body.append(raw)
            continue
        code = raw.strip()
        if not code or code.startswith("#"):
            continue
        if out and continued:
            # A `\`-continued command is one command.
            pending, continued, joined = _extend_continuation(pending, code)
            if joined is None:
                out[-1] = out[-1]._replace(end=number)
            else:
                out[-1] = out[-1]._replace(code=joined, end=number)
                # A continuation chain closing into a `<<'EOF'` still opens a heredoc.
                terminator = _heredoc_terminator(joined)
            continue
        continued = code.endswith("\\")
        if continued:
            pending = [code]
        if _SET_MINUS_E.match(code):
            errexit = False
        elif _SET_PLUS_E.match(code):
            errexit = True
        opened = _FUNCTION.match(code)
        if opened and not function:
            function = opened.group(1)
        elif function and _CLOSES_FUNCTION.match(raw):
            function = ""
        reached_at = depth
        if _OPENS_BLOCK.match(code):
            depth += 1
        elif _CLOSES_BLOCK.match(code):
            depth = max(0, depth - 1)
            reached_at = depth
        out.append(Line(number, code, reached_at, errexit, "", function, number))
        terminator = _heredoc_terminator(code)
    return out


def bare(code: str) -> str:
    """`code` with every quoted span collapsed to one token.

    A quoted span is prose, so `echo "then run git push"` is not a push. The collapse
    keeps only whether the span named a variable: `>"$msg"` writes to a variable path.
    Public because `rehearse_runbook.py` classifies the same lines.
    """
    return _QUOTED.sub(lambda m: "$Q" if "$" in m.group(0) else "Q", code)


def mutates(line: Line, temp_vars: frozenset[str] = frozenset()) -> str | None:
    """What this line rewrites in the tree, or None when it rewrites nothing.

    `temp_vars` (from `temp_variables`) names the `$(mktemp ...)`-bound variables; a
    `cp`/`mv` into one is a snapshot. Read against the raw code, not `bare()`, so
    `cp "$CFG" "$SNAP"` (a backup) differs from `cp "$SNAP" "$CFG"` (a write).
    """
    if line.heredoc and _PY_WRITES.search(line.heredoc):
        return "a here-doc that writes"
    if _CP_MV.match(bare(line.code)):
        dest = _CP_MV_DEST.search(line.code)
        if not dest or dest.group(1) not in temp_vars:
            return "a cp/mv into the tree"
        return None
    if not _LITERAL_WORDS.fullmatch(bare(line.code)):
        for pattern, what in _RAW_MUTATORS:
            if pattern.search(line.code):
                return what
    for pattern, what in _MUTATORS:
        if pattern.search(bare(line.code)):
            return what
    return None


def guarded(line: Line) -> bool:
    """True when a failure on this line cannot stop the script."""
    return not line.errexit or bool(_TOLERATED.search(line.code))


def check(text: str) -> list[Finding]:
    """Return every way this runbook departs from the shape, in line order."""
    lines = parse(text)
    if not lines:
        return [Finding(1, "empty", "the runbook has no commands")]

    findings: list[Finding] = []
    temp_vars = temp_variables(lines)
    committing = {
        line.function for line in lines if line.function and _COMMIT.search(line.code)
    }
    called = re.compile(r"^\s*(" + "|".join(re.escape(f) for f in committing) + r")\b")
    commits = [
        line
        for line in lines
        if (_COMMIT.search(line.code) and not line.function)
        or (committing and not line.function and called.match(line.code))
    ]

    if not any(_SHELL_SAFETY.match(line.code) for line in lines):
        findings.append(
            Finding(
                1,
                "shell-safety",
                "no `set -euo pipefail`, so a failed step lets the next one run against "
                "a tree it did not expect.",
            )
        )

    # 10**9 is a sentinel for "no such line", so min() needs no empty-list branch.
    first_act = min(
        ([line.number for line in lines if mutates(line, temp_vars)] or [10**9])
        + ([commits[0].number] if commits else [10**9])
    )
    guard_at = [
        line.number
        for line in lines
        if _REV_PARSE_REF.search(line.code) or _SHA.search(line.code)
    ]
    has_guard = any(_REV_PARSE_REF.search(line.code) for line in lines) and any(
        _SHA.search(line.code) for line in lines
    )
    if commits and not (has_guard and guard_at and min(guard_at) < first_act):
        findings.append(
            Finding(
                first_act if first_act < 10**9 else 1,
                "head-guard",
                "no HEAD guard before the first mutation. Record the authoring sha and "
                "refuse to run against a different one, or a re-run commits nonsense "
                "against a tree that moved underneath it.",
            )
        )

    last_commit = commits[-1].number if commits else None
    gate_at = next((line.number for line in lines if _GATE.search(line.code)), None)
    for line in lines:
        what = mutates(line, temp_vars)
        if (
            what
            and line.depth == 0
            and (last_commit is None or line.number < last_commit)
        ):
            findings.append(
                Finding(
                    line.number,
                    "unguarded-apply",
                    f"{what} runs unconditionally before the commit. A commit is the step "
                    f"most likely to fail, and an edit that asserts its pre-state leaves "
                    f"the operator a dead end. Reach it through a state test: already "
                    f"applied, skip; appliable, apply; neither, refuse and say so.",
                )
            )
        if (
            last_commit is not None
            and line.number > last_commit
            and not guarded(line)
            and not _GATE.search(line.code)
            and not _STRUCTURAL.match(line.code)
            and not (_INERT.match(line.code) and not _CHAINED.search(bare(line.code)))
            and not _INERT_TEST.match(line.code)
            and not (
                gate_at is not None
                and line.number > gate_at
                and _PUBLISH.search(line.code)
            )
        ):
            findings.append(
                Finding(
                    line.number,
                    "fallible-after-commit",
                    "this can fail after the tree has already moved, and only the gate "
                    "may do that. Bookkeeping goes after the gate and non-fatal — "
                    "stopping between 'the tree has moved' and 'something graded it' is "
                    "the worst state a runbook can leave behind.",
                )
            )
    return sorted(findings)


def main(argv: list[str]) -> int:
    """Grade each named runbook, reporting one line per finding at `file:line`."""
    parser = argparse.ArgumentParser(
        description="Grade an emitted runbook against its four rules."
    )
    parser.add_argument("script", nargs="+", type=Path, help="runbook(s) to grade")
    args = parser.parse_args(argv)

    total = 0
    for path in args.script:
        if not path.is_file():
            print(f"{NAME}: error: {path}: no such file")
            total += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(f"{NAME}: error: {path}: not valid UTF-8 ({exc})")
            total += 1
            continue
        for finding in check(text):
            print(
                f"{NAME}: error: {path}:{finding.line}: [{finding.rule}] {finding.message}"
            )
            total += 1
    if total:
        print(f"{NAME}: {total} error(s)")
        return 1
    print(f"{NAME}: OK ({len(args.script)} runbook(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
