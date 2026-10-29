"""check-json: what each command's `--json` actually returns, found by running it.

Every command in the repo's `surface.jsonl` is listed, and each one the spec marks `read`
(a verb that changes nothing, `arch-python/SURFACES.md`) is run once, with
`--json`, from the repo root. `read` says
nothing about arguments: a verb that requires one answers a bare run with its help on stderr,
`<prog>: needs <arguments>` as the last line, and exit 2 (`arch-python/CLI.md` B2). That
answer gets no verdict; the record says what the verb needs. A write or job verb is never run.
In a repo with no `surface.jsonl`, the commands its code builds are listed and none is run.

One record per command, `{noun, verb, json, exit, needs}`:

- `noun`, `verb`: the command, as the lexicon names it; the package root's verbs are under
  the package name.
- `json`: true when stdout parses as JSON, whatever the document (`[]` and `{}` included);
  false when it printed something that does not parse; null when it printed nothing, or
  answered with B2's help for a missing argument.
- `exit`: the exit code; null when the command was not run, or did not finish in time.
- `needs`: the arguments B2 said the verb requires, read off its own `needs` line; `[]` when
  it answered without one, or was not run.

Each command runs under the repo's own interpreter where it has one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from lib import not_a_command
from lib.shared import _spec
from lib.shared._python import repo_python

from ._faces import acting_on, default_api
from ._form import offered, package_of
from ._record import FINDINGS, OK
from ._vocab import face, named
from .renderer import text

#: B2's stderr line, `<prog>: needs <arguments>`, as `racecar.lib._cli.VerbParser` prints it.
_NEEDS = ": needs "

#: Seconds one command may run before it counts as returning nothing.
TIMEOUT = 300
_WORKERS = 4


def _pair(module: str, verb: str, package: str) -> tuple[str, str]:
    """`racecar.graph.topology` + `check` is `("graph.topology", "check")`."""
    noun = module[len(package) + 1 :] if module != package else package
    return noun, verb


def _commands(root: Path, package: str) -> list[tuple[tuple[str, str], bool]]:
    """`((noun, verb), runnable)` per command: the spec's rows, or the code's tree unrun."""
    spec = _spec.spec_path(root)
    if spec.is_file():
        out = []
        for row in _spec.read_rows(spec):
            parsed_cli = _spec.command(row)
            if parsed_cli is None:
                continue  # not `python -m <module> <verb>`
            runnable = row.get("kind") == "read" and row.get("status") == "exists"
            out.append((_pair(*parsed_cli, package), runnable))
        return out
    return [
        ((noun, verb), False)
        for noun, verbs in sorted(offered(root, package).items())
        for verb in sorted(verbs)
    ]


def parsed(stdout: str) -> bool | None:
    """True for a JSON document, false for other output, null for no output at all."""
    printed = stdout.strip()
    if not printed:
        return None
    try:
        json.loads(printed)
    except ValueError:
        return False
    return True


def _probe(
    root: Path, python: str, package: str, pair: tuple[str, str]
) -> dict[str, Any]:
    noun, verb = pair
    module = package if noun == package else f"{package}.{noun}"
    try:
        done = subprocess.run(
            [python, "-m", module, verb, "--json"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _record(pair, None, None)
    prefix = f"python -m {module} {verb}{_NEEDS}"
    for line in done.stderr.splitlines():
        if line.startswith(prefix):
            return _record(pair, None, done.returncode, line[len(prefix) :].split(", "))
    return _record(pair, parsed(done.stdout), done.returncode)


def verdict(record: dict[str, Any]) -> bool:
    """Whether a record gets a verdict: it ran, and did not stop for a missing argument."""
    return record["exit"] is not None and not record["needs"]


def _record(
    pair: tuple[str, str],
    printed: bool | None,
    code: int | None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "noun": pair[0],
        "verb": pair[1],
        "json": printed,
        "exit": code,
        "needs": list(required or []),
    }


def run(root: Path, surface: str, noun: str | None = None) -> list[dict[str, Any]]:
    """The `check-json` verb on one face: one `{surface, noun, verb, json, exit, needs}` per
    command, in the spec's order. Another face has no command to run."""
    if face(surface) != "cli":
        return []
    return named(surface, probed(root, noun))


def probed(root: Path, noun: str | None = None) -> list[dict[str, Any]]:
    """One `{noun, verb, json, exit, needs}` per cli command, in the spec's order."""
    package = package_of(root)
    listed = [c for c in _commands(root, package) if noun is None or c[0][0] == noun]
    python = repo_python(root)
    # Never itself: marked `read`, it is in the list it runs, and each copy would run the next.
    runnable = [
        pair for pair, run in listed if run and pair != ("surface", "check-json")
    ]
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        found = dict(
            zip(
                runnable, pool.map(lambda p: _probe(root, python, package, p), runnable)
            )
        )
    return [found.get(pair) or _record(pair, None, None) for pair, _ in listed]


def main(
    root: Path,
    surfaces: list[str] | None,
    *,
    noun: str | None = None,
    as_json: bool = False,
    api: Any = None,
) -> int:
    """Run `check-json` on the cli through `api`, then print one line per command; exit 1
    when a command that ran printed no JSON.

    Only the cli has commands to run, so a run that names no cli prints no record, only a
    line on stderr saying so.
    """
    api = api or default_api()
    faces, said = acting_on(root, surfaces, api)
    cli = [one for one in faces if one == "cli"]
    found = [r for one in cli for r in api.check_json(root, one, noun)]
    for line in text.notes(said):
        print(line, file=sys.stderr)
    if not cli:
        print(text.check_json_no_cli(), file=sys.stderr)
        return OK
    failed = [r for r in found if verdict(r) and r["json"] is not True]
    if as_json:
        print(json.dumps(found, indent=2))
    else:
        for line in text.check_json(found):
            print(line)
        judged = sum(map(verdict, found))
        print(text.check_json_tally(judged, len(failed)), file=sys.stderr)
    return FINDINGS if failed else OK


if __name__ == "__main__":
    not_a_command()
