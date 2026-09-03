"""Grade the CLI tree against `src/<pkg>/api/surface.jsonl` — the WHAT, not the HOW.

`arch-python/SURFACES.md` §19 gives a governed package one spec file, one row per
capability, carrying that capability's `cli`, `pages`, `rest` and `mcp` bindings side by
side. This checker is what makes that file a source rather than a description.

The pairing to hold in mind:

    check_cli_commands   the HOW — every node has `__main__.py` + `commands()`, the
                         listing matches, the parser is built but not run at import
    check_surface        the WHAT — the verbs that exist are the verbs that were
                         specified, and the specified ones exist

Neither substitutes for the other. A tree can satisfy every structural rule in §3 and
still offer a verb nobody designed, or be missing one that was. Conversely a tree can
match its spec exactly and still build the parser at import time. The two failures have
nothing to do with each other, which is why they are two checkers.

## Closure runs in both directions

A spec that only required every declared verb to exist would let the tree grow verbs
nobody declared. One that only required every verb to be declared would let the spec keep
rows for verbs long deleted. §17 is blunt about the half-measure:

    a spec without closure in both directions is strictly worse than the derivation
    racecar already has: it adds a place to lie and removes nothing.

## The spec is the source; the tree is what changes

When the two disagree, this checker reports the TREE as wrong. That direction is not
politeness. A spec its implementer may edit to match what they built is not a spec, it is
a transcript, and it cannot fail.

## Applicability

Repos with no `api/surface.jsonl` are skipped, not failed. Declaring a surface spec is a
choice a package makes; §19 does not require one, and a checker that failed every repo
without one would be enforcing a rule nobody wrote.

Usage:
    python scripts/check_surface.py                 # discover src/<pkg>
    python scripts/check_surface.py --root <path>   # grade another repo
    python scripts/check_surface.py --json

Exit 0 when the spec and the tree agree (or no spec exists), 1 on any divergence,
2 on a usage error.

Complexity: O(n + m), n = surface.jsonl rows, m = CLI tree nodes walked
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# The walker runs in a SUBPROCESS with the target's `src` on sys.path. Importing an
# arbitrary repo's package into this process would run its module-scope code inside the
# checker, and a repo whose `__main__.py` does work at import (itself a §3 violation)
# would take the checker down with it instead of being reported.
_WALKER = r"""
import importlib, json, sys
pkg = sys.stdin.read()
found = []
queue = [(pkg, importlib.import_module(pkg + ".__main__"))]
while queue:
    path, node = queue.pop()
    for verb, _ in node.subcommands() if hasattr(node, "subcommands") else []:
        found.append("python -m %s %s" % (path, verb))
    for name, _ in node.commands() if hasattr(node, "commands") else []:
        child = path + "." + name
        queue.append((child, importlib.import_module(child + ".__main__")))
print(json.dumps(sorted(found)))
"""

# `absent` and `broken` are kept apart deliberately. A bare `except Exception: False`
# reads an ImportError inside a module that plainly exists as "not built yet", so a row
# marked `proposed` whose implementation is present but crashing on import is reported as
# conforming. That is the checker agreeing with the spec about a module neither of them
# could load.
_RESOLVER = r"""
import importlib, json, sys
out = {}
for fn in json.loads(sys.stdin.read()):
    module_name, _, attr = fn.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        absent = missing == module_name or module_name.startswith(missing + ".")
        out[fn] = "absent" if absent else "broken:%s" % exc
    except Exception as exc:
        out[fn] = "broken:%s: %s" % (type(exc).__name__, exc)
    else:
        # `fn` may name a MODULE (`pkg.api.package.commit`) rather than a callable on
        # one. `hasattr(parent, "commit")` is False for an unimported submodule and True
        # once anything imports it, so testing only the attribute makes the answer depend
        # on what ran first. Import the full path as a fallback and the answer is stable.
        if hasattr(module, attr):
            out[fn] = "present"
        else:
            try:
                importlib.import_module(fn)
            except ModuleNotFoundError:
                out[fn] = "absent"
            except Exception as exc:
                out[fn] = "broken:%s: %s" % (type(exc).__name__, exc)
            else:
                out[fn] = "present"
print(json.dumps(out))
"""


def find_spec(root: Path) -> tuple[Path, str] | None:
    """Return `(spec path, package name)` for the repo at `root`, or None if it has no spec."""
    src = root / "src"
    if not src.is_dir():
        return None
    for pkg_dir in sorted(p for p in src.iterdir() if (p / "__init__.py").is_file()):
        spec = pkg_dir / "api" / "surface.jsonl"
        if spec.is_file():
            return spec, pkg_dir.name
    return None


def read_rows(spec: Path) -> list[dict[str, object]]:
    """Parse the spec, failing loudly on a malformed line rather than skipping it."""
    rows = []
    for n, line in enumerate(spec.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"check_surface: {spec}:{n}: not valid JSON — {exc}"
            ) from exc
    return rows


def _run(script: str, arg: str, root: Path) -> object:
    """Run one of the probe scripts against the target repo; return its parsed output.

    `arg` travels over the child's STDIN, not as an argv element. It used to be
    `[sys.executable, "-c", script, arg]` -- fine for `_WALKER`'s package name, but
    `_RESOLVER`'s `arg` is a JSON array of every row's `fn`, one entry per spec row, and
    argv has a ceiling `stdin` does not: the OS's `ARG_MAX` (`getconf ARG_MAX`, typically
    a few MB). A spec large enough to approach it raised an unhandled `OSError` from
    `subprocess.run` itself, before this function's own `except subprocess.TimeoutExpired`
    or `proc.returncode != 0` handling ever ran -- the standard fix for that ceiling,
    predating this checker, is the same reason `xargs` exists: move the payload off the
    command line and onto a stream a process reads instead of receives as an argument.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            input=arg,
            capture_output=True,
            text=True,
            cwd=str(root),
            env={**_env(), "PYTHONPATH": str(root / "src")},
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(
            "check_surface: a target module's import hung the probe past 120s "
            f"({exc})"
        ) from exc
    if proc.returncode != 0:
        raise SystemExit(
            f"check_surface: could not walk the CLI tree — {proc.stderr.strip()[:400]}"
        )
    return json.loads(proc.stdout)


def _env() -> dict[str, str]:
    """The parent environment, imported here so the module top stays free of `os`."""
    import os  # pylint: disable=import-outside-toplevel

    return dict(os.environ)


def audit(root: Path) -> tuple[list[str], int]:
    """Return `(findings, rows graded)`; an empty finding list means the tree conforms.

    The two passes below (`declared - built - proposed`, then `built - declared`) are
    plain two-way set difference reconciling desired state (the spec) against actual
    state (the probed tree) — the same shape as a Kubernetes controller's reconcile
    loop, with `proposed` carved out as the one sanctioned lag between the two.
    """
    located = find_spec(root)
    if located is None:
        print("check_surface: no src/<pkg>/api/surface.jsonl — nothing to grade (info)")
        return [], 0
    spec, pkg = located
    rows = read_rows(spec)
    if not rows:
        return [
            f"{spec}: the spec is empty; both closure directions would pass vacuously"
        ], 0

    rel = spec.relative_to(root)
    walked: list[str] = _run(_WALKER, pkg, root)  # type: ignore[assignment]
    built = set(walked)
    declared = {str(r["cli"]) for r in rows}
    # A row's `status` decides whether "declared and not built" is a finding at all.
    proposed = {str(r["cli"]) for r in rows if r.get("status") == "proposed"}
    findings = []

    # A SPEC AHEAD OF ITS IMPLEMENTATION IS NOT A DEFECT. `proposed` is the state of
    # having decided what to build and not yet built it, which is strictly better than
    # not having decided — you know the name of what is missing. Failing on it would make
    # a repo that wrote a spec score worse than one that wrote none, and the way to a
    # green gate would be to delete the plan. Only a row claiming to be built, and not
    # being, is a divergence.
    for verb in sorted(declared - built - proposed):
        findings.append(
            f"{rel}: `{verb}` is declared `exists` and does not. BUILD IT — the spec is "
            "the source; removing the row is not the available fix. (A verb genuinely "
            "not built yet belongs in the spec as `proposed`, which is not a finding.)"
        )
    pending = sorted(proposed - built)
    if pending:
        print(
            f"check_surface: {len(pending)} verb(s) declared and not yet built "
            f"(info, not a finding): {', '.join(pending)}"
        )
    # The mirror of the `fn` axis's stale-`proposed` check below: a verb genuinely
    # built and callable, whose spec row still says `proposed`, is a finding on the
    # `cli` axis too -- the same reconcile-loop shape, just the other resolver.
    for verb in sorted(proposed & built):
        findings.append(
            f"{rel}: `{verb}` is marked `proposed` and now exists — the row needs its "
            "status flipped"
        )
    for verb in sorted(built - declared):
        findings.append(
            f"{rel}: `{verb}` exists and is not declared. Either remove the verb, or have "
            "the spec's owner declare it. Widening the spec to match is the same drift "
            "with the evidence erased."
        )

    resolved = _run(_RESOLVER, json.dumps([str(r["fn"]) for r in rows]), root)
    for row in rows:
        fn, status = str(row["fn"]), row.get("status")
        state = str(resolved[fn])  # type: ignore[index]
        if state.startswith("broken:"):
            findings.append(
                f"{rel}: `{fn}` exists and raised on import — {state[len('broken:'):]}. "
                "Neither `exists` nor `proposed` is true of a module that cannot load."
            )
        elif status == "proposed" and state == "present":
            findings.append(
                f"{rel}: `{fn}` is marked `proposed` and now exists — the row needs its "
                "status flipped"
            )
        elif status != "proposed" and state == "absent":
            findings.append(f"{rel}: `{fn}` is marked `{status}` and does not resolve")
    return findings, len(rows)


def main(argv: list[str] | None = None) -> int:
    """Parse argv, audit the repo, and return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=Path("."), help="Repo to grade (default: CWD)"
    )
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON")
    args = parser.parse_args(argv)

    findings, graded = audit(args.root.resolve())
    if args.json:
        print(json.dumps({"findings": findings, "rows": graded}, indent=2))
    else:
        for f in findings:
            print(f"check_surface: {f}")
        if graded:
            print(f"check_surface: {len(findings)} finding(s) over {graded} row(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
