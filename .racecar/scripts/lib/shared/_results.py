"""Test results as records: read JUnit XML files, and say what they add up to.

JUnit XML is the machine format every common runner can write: pytest `--junitxml`,
`swift test --xunit-output`, `go test` through `go-junit-report`. Each file holds one
`<testcase>` per test, with a `<failure>`, `<error>` or `<skipped>` child when it did not pass.

The verdict is said in words, last, so output truncated from the top (`tail -1`) still
shows it.

`xml.etree` is not hardened against hostile XML; it reads only files a test run on this
machine just wrote.

Complexity: O(t) in test cases across the files read.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

#: The outcomes a test case can have, in the order a failure listing reads them.
FAILED, ERROR, SKIPPED, PASSED = "failed", "error", "skipped", "passed"


@dataclass(frozen=True)
class TestResult:
    """One test case, as a JUnit file records it."""

    __test__ = False  # a record named Test*, not a pytest test class

    test: str  # `classname::name`, or `name` when there is no class
    file: str  # the test file, when the runner recorded one, else ""
    line: int  # 1-based; 0 when the runner recorded none
    outcome: str  # FAILED, ERROR, SKIPPED or PASSED
    seconds: float
    message: str  # the failure's first line, else ""


def _outcome(case: ET.Element) -> tuple[str, str]:
    """The case's outcome and the first line of its message."""
    for tag, outcome in (("failure", FAILED), ("error", ERROR), ("skipped", SKIPPED)):
        child = case.find(tag)
        if child is not None:
            text = child.get("message") or (child.text or "")
            return outcome, (text.strip().splitlines() or [""])[0]
    return PASSED, ""


def read(paths: list[Path]) -> tuple[list[TestResult], list[str]]:
    """Every test case in `paths`, and a reason for each file that could not be read.

    A missing or malformed file is reported, never skipped: a run that left no record is
    not a run that passed.
    """
    results: list[TestResult] = []
    unreadable: list[str] = []
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except FileNotFoundError:
            unreadable.append(f"{path}: no results file (the run did not finish)")
            continue
        except ET.ParseError as exc:
            unreadable.append(f"{path}: not JUnit XML ({exc})")
            continue
        for suite in root.iter("testsuite"):
            # pytest counts `line` from 0 (its own `item.location`), so its records point
            # one line above the test. pytest names its suite "pytest"; other runners'
            # lines are taken as written.
            offset = 1 if suite.get("name") == "pytest" else 0
            for case in suite.findall("testcase"):
                outcome, message = _outcome(case)
                cls, name = case.get("classname", ""), case.get("name", "")
                line = int(case.get("line") or 0)
                results.append(
                    TestResult(
                        test=f"{cls}::{name}" if cls else name,
                        file=case.get("file", ""),
                        line=line + offset if case.get("line") else 0,
                        outcome=outcome,
                        seconds=float(case.get("time") or 0),
                        message=message,
                    )
                )
    return results, unreadable


def verdict(results: list[TestResult], unreadable: list[str]) -> list[str]:
    """The lines to print: each failure, then the verdict in words, last."""
    lines = [f"could not read {why}" for why in unreadable]
    bad = [r for r in results if r.outcome in (FAILED, ERROR)]
    for r in bad:
        where = f"{r.file}:{r.line}" if r.file else r.test
        lines.append(
            f"{r.outcome.upper()} {where} {r.test} -- {r.message}".rstrip(" -")
        )
    ran = [r for r in results if r.outcome != SKIPPED]
    skipped = len(results) - len(ran)
    tail = f", {skipped} skipped" if skipped else ""
    if unreadable:
        lines.append(
            f"tests: could not run -- {len(unreadable)} results file(s) missing"
        )
    elif not ran:
        lines.append(f"tests: none ran{tail}")
    elif bad:
        lines.append(f"tests: {len(bad)} of {len(ran)} failed{tail}")
    else:
        lines.append(f"tests: all {len(ran)} passed{tail}")
    return lines
