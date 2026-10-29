#!/usr/bin/env python3
"""Print what one or more JUnit XML files add up to, the verdict on the last line.

`racecar.mk`'s test recipe runs this over the file pytest just wrote. The exit code is always
0; the run's own status is the test's.

Usage:
    python3 <path-to>/results.py <junit.xml> [<junit.xml> ...]

Complexity: O(t) in test cases across the files given.
"""

from __future__ import annotations

import sys
from pathlib import Path

from lib.shared._results import read, verdict


def main(argv: list[str] | None = None) -> int:
    """Read the files named on the command line and print their verdict."""
    paths = [Path(p) for p in (sys.argv[1:] if argv is None else argv)]
    if not paths:
        print("results: name at least one JUnit XML file", file=sys.stderr)
        return 2
    results, unreadable = read(paths)
    for line in verdict(results, unreadable):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
