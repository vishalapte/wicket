#!/usr/bin/env python3
"""Report a test that reaches the user's real state instead of a scratch copy of it.

A test that writes the machine's real settings store, home directory or keychain changes
the running application on the machine that runs the suite, and `check_red.py` cannot see
it. This reads test files for the handles that reach real state and reports each at
`file:line`. The handles are data, one row per language (`HANDLES`).

A use that is meant is marked with `racecar: real-state <reason>` in a comment on that line
or the line above, and is then not reported. A mark with no reason is reported.

Exit codes:
  - 0: nothing found; or findings, reported as warnings, when `RACECAR_STRICT` is not set.
  - 1: findings, under `RACECAR_STRICT=true`, where they are errors.

Usage:
    python3 <path-to>/check_test_isolation.py [--root <path>] [--tests GLOB ...]

Complexity: O(L) in lines of test files.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from lib.shared._files import repo_files
from lib.shared._report import Findings, emit
from lib.shared._root import find_repo_root
from lib.shared._switches import strict
from lib.shared._test_files import ALL_TEST_FILES, is_test

PROG = "check_test_isolation"

#: Per language (by file suffix): the symbols that reach real state, and what they reach.
#: A row is added when an incident shows that language's tests reaching it.
HANDLES: dict[str, tuple[tuple[str, str], ...]] = {
    ".swift": (
        ("UserDefaults.standard", "the user's real settings store"),
        (
            "FileManager.default.homeDirectoryForCurrentUser",
            "the user's real home directory",
        ),
        ("NSHomeDirectory()", "the user's real home directory"),
        ("SecItemAdd", "the user's real keychain"),
    ),
}


def _pattern(symbol: str) -> re.Pattern[str]:
    """`symbol` as a whole token: not a longer name that happens to contain it."""
    tail = "" if symbol.endswith(")") else r"\b"
    return re.compile(r"(?<![\w.])" + re.escape(symbol) + tail)


#: The mark that says a use is meant, and its reason, in a comment.
MARK = re.compile(r"racecar:\s*real-state\b(?P<reason>.*)")


def _marked(lines: list[str], index: int) -> tuple[bool, bool]:
    """(marked, has a reason) for the line at `index`: the mark on it or on the line above."""
    for at in (index, index - 1):
        if at < 0:
            continue
        hit = MARK.search(lines[at])
        if hit:
            return True, bool(hit["reason"].strip(" :-—"))
    return False, False


def findings(root: Path, globs: tuple[str, ...]) -> list[str]:
    """Every unmarked use of a real-state handle in a test file, as `file:line: message`."""
    out: list[str] = []
    for path in repo_files(root, *globs):
        rows = HANDLES.get(path.suffix)
        rel = path.relative_to(root).as_posix()
        if not rows or not path.is_file() or not is_test(rel, globs):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            for symbol, reaches in rows:
                if not _pattern(symbol).search(line):
                    continue
                marked, reason = _marked(lines, index)
                if marked and reason:
                    continue
                why = (
                    "is marked `racecar: real-state` with no reason; say why it is meant"
                    if marked
                    else "use a scratch instance the test owns, or mark it "
                    "`racecar: real-state <reason>` if it is meant"
                )
                out.append(
                    f"{rel}:{index + 1}: a test reaches {reaches} (`{symbol}`); {why}"
                )
    return out


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, read the test files, report, and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None, help="repo root")
    parser.add_argument(
        "--tests",
        action="append",
        metavar="GLOB",
        help="a test-file name pattern, repeatable (default: every known language's)",
    )
    args = parser.parse_args(argv)
    root = (args.root or find_repo_root(Path.cwd())).resolve()
    globs = tuple(args.tests) if args.tests else ALL_TEST_FILES
    report = Findings()
    for message in findings(root, globs):
        (report.error if strict() else report.warning)(message)
    return emit(report, PROG)


if __name__ == "__main__":
    sys.exit(main())
