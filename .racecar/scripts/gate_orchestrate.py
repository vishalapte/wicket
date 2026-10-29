#!/usr/bin/env python3
"""Run several make targets at once and render their output in a FIXED order.

Complexity: O(n) processes for n legs; wall time is the slowest leg, not their sum.

`check-full` fans out into legs that share nothing — one grades the code and runs the
fast tier, one lints the suites, one runs the slow tier, one does typecheck + arch. This
runs them concurrently and prints each leg's captured output in the declared order, as
soon as every leg before it has finished. It stands in for `--output-sync=target`, which
make 3.81 (macOS) rejects.

It does not parse or reformat a leg's output, and does not decide what runs: the caller
names the targets, in the order it wants them read.

Usage:
    python3 gate_orchestrate.py --make "make" -- _check lint-tests test-slow

Exit code is the worst leg's. Every leg runs to completion even when an earlier one has
already failed.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import threading
import time


def _spawn(make: list[str], target: str) -> subprocess.Popen[str]:
    """Start one leg, with its stdout and stderr merged into one captured stream."""
    return subprocess.Popen(
        [*make, "--no-print-directory", target],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def main(argv: list[str] | None = None) -> int:
    """Run every target concurrently; print them in argv order; return the worst code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Split: `$(MAKE)` is a command line carrying make's flags, not one executable.
    parser.add_argument("--make", default="make", help="the make command to invoke")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="omit the per-leg timing footer",
    )
    parser.add_argument("targets", nargs="+", help="make targets, in render order")
    args = parser.parse_args(argv)

    started = time.monotonic()
    make = shlex.split(args.make)
    results: list[dict[str, object]] = []

    def drain(slot: dict[str, object]) -> None:
        """Read one leg to EOF and record when it actually ended."""
        proc = slot["proc"]
        assert isinstance(proc, subprocess.Popen)
        slot["output"] = proc.communicate()[0]
        slot["code"] = proc.returncode
        slot["elapsed"] = time.monotonic() - started

    # A reader thread per leg, so each finish time is taken when the leg ends and a leg
    # that fills its pipe buffer cannot deadlock.
    threads = []
    for target in args.targets:
        slot: dict[str, object] = {"target": target, "proc": _spawn(make, target)}
        results.append(slot)
        thread = threading.Thread(target=drain, args=(slot,), daemon=True)
        thread.start()
        threads.append(thread)

    worst = 0
    for slot, thread in zip(results, threads):
        thread.join()
        code = int(slot["code"])  # type: ignore[call-overload]
        worst = worst or code
        status = "ok" if code == 0 else f"FAILED ({code})"
        print(
            f"===== {slot['target']}: {status} in {slot['elapsed']:.1f}s =====",
            flush=True,
        )
        output = slot["output"]
        if output:
            print(str(output).rstrip("\n"), flush=True)

    if not args.quiet:
        total = time.monotonic() - started
        # The per-leg times sum to more than this; the gap is the parallelism.
        print(f"gate_orchestrate: {len(results)} leg(s), {total:.1f}s wall", flush=True)
    return worst


if __name__ == "__main__":
    sys.exit(main())
