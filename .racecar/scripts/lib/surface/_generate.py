"""`generate --surface cli --docs`: the README's `## CLI` block, and which verbs lack `output()`.

Both render the CLI code, not the lexicon.

The block is the CLI audit's own rendering (`check_cli_commands.render_tree`), so the README
shows byte for byte what `make arch` audits. The audit runs in a child process with the
repo's `src` on its path: a repo is graded without importing it into this interpreter, and
a second repo's `acme` is never the first one's.

The presweep reports which declared verbs render no schema because their leaf declares no
`output()`, and, separately, which leaves could not be loaded to ask.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import check_cli_commands
from lib import load_file, not_a_command
from lib.shared._root import package_root

from ._conform import declared
from ._error import SurfaceError
from ._faces import acting_on, default_api
from ._form import audit_tree, package_of
from ._record import FINDINGS, OK, rel
from ._vocab import face
from .renderer import text

CLI_BEGIN = "<!-- BEGIN cli-tree (generated) -->"
CLI_END = "<!-- END cli-tree -->"
REGENERATE = "python -m racecar.surface generate --surface cli --docs --apply"


def cli_block(root: Path) -> str:
    """The README's marker-delimited `## CLI` block for the repo at `root`."""
    audit = check_cli_commands
    rendered = list(audit.render_tree(audit_tree(root)))
    return "\n".join(
        [
            CLI_BEGIN,
            "## CLI",
            "",
            "<!-- GENERATED — DO NOT EDIT between the markers. Regenerate:  "
            f"{REGENERATE} -->",
            "",
            "```text",
            *rendered,
            "```",
            CLI_END,
        ]
    )


def _splice(existing: str, section: str) -> str:
    """`existing` with `section` between the markers, by the one splice helper racecar has.

    A README missing either marker comes back unchanged.
    """
    block = importlib.import_module("lib.shared._readme_block")
    spliced: str = block.splice(existing, CLI_BEGIN, CLI_END, section)
    return spliced


def _outputs(source: Path) -> tuple[set[str], str]:
    """The verbs whose schemas the leaf at `source` declares in `output()`, or why unread.

    Loaded by file location rather than by module name, so a repo other than this one is
    read as itself. The `except` is broad because a leaf runs arbitrary code at import, and
    its error is carried out rather than erased.
    """
    name = f"_surface_leaf__{abs(hash(str(source)))}"
    try:
        module = load_file(source, name)
        entries = module.output() if hasattr(module, "output") else []
    except Exception as err:  # pylint: disable=broad-exception-caught
        return set(), f"{type(err).__name__}: {err}"
    if not isinstance(entries, list):
        return set(), f"output() returned {type(entries).__name__}, not a list"
    return {
        str(entry[0].get("phase"))
        for entry in entries
        if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], dict)
    }, ""


def presweep(root: Path) -> tuple[list[str], list[str]]:
    """`(no_output, unreadable)`: declared verbs whose leaf declares no schema for them, and
    leaves that could not be loaded to ask. Reported, never fatal."""
    src = package_root(root) / package_of(root)
    no_output: list[str] = []
    unreadable: list[str] = []
    for noun, meta in declared(root).items():
        leaf = src.joinpath(*([] if meta["root"] else noun.split(".")), "__main__.py")
        if not leaf.is_file():
            continue
        phases, error = _outputs(leaf)
        for verb in sorted(meta["verbs"]):
            if error:
                unreadable.append(f"{noun} {verb}: {error}")
            elif verb not in phases:
                no_output.append(f"{noun} {verb}")
    return no_output, unreadable


def run(root: Path, surface: str, *, apply: bool = False) -> dict[str, Any]:
    """The `generate` verb on one face: its docs, `{surface, readme, state, no_output,
    unreadable}`."""
    return {"surface": face(surface), **docs(root, surface, apply=apply)}


def docs(root: Path, surface: str, *, apply: bool = False) -> dict[str, Any]:
    """Render what `surface` generates as docs: `{readme, state, no_output, unreadable}`.

    For `cli`, the README block and the presweep; `state` is `current`, `stale` or
    `written`. Any other face, or a repo with no CLI to render, generates no docs here,
    which `state: none` says. A README without the markers is left as it is.
    """
    nothing: dict[str, Any] = {
        "readme": None,
        "state": "none",
        "no_output": [],
        "unreadable": [],
    }
    if surface != "cli":
        return nothing
    try:
        package = package_of(root)
    except SurfaceError:
        return nothing  # no single package: there is no CLI tree to render
    if not (package_root(root) / package / "__main__.py").is_file():
        return nothing  # a package with no CLI; `none` is the truthful state
    readme = root / "README.md"
    existing = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    updated = _splice(existing, cli_block(root))
    if updated == existing:
        state = "current"
    elif apply:
        readme.write_text(updated, encoding="utf-8")
        state = "written"
    else:
        state = "stale"
    no_output, unreadable = presweep(root)
    return {
        "readme": str(readme),
        "state": state,
        "no_output": no_output,
        "unreadable": unreadable,
    }


def main(
    root: Path,
    surfaces: list[str] | None,
    *,
    apply: bool = False,
    strict: bool = False,
    as_json: bool = False,
    api: Any = None,
) -> int:
    """Run `generate` on each face through `api`, then print each rendering's state; exit 1
    when one was stale under `strict`."""
    api = api or default_api()
    faces, said = acting_on(root, surfaces, api)
    found = [api.generate(root, one, apply=apply) for one in faces]
    for line in text.notes(said):
        print(line, file=sys.stderr)
    shown = rel(root, found)
    assert isinstance(shown, list)
    stale = [r for r in shown if r["state"] == "stale"]
    if as_json:
        print(json.dumps(shown, indent=2))
    else:
        for line in text.generate(shown):
            print(line)
        print(text.generate_tally(shown), file=sys.stderr)
    return FINDINGS if stale and strict else OK


if __name__ == "__main__":
    not_a_command()
