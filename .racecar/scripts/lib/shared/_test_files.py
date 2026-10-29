"""Which files are tests, per language, by each runner's own naming rule.

Read by `check_red.py` and `check_test_isolation.py`. Each row is the runner's own rule: the
file name the runner looks for, not a directory.

Complexity: O(g) in globs per path.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

#: The file names each language's test runner collects.
TEST_FILES: dict[str, tuple[str, ...]] = {
    # pytest's own default collection patterns.
    "python": ("test_*.py", "*_test.py"),
    # XCTest and Swift Testing targets: SwiftPM's convention names test files `*Tests`.
    "swift": ("*Tests.swift",),
}

#: Every pattern, for a caller that does not know the repo's language.
ALL_TEST_FILES: tuple[str, ...] = tuple(
    g for globs in TEST_FILES.values() for g in globs
)


def is_test(path: str, globs: tuple[str, ...]) -> bool:
    """True iff `path`'s file name matches one of `globs`."""
    name = Path(path).name
    return any(fnmatch.fnmatch(name, glob) for glob in globs)
