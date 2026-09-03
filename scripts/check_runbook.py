#!/usr/bin/env python3
"""Grade an emitted runbook before the owner runs it.

racecar's writing skills do not commit. They hand the owner a script -- one guarded,
idempotent file at `/tmp/rc-*.sh` -- and that script is the artifact the whole
tooling-confirms-owner-authorizes split rests on (shared/OWNERSHIP.md). Its shape is
specified once, in `commit/SKILL.md` "Required properties", and inherited by every skill
that emits one.

A HEAD guard makes the COMMIT run once. It does not make the EDITS run once, and the edits
run first. That asymmetry is what this grades. Observed on three of seven runbooks in one
session: the commit failed on a hook, the version home and the changelog had already moved,
and a re-run died asserting a number that was no longer there -- a safe failure, and a dead
end the operator had to unpick by hand. A fourth moved a branch ref after committing and
before `make check-full`; the ref move failed, `set -e` stopped the script, and the commit
sat in the trunk with nothing having graded it.

FOUR RULES, each with one of those failures behind it:

  shell-safety            `set -euo pipefail`, so step 3 failing does not let step 4 run.
  head-guard              A comparison of `git rev-parse HEAD` against a literal sha,
                          before anything mutates. This is what makes it run-once.
  unguarded-apply         Every mutation before a commit sits inside an `if` or a `case`.
                          The idiom is skip / apply / refuse: already at NEW, skip; at OLD,
                          apply; neither, refuse and say so. Refusing matters -- silently
                          guessing between the two is how a double bump happens.
  fallible-after-commit   After the last commit, the only command that may fail is the gate
                          itself. Bookkeeping the forge wants -- moving a ref, writing a
                          note, pruning a worktree -- goes after the gate and non-fatal.

WHAT THIS DOES NOT PROVE, said out loud because a guard that quietly stops guarding is
worse than one that was never there. `unguarded-apply` is structural: it asserts a mutation
is reached through a state test, never that the test is the RIGHT one. `if true; then` sails
through. It catches the shape that actually failed -- an edit applied unconditionally -- and
a reviewer still owns whether the condition means anything.

The mutation set is deliberately narrow, for the same reason: an in-place `sed` or `perl`,
a here-doc Python that writes, a redirect into the repo, and `renumber_bump.py` without
`--check`. Git's own index and worktree restores (`add`, `reset`, `checkout`, `stash`) are
idempotent by construction and are not mutations here -- flagging the pre-commit-config
quarantine idiom, which is re-entrant precisely because it restores from HEAD, would train
the reader to ignore the checker.

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
# A recorded sha, long or short. `racecar-commit-merge` guards on N branch tips rather
# than one HEAD, and records them abbreviated, so a 40-hex-only rule reads its N-ref guard
# as no guard at all.
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_REV_PARSE_REF = re.compile(r"git\s+rev-parse\b(?![^\n]*--(show-|git-dir|is-))")
_COMMIT = re.compile(r"\bgit\s+commit\b")
_GATE = re.compile(r"\bmake\s+(-\S+\s+)*check(-full)?\b")

_OPENS_BLOCK = re.compile(r"^\s*(if|case)\b")
_CLOSES_BLOCK = re.compile(r"^\s*(fi|esac)\b")
_SET_MINUS_E = re.compile(r"^\s*set\s+\+e\b")
_SET_PLUS_E = re.compile(r"^\s*set\s+-e")
_TOLERATED = re.compile(r"\|\|\s*(true|:)\s*$")

# `name() {` -- a shell function. Its body runs where it is CALLED, so reading it in place
# would put a runbook's commit wherever the definition happens to sit and grade its own
# driver loop as bookkeeping after it.
_FUNCTION = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\(\)\s*\{?\s*$")
_CLOSES_FUNCTION = re.compile(r"^\}")

# A heredoc opener, capturing the terminator whether or not it is quoted.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _heredoc_terminator(code: str) -> str | None:
    """The terminator a heredoc opener on `code` declares, or None.

    One home for the check `parse()` runs on both a fresh line and a `\\`-continuation
    chain's joined line -- a heredoc opener is a heredoc opener regardless of which path
    produced the line that carries it.
    """
    opener = _HEREDOC.search(code)
    return opener.group(2) if opener else None


# Lines that are structure rather than a command, so they can neither fail nor mutate.
_STRUCTURAL = re.compile(
    r"^\s*(fi|esac|done|else|elif\b|;;|\}|\{|then|do|"
    r"[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{?\s*)$"
)

# Commands that report or set, and touch nothing. The rule below exists so that nothing
# which can fail AND leave the tree ungraded runs after a commit; demanding `|| true` on
# the line that tells the operator what happened would be a wall rather than a rule
# (PRINCIPLES.md R-06), and a checker read as a wall gets switched off.
_INERT = re.compile(r"^\s*(printf|echo|cat|trap|set|:|true|[A-Za-z_][A-Za-z0-9_]*=)")

# Quoted spans are prose -- a progress message, a drafted subject, an arrow between two
# version numbers. Reading `->` in `0.8.0 -> 0.9.0` as a redirect into the tree is the
# false positive that would make this checker unreadable on the merge runbook, whose whole
# output is that arrow.
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")

_MUTATORS = (
    (re.compile(r"\bsed\s+(-\S+\s+)*-i\b|\bsed\s+-i"), "an in-place sed"),
    (re.compile(r"\bperl\s+-\S*i\S*\b"), "an in-place perl"),
    (re.compile(r"\brenumber_bump\.py\b(?!.*--check)"), "renumber_bump.py"),
    # `>&2` and `2>&1` are fd plumbing, not a write, and `> /tmp/...` leaves the tree alone.
    (re.compile(r"(?<!\d)>>?\s*(?![&/]|\"?\$)"), "a redirect into the tree"),
)

# A variable bound to `$(mktemp ...)` -- the one idiom this repo's own runbooks use for a
# genuine scratch path (`TMP_DIR="$(mktemp -d)"`, the `.pre-commit-config.yaml` quarantine's
# `SNAP=$(mktemp)`). `cp`/`mv` INTO one of these is a snapshot; the same command into any
# other destination is indistinguishable, statically, from `cp "$SRC" "$f"` restoring a
# tracked file out of scratch -- which is exactly the write into the tree this exists to
# catch, and which a real runbook did make, unflagged, before this exemption was scoped.
_MKTEMP_ASSIGN = re.compile(r'^([A-Za-z_]\w*)="?\$\(\s*mktemp\b')
_CP_MV = re.compile(r"^\s*(cp|mv)\b")
_CP_MV_DEST = re.compile(r'(?:cp|mv)\s+.*[\s"]\$([A-Za-z_]\w*)"?\s*$')

# A here-doc Python is a mutation only when its body writes; the same construct is the
# ordinary way to READ a value out of pyproject.toml, and reading is re-entrant. Beyond
# the two pathlib/builtin spellings, shutil.copy*/os.replace/json.dump are the other
# stdlib calls a drafted here-doc plausibly writes through.
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
    # The last PHYSICAL line this command occupies -- its own, plus any `\`-continuations
    # and any here-doc body down to the terminator. `check` never reads it; a consumer that
    # rewrites the script does, and computing the span twice is how the two disagree about
    # where a commit's message ends.
    end: int = 0


def temp_variables(lines: list[Line]) -> frozenset[str]:
    """Names this script binds to `$(mktemp ...)`, anywhere -- assignment can precede or
    follow the copy that uses them in file order, so this is a whole-script pass, not a
    per-line one."""
    return frozenset(
        m.group(1) for line in lines if (m := _MKTEMP_ASSIGN.match(line.code))
    )


def _extend_continuation(
    pending: list[str], code: str
) -> tuple[list[str], bool, str | None]:
    """Append `code` to a `\\`-continuation chain; return (pending, still-open, joined).

    `joined` is the finished command text once the chain closes (`code` does not itself
    end in `\\`), else None -- the caller only writes `out[-1].code` on that transition,
    which is what keeps `parse` from rebuilding the accumulated string on every line (see
    `parse`'s own note on the O(k^2) this replaced).
    """
    pending.append(code)
    if code.endswith("\\"):
        return pending, True, None
    # `.rstrip("\\")` on the last fragment is a no-op (it does not end in `\`, or the
    # chain would not have closed) -- applying it uniformly avoids a slice-based special
    # case for the same result.
    joined = " ".join(fragment.rstrip("\\") for fragment in pending)
    return [], False, joined


def parse(text: str) -> list[Line]:
    """Flatten the script to commands, folding each here-doc into its opening line.

    A here-doc body is data -- a drafted commit message, a Python fragment -- and reading
    it as commands would let a changelog entry that happens to contain `git commit` decide
    where the last commit is.

    The shape is a single-pass, line-oriented state-machine scanner -- a hand-written
    lexer: one walk over physical lines, no backtracking, carrying `terminator`/`body`
    (heredoc sub-state), `pending` (continuation sub-state), and `depth`/`errexit`/
    `function` as running state threaded line to line. Backslash-newline folding is what
    the C standard calls line splicing; a here-doc body is read the way a lexer reads a
    quoted string or comment block -- an opaque, sentinel-terminated span.
    """
    out: list[Line] = []
    terminator: str | None = None
    body: list[str] = []
    # A plain counter, not a stack -- the degenerate case of stack-based bracket/nesting
    # matching, valid because only the current nesting DEPTH is ever needed, never which
    # construct opened it. A full stack would be needed only to recover that identity.
    depth = 0
    errexit = True
    function = ""
    continued = False
    # Fragments of the `\`-continued command currently being read -- see
    # `_extend_continuation` for why these are joined once, when the chain closes,
    # rather than rebuilt from scratch on every line.
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
            # A `\`-continued command is one command; reporting its `gh pr create` and
            # each of its five flags as five findings buries the one fact.
            pending, continued, joined = _extend_continuation(pending, code)
            if joined is None:
                out[-1] = out[-1]._replace(end=number)
            else:
                out[-1] = out[-1]._replace(code=joined, end=number)
                # The same heredoc-opener check the fresh-line path runs below, applied
                # to the joined line: a continuation chain closing into a `<<'EOF'` must
                # still open a heredoc, or its body is read as ordinary commands.
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

    A quoted span is prose -- a progress message, a drafted subject, an arrow between two
    version numbers -- and reading commands inside it is how `echo "then run git push"`
    becomes a push. The collapse keeps only whether the span named a variable, because
    `>"$msg"` writes to whatever `$msg` is and a variable path is the /tmp idiom.

    Public because `rehearse_runbook.py` classifies the same lines and must read them the
    same way: two homes for "what is prose here" is two answers to `git push`.
    """
    return _QUOTED.sub(lambda m: "$Q" if "$" in m.group(0) else "Q", code)


def mutates(line: Line, temp_vars: frozenset[str] = frozenset()) -> str | None:
    """What this line rewrites in the tree, or None when it rewrites nothing.

    `temp_vars` (from `temp_variables`) is the whole script's `$(mktemp ...)`-bound
    names -- a `cp`/`mv` whose destination is one of them is a snapshot, not a write into
    the tree. Read against the RAW code, not `bare()`: `bare()` collapses every quoted
    variable reference to the same `$Q` token, which is exactly the distinction this needs
    to keep (`cp "$CFG" "$SNAP"` is a backup; `cp "$SNAP" "$CFG"` is the write back).
    """
    if line.heredoc and _PY_WRITES.search(line.heredoc):
        return "a here-doc that writes"
    if _CP_MV.match(bare(line.code)):
        dest = _CP_MV_DEST.search(line.code)
        if not dest or dest.group(1) not in temp_vars:
            return "a cp/mv into the tree"
        return None
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

    # 10**9 is a sentinel standing in for "no such line" -- past any real line number --
    # so min() picks the earliest of "first mutation" and "first commit" with no branch
    # for the empty-list case, unwound back to a real line number only for display below.
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
            and not _INERT.match(line.code)
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
        description="Grade an emitted runbook against commit/SKILL.md's required shape."
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
