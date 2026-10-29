#!/usr/bin/env python3
"""Commit-msg gate: the subject and body stay within shared/COMMITS.md's budget.

Enforces shared/COMMITS.md "Format" (subject length) and "Body" (prose ceiling) at the
`commit-msg` stage, alongside check_version_bump.py. The 72-column wrap is not checked.

  1. The subject is at most 72 characters.
  2. The body's prose is at most 12 non-blank lines. COMMITS.md asks for 5 and states
     both numbers. The count is lines, so it applies to prose and bullet bodies alike.

Footers are excluded from the prose count: `Bump version to X.Y.Z.`, the
`BREAKING CHANGE:` block, and the git trailers (`Co-Authored-By:`, `Signed-off-by:`,
`Reviewed-by:`, ...).

Comment lines (`#`), the `git commit --verbose` diff, and a `scissors` section are
stripped before anything is measured, as git strips them.

Usage (invoked by pre-commit at the commit-msg stage):
    python scripts/check_commit_message.py <commit-msg-file>

Exit 0 when the message conforms (or the check does not apply), 1 on a violation.

Complexity: O(n), n = lines in the commit message
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from lib.shared import _commits

SUBJECT_MAX = 72
BODY_PROSE_MAX = 12


#: Public because `bump_version.py` loads it to write the footer this reads.
FOOTER_RE = re.compile(
    r"^(BREAKING[ -]CHANGE:|Bump version to \d+\.\d+\.\d+\.$"
    r"|[A-Za-z][A-Za-z-]*-by:|Refs:|Closes:|Fixes:)",
)


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
    """Body lines that count against the ceiling: everything above the first footer."""
    out: list[tuple[int, str]] = []
    for offset, line in enumerate(body):
        if FOOTER_RE.match(line.strip()):
            break
        if line.strip():
            out.append((offset + 3, line))  # +3: subject, blank, 1-indexed
    return out


def check(text: str) -> list[str]:
    """Return a list of violation messages; empty means the message conforms."""
    lines = _commits.strip_noise(text)
    subject, body = split_message(lines)
    if not subject:
        return []
    low = subject.lower()
    if low.startswith(_commits.SKIP_PREFIXES) or not _commits.SUBJECT_RE.match(subject):
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
