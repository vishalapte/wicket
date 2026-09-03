#!/usr/bin/env python3
"""Commit-msg gate: the subject and body stay within shared/COMMITS.md's budget.

Enforces shared/COMMITS.md "Format" (subject length) and "Body" (prose ceiling).
The message is only available at commit-msg time, so this is a `commit-msg`-stage
hook, alongside check_version_bump.py.

The 72-column wrap COMMITS.md asks for is deliberately NOT gated. It is a writing
convention, and gating it failed real commits over one or two characters — a rule
that reads as a wall is a defect in the rule (PRINCIPLES.md R-06), and every
marginal failure dilutes the one below that matters.

Two rules, and only the first is absolute:

  1. The subject is at most 72 characters. A longer one truncates in
     `git log --oneline`, in every forge's list view, and in `git shortlog`.
     There is no legitimate instance, so it gates.

  2. The body stays short. COMMITS.md asks for 5 lines; this gate fails above
     12, and the gap is deliberate — a rule aimed at judgment and a gate aimed
     at abuse should not be the same number, and COMMITS.md states both so the
     prose and the contract do not disagree (PRINCIPLES.md R-02).

     12 is calibrated against this repo's own history rather than taste. Bodies
     that read well run 5 to 10 lines; the ones that motivated the gate — a
     CHANGELOG.md entry pasted into the commit body, a second home for one
     narrative (P-02) — run 14 to 19. The threshold sits in the gap, so it
     catches the dump and never argues with a good message.

     The count is lines, not sentences, so it applies unchanged to both body
     shapes COMMITS.md allows: an argument in prose, or an interface inventory
     in verb-first bullets. For the inventory the ceiling reads as a second
     signal — past roughly eight bullets the commit is doing more than one
     thing, and the answer is to split it, not to trim the list.

Footers are excluded from the prose count: `Bump version to X.Y.Z.`, the
`BREAKING CHANGE:` block, and the git trailers (`Co-Authored-By:`, `Signed-off-by:`,
`Reviewed-by:`, ...). A breaking change must be free to explain itself.

Comment lines (`#`), the `git commit --verbose` diff, and a `scissors` section are
stripped before anything is measured, exactly as git itself would strip them.

Usage (invoked by pre-commit at the commit-msg stage):
    python scripts/check_commit_message.py <commit-msg-file>

Exit 0 when the message conforms (or the gate does not apply), 1 on a violation.

Complexity: O(n), n = lines in the commit message
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SUBJECT_MAX = 72
BODY_PROSE_MAX = 12

# A conventional subject. A merge commit, a revert summary git wrote, or a fixup
# is not this gate's concern.
_CONVENTIONAL_RE = re.compile(r"^[a-z]+(\([^)]*\))?!?: .+")
_SKIP_PREFIXES = ("merge ", "revert ", "fixup!", "squash!", "amend!")

_FOOTER_RE = re.compile(
    r"^(BREAKING[ -]CHANGE:|Bump version to \d+\.\d+\.\d+\.$"
    r"|[A-Za-z][A-Za-z-]*-by:|Refs:|Closes:|Fixes:)",
)


def strip_noise(text: str) -> list[str]:
    """Drop comments and everything git itself would drop before storing."""
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith("# ------------------------ >8 ------------------------"):
            break  # scissors: everything below is commentary
        if raw.startswith("#"):
            continue
        lines.append(raw.rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def split_message(lines: list[str]) -> tuple[str, list[str]]:
    """Return ``(subject, body_lines)`` with the blank separator removed."""
    if not lines:
        return "", []
    subject = lines[0]
    body = lines[1:]
    i = 0
    while i < len(body) and not body[i].strip():
        i += 1
    return subject, body[i:]


def prose_lines(body: list[str]) -> list[tuple[int, str]]:
    """Body lines that count against the ceiling: everything above the footers.

    The first footer ends the prose. A footer cannot be followed by more prose in
    a well-formed message, and treating it that way keeps the rule simple enough
    to predict without running it.
    """
    out: list[tuple[int, str]] = []
    for offset, line in enumerate(body):
        if _FOOTER_RE.match(line.strip()):
            break
        if line.strip():
            out.append((offset + 3, line))  # +3: subject, blank, 1-indexed
    return out


def check(text: str) -> list[str]:
    """Return a list of violation messages; empty means the message conforms."""
    lines = strip_noise(text)
    subject, body = split_message(lines)
    if not subject:
        return []
    low = subject.lower()
    if low.startswith(_SKIP_PREFIXES) or not _CONVENTIONAL_RE.match(subject):
        return []  # not a conventional commit; not this gate's concern

    problems: list[str] = []
    if len(subject) > SUBJECT_MAX:
        problems.append(
            f"line 1: subject is {len(subject)} characters, over the "
            f"{SUBJECT_MAX} allowed (COMMITS.md Format). It must stand alone "
            f"in `git log --oneline`."
        )

    prose = prose_lines(body)
    if len(prose) > BODY_PROSE_MAX:
        problems.append(
            f"line {prose[BODY_PROSE_MAX][0]}: body runs {len(prose)} lines, "
            f"over the {BODY_PROSE_MAX} allowed (COMMITS.md Body, which asks for 5). "
            f"If this is an interface inventory, the commit is doing more than "
            f"one thing — split it. If it is the change's narrative, its one "
            f"home is CHANGELOG.md, not the commit body."
        )
    return problems


def main(argv: list[str]) -> int:
    """Validate the commit message file named on the command line."""
    parser = argparse.ArgumentParser(
        description="Commit-msg gate for shared/COMMITS.md subject and body limits."
    )
    parser.add_argument(
        "msgfile", help="Path to the commit message file (git supplies it)."
    )
    args = parser.parse_args(argv)

    path = Path(args.msgfile)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"check_commit_message: error: cannot read {path}: {exc}")
        return 1

    problems = check(text)
    if not problems:
        return 0
    for problem in problems:
        print(f"check_commit_message: {problem}")
    print(
        "\nshared/COMMITS.md: the default is no body. Add one only when a "
        "`git log` reader would otherwise be left with a question the diff "
        "cannot answer."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
