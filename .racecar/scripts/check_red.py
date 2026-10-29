#!/usr/bin/env python3
"""Prove the tests a change adds actually FAIL without that change.

`shared/ACCEPTANCE.md` A3: the test must fail against the pre-fix tree. This checker
builds a scratch worktree at the base revision (`git worktree add --detach`; the owner's
tree is never touched), copies the changed test files in over it, runs them, and requires
them red.

A test file is whatever the runner's naming rule collects: pytest's `test_*.py` or
`*_test.py` by default, any language's patterns with `--runner make`
(`lib/shared/_test_files.py`), or the globs `--tests` names.

Two runners:
  - `--runner pytest` (default): `python -m pytest` over the changed test files, with
    `--junitxml`, so each changed file is graded from its own records.
  - `--runner make`: for a repo in any language. `make -s test-build` first, then
    `make -s test`. A build that fails is "could not decide", never red. With no
    `test-build` target the run refuses and names the line to add. `make test` is passed
    `RESULTS_DIR=<scratch>` and `TEST_LEG=red`; a Makefile that writes JUnit there gets
    per-file answers, and any other gets the whole-suite answer, which says what it does
    not prove when more than one test file changed.

A changed test file is one whose executable content changed: a file whose only edit is a
docstring, a comment or a reformat is declined. A file unparseable on either side, or
absent from the base, is graded.

Exit codes:
  - 0: every changed test file failed in the pre-fix tree, as it must; or there
    were no test changes to grade, reported as info; or a test PASSED without
    the change it tests and `RACECAR_STRICT` is not set, in which case the
    finding is printed and the run does not stop.
  - 1: a test passed without the change it tests, under `RACECAR_STRICT=true`.
  - 2: the pre-fix run could not decide: the scratch worktree, the build or the test
    run failed for a reason that is not a test result. `RACECAR_STRICT` does not apply.

`--base` is the revision to reconstruct, defaulting to `HEAD`. For a branch, pass the
merge-base: `--base "$(git merge-base main HEAD)"`.

Usage:
    python3 <path-to>/check_red.py [--root <path>] [--base <rev>] [--keep]
        [--runner pytest|make] [--tests GLOB ...]

Complexity: O(n) in changed files, plus one test run in the pre-fix tree (one pytest
run over the changed test files, or one build and one `make test`).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lib.shared._results import ERROR, FAILED, TestResult, read
from lib.shared._root import find_repo_root
from lib.shared._switches import STRICT_ENV, strict
from lib.shared._test_files import ALL_TEST_FILES, TEST_FILES

#: pytest's own default collection patterns, matched against a path's name.
TEST_GLOBS: tuple[str, ...] = TEST_FILES["python"]

#: pytest's exit codes. 1 is the outcome this checker wants; any other non-zero value
#: is a run that failed to decide.
PYTEST_PASSED = 0
PYTEST_FAILED = 1
PYTEST_NO_TESTS = 5

#: Not pytest's: this checker's own signal that the worktree never got built.
WORKTREE_FAILED = -1

#: `--runner make`'s own signals: the repo has no `test-build` target, or its build failed
#: in the pre-fix tree. Neither is a test result, so neither may read as red.
NO_BUILD_TARGET = -2
BUILD_FAILED = -3

#: This checker's exit when the pre-fix run could not decide. It is not a verdict, so
#: `RACECAR_STRICT`, which decides whether a verdict stops the run, does not apply.
UNDECIDED = 2

#: The line a repo adds to separate building from testing, for the refusal to name.
TEST_BUILD_HINT = (
    "test-build: ; swift build --build-tests   # or your language's build step"
)

#: Files that are neither source nor test, excluded from the changed set.
IGNORE_SUFFIXES: tuple[str, ...] = (".md", ".txt", ".jsonl", ".toml", ".yaml", ".yml")


def git(root: Path, *args: str) -> str:
    """Run a read-only git command in `root`; return stdout, or "" on failure."""
    out = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return out.stdout if out.returncode == 0 else ""


def is_test(path: str, globs: tuple[str, ...] = TEST_GLOBS) -> bool:
    """True iff the runner would collect this path as a test file (pytest's by default)."""
    name = Path(path).name
    return any(fnmatch.fnmatch(name, glob) for glob in globs)


def _executable(source: str) -> str | None:
    """`source` as an AST dump with docstrings stripped, or None when it will not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:]
    return ast.dump(tree)


def changed_test(root: Path, base: str, path: str) -> bool:
    """True iff this test file's executable content differs from `base`.

    Docstring, comment and reformat edits do not count. A file that will not parse on
    either side, or is absent from `base`, counts as changed.
    """
    was = git(root, "show", f"{base}:{path}")
    if not was:
        return True
    text = (root / path).read_text(encoding="utf-8")
    if not path.endswith(".py"):
        # Only Python is parsed for executable content; any other edit counts.
        return was != text
    now = _executable(text)
    if now is None:
        return True
    return _executable(was) != now


def changed_paths(root: Path, base: str) -> list[str]:
    """Every path differing from `base`: committed, staged, or untracked."""
    seen: set[str] = set()
    for args in (
        ("diff", "--name-only", base),
        ("diff", "--name-only", "--cached", base),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        for line in git(root, *args).splitlines():
            path = line.strip()
            if path and not path.endswith(IGNORE_SUFFIXES):
                seen.add(path)
    return sorted(seen)


def _may_name_python(glob: str) -> bool:
    """Whether `glob` can match a `.py` file: it ends in `.py`, or in no literal extension."""
    suffix = Path(glob).suffix
    return not suffix or suffix == ".py" or any(c in suffix for c in "*?[")


def _refuse_foreign_globs(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Under `--runner pytest`, refuse a `--tests` pattern that cannot name a `.py` file."""
    if args.runner != "pytest":
        return
    foreign = [g for g in args.tests or [] if not _may_name_python(g)]
    if foreign:
        parser.error(
            f"--tests {foreign[0]!r} names no Python file, and --runner pytest runs "
            "only Python; use --runner make"
        )


def _make_has(tree: Path, target: str) -> bool:
    """Whether the Makefile in `tree` has a rule for `target` (`make -n`, which runs nothing).

    No makefile at all is a no.
    """
    if not any(
        (tree / name).is_file() for name in ("GNUmakefile", "makefile", "Makefile")
    ):
        return False
    probe = subprocess.run(
        ["make", "-n", "-s", target],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0 or "No rule to make target" not in probe.stderr


def _run_make(tree: Path, scratch: Path) -> tuple[int, str]:
    """`make -s test-build`, then `make -s test` writing JUnit into `scratch`, in `tree`.

    The environment, `HOME` included, is inherited for the toolchain's caches.
    """
    if not _make_has(tree, "test-build"):
        return NO_BUILD_TARGET, ""
    build = subprocess.run(
        ["make", "-s", "test-build"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        return BUILD_FAILED, build.stdout + build.stderr
    proc = subprocess.run(
        ["make", "-s", "test", f"RESULTS_DIR={scratch}", "TEST_LEG=red"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run_pre_fix(
    root: Path, base: str, tests: list[str], keep: bool, runner: str = "pytest"
) -> tuple[int, str, list[TestResult] | None]:
    """Build the pre-fix tree, copy the changed tests in, run them.

    Returns the run's exit code, its output, and the per-test records when the run wrote
    a JUnit file (None when it did not). A non-zero code is the good outcome here.
    """
    scratch = Path(tempfile.mkdtemp(prefix="rc-red-"))
    tree = scratch / "tree"
    try:
        add = subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "--detach", str(tree), base],
            capture_output=True,
            text=True,
            check=False,
        )
        if add.returncode != 0:
            return WORKTREE_FAILED, add.stderr, None

        for rel in tests:
            dest = tree / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dest)

        # The interpreter running this script supplies pytest. PYTHONPATH names the
        # worktree's src/ and scripts/ first, so an installed `racecar` does not resolve
        # to the parent checkout. The parent environment is inherited, so `git` stays on
        # PATH.
        junit = scratch / "red.xml"
        if runner == "make":
            code, output = _run_make(tree, scratch)
        else:
            env = dict(os.environ)
            env["PYTHONPATH"] = f"{tree / 'src'}:{tree / 'scripts'}:{tree}"
            env["HOME"] = str(scratch)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "--no-header",
                    f"--junitxml={junit}",
                    "-o",
                    "junit_family=xunit1",
                    *tests,
                ],
                cwd=tree,
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            code, output = proc.returncode, proc.stdout + proc.stderr
        records = read([junit])[0] if junit.is_file() else None
        return code, output, records
    finally:
        if keep:
            print(f"check_red: info: worktree kept at {tree}")
        else:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "remove", "--force", str(tree)],
                capture_output=True,
                check=False,
            )
            shutil.rmtree(scratch, ignore_errors=True)


def weakened_assertions(root: Path, base: str, tests: list[str]) -> list[str]:
    """Test files whose existing lines this change deleted or rewrote.

    Advisory: the mechanical tell for A5. Only removed lines count.
    """
    touched: list[str] = []
    for rel in tests:
        diff = git(root, "diff", "-U0", base, "--", rel) or git(
            root, "diff", "-U0", "--cached", base, "--", rel
        )
        removed = [
            line
            for line in diff.splitlines()
            if line.startswith("-") and not line.startswith("---") and line[1:].strip()
        ]
        if removed:
            touched.append(
                f"{rel} ({len(removed)} existing line(s) removed or rewritten)"
            )
    return touched


def vacuous_files(tests: list[str], records: list[TestResult]) -> list[str]:
    """Changed test files whose every recorded test PASSED in the pre-fix tree.

    A file with no records is not called vacuous here; `verdict` asks whether it collected
    anything.
    """
    out: list[str] = []
    for rel in tests:
        mine = [r for r in records if r.file == rel]
        if mine and not any(r.outcome in (FAILED, ERROR) for r in mine):
            out.append(rel)
    return out


def make_verdict(code: int, tests: list[str], output: str) -> int:
    """`--runner make`'s exit code, turned into findings when no records say more."""
    if code == NO_BUILD_TARGET:
        print(
            "check_red: error: the pre-fix run could not decide: no `test-build` target"
        )
        print(
            "check_red: a failed build and a failed test both exit 2 under make, so the "
            "build must run on its own first. Add a target such as:"
        )
        print(f"check_red:   {TEST_BUILD_HINT}")
        print("check_red: 1 errors")
        return UNDECIDED
    if code == BUILD_FAILED:
        print("check_red: error: the pre-fix run could not decide (the build failed)")
        for line in output.strip().splitlines()[-8:]:
            print(f"check_red:   {line}")
        print(
            "check_red: a test that calls code only the fix adds cannot compile without "
            "the fix. That is not red. Confirm by hand; do not read it as red."
        )
        print("check_red: 1 errors")
        return UNDECIDED
    if code == 0:
        for rel in tests:
            print(f"check_red: error: {rel} passes without the change it tests")
        print(
            "check_red: the pre-fix tree ran the suite green. A test that cannot tell "
            "the fixed tree from the broken one is not evidence (ACCEPTANCE.md A3)."
        )
        print(f"check_red: {len(tests)} errors")
        return 1
    if len(tests) > 1:
        print(
            "check_red: info: the suite went red with the changed tests in; with no "
            "per-test records this does not show each changed file is red"
        )
    return 0


def verdict(code: int, tests: list[str], output: str) -> int:
    """Turn pytest's exit code into findings, and return this checker's own code."""
    if code == PYTEST_FAILED:
        return 0

    if code == PYTEST_PASSED:
        for rel in tests:
            print(f"check_red: error: {rel} passes without the change it tests")
        print(
            "check_red: the pre-fix tree ran these green. A test that cannot tell "
            "the fixed tree from the broken one is not evidence (ACCEPTANCE.md A3)."
        )
        print(f"check_red: {len(tests)} errors")
        return 1

    # Non-zero and not a test failure. Exit 5 means pytest collected nothing, which is
    # not red.
    if code == PYTEST_NO_TESTS:
        print(
            "check_red: error: the pre-fix tree collected no tests from "
            f"{', '.join(tests)}"
        )
        print(
            "check_red: a changed test file that runs no test cannot show red. "
            "Add a case, or this file is not the evidence for this change."
        )
        print("check_red: 1 errors")
        return 1

    print(f"check_red: error: the pre-fix run could not decide (pytest exit {code})")
    for line in output.strip().splitlines()[-8:]:
        print(f"check_red:   {line}")
    print(
        "check_red: a collection error may be legitimate — the test can import "
        "something only the fix adds — but this cannot tell that from a broken "
        "test. Confirm by hand; do not read it as red."
    )
    print("check_red: 1 errors")
    return UNDECIDED


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the pre-fix suite, print findings, return an exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None, help="repo root")
    parser.add_argument("--base", default="HEAD", help="revision to reconstruct")
    parser.add_argument(
        "--keep", action="store_true", help="leave the scratch worktree in place"
    )
    parser.add_argument(
        "--runner",
        choices=("pytest", "make"),
        default="pytest",
        help="pytest over the changed tests (default), or the repo's make test-build + test",
    )
    parser.add_argument(
        "--tests",
        action="append",
        metavar="GLOB",
        help="a test-file name pattern, repeatable (default: the runner's own rule)",
    )
    args = parser.parse_args(argv)
    _refuse_foreign_globs(parser, args)
    globs = (
        tuple(args.tests)
        if args.tests
        else (ALL_TEST_FILES if args.runner == "make" else TEST_GLOBS)
    )

    root = args.root or find_repo_root(Path.cwd())
    if root is None:
        print("check_red: info: not a git repository; nothing to grade")
        print("check_red: OK")
        return 0

    changed = changed_paths(root, args.base)
    touched = [p for p in changed if is_test(p, globs) and (root / p).is_file()]
    tests = [p for p in touched if changed_test(root, args.base, p)]
    for path in touched:
        if path not in tests:
            print(
                f"check_red: info: {path} changed no executable content; A3 has no referent"
            )
    if not tests:
        print(f"check_red: info: no changed test file against {args.base}")
        other = [p for p in changed if not p.endswith(".py")]
        if args.runner == "pytest" and other and _make_has(root, "test"):
            # Point a non-Python repo at the make runner.
            print(
                "check_red: info: no Python test changed, but other files did and a "
                "`make test` exists; for another language run "
                "`check_red.py --runner make --tests '<glob>'`"
            )
        print("check_red: OK")
        return 0

    code, output, records = run_pre_fix(root, args.base, tests, args.keep, args.runner)
    if code == WORKTREE_FAILED:
        print(f"check_red: error: could not build the pre-fix tree: {output.strip()}")
        print("check_red: 1 errors")
        return UNDECIDED

    mapped = [rel for rel in tests if any(r.file == rel for r in records or [])]
    if args.runner == "make" and (code < 0 or not mapped):
        # No per-test records name the changed files: the whole-suite answer.
        failed, hidden = make_verdict(code, tests, output), []
    elif args.runner == "make":
        # make's own exit code says only "something failed"; the records say which.
        failed, hidden = 0, vacuous_files(tests, records or [])
        for rel in sorted(set(tests) - set(mapped)):
            print(
                f"check_red: info: {rel} has no per-test records; not graded per file"
            )
    else:
        failed = verdict(code, tests, output)
        hidden = vacuous_files(tests, records or []) if code == PYTEST_FAILED else []
    for rel in hidden:
        print(f"check_red: error: {rel} passes without the change it tests")
    if hidden:
        print(
            "check_red: every test in these files passed in the pre-fix tree. A test "
            "that cannot tell the fixed tree from the broken one is not evidence, even "
            "when another file's failure turns the whole run red (ACCEPTANCE.md A3)."
        )
        print(f"check_red: {len(hidden)} errors")
        failed = 1
    if failed:
        # A red verdict is fatal only under RACECAR_STRICT; the finding is printed
        # either way. A run that could not decide is no verdict, so always fatal.
        if failed != UNDECIDED and not strict():
            print(f"check_red: not fatal — set {STRICT_ENV}=true to gate on this")
            return 0
        return failed

    for note in weakened_assertions(root, args.base, tests):
        print(f"check_red: info: {note} — A5 asks whether the ask said to preserve it")

    print(f"check_red: OK ({len(tests)} test file(s) red against {args.base})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
