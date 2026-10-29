"""Derive the perimeter from the delivered checkers, and place what the tree lacks.

The node set is derived from the code: a declaration exists because a checker reads it.
The hand-written half of a node is which principle it serves, its class, and its
reasoning.

Access chains are walked, not just leaf names: `data["tool"]["racecar"]["lexicon"]
.get("corpora")` yields the site `[tool.racecar.lexicon].corpora`, so a new key can be
filed under the group that already owns its table.

    main(data)              report the gap
    main(data, write=True)  create the placeable nodes; list the rest

Part of `lib.graph`; `scripts/graph.py` parses the command line and calls `main`. `run`
places every derived key the tree lacks and returns the placement as a record;
`renderer.text` is how the record reads.

Complexity: O(C) in delivered checkers -- one AST parse each.
"""

from __future__ import annotations

import ast
import collections
import json
import re
import sys
from pathlib import Path
from typing import Any

from lib import not_a_command
from lib.graph.renderer import text
from lib.shared._as_json import run_json
from lib.shared._root import find_repo_root
from lib.topology._walk import load

ROOT = find_repo_root(Path(__file__).resolve())
META_ROOT = ROOT / "architecture"
FOREIGN = {
    "black",
    "isort",
    "mypy",
    "pylint",
    "hatch",
    "importlinter",
    "profile",
    "strict",
    "python_version",
    "target-version",
    "disable",
    "requires",
    "requires-python",
    "project",
    "dependencies",
    "dependency-groups",
    "optional-dependencies",
    "build-system",
    "tool",
    "build-backend",
    "known_first_party",
    "src_paths",
    "ignore-paths",
    "dev",
    "django",
    "runtime",
}
INTERNAL = {
    "children",
    "orphan",
    "violations",
    "flags",
    "desc",
    "help",
    "nargs",
    "choices",
    "args",
    "required",
    "pattern",
    "action",
    "command",
    "subcommands",
    "pkg",
    "role",
    "findings",
    "default",
    "api",
    "lib",
    "name",
    "path",
    "kind",
    "type",
    "key",
    "value",
    "module",
    "view",
    "id",
    "text",
    "file",
    "line",
    # A field of racecar's own delivery manifest, read by `check_content_blind.py`; not
    # something an adopter declares.
    "source",
}


def chain(node: ast.AST) -> list[str]:
    """The literal access path leading here, outermost first.

    Both spellings are walked: `d["tool"]["racecar"]` and `d.get("tool", {}).get(...)`.
    """
    out: list[str] = []
    cur = node
    while True:
        if isinstance(cur, ast.Subscript) and isinstance(cur.slice, ast.Constant):
            out.append(str(cur.slice.value))
            cur = cur.value
        elif (
            isinstance(cur, ast.Call)
            and isinstance(cur.func, ast.Attribute)
            and cur.func.attr == "get"
            and cur.args
            and isinstance(cur.args[0], ast.Constant)
        ):
            out.append(str(cur.args[0].value))
            cur = cur.func.value
        else:
            break
    return list(reversed(out))


def derive() -> dict[str, dict[str, Any]]:
    """key -> {scripts, sites}. Read fresh from the delivered set, never cached."""
    # Each manifest line is a JSON object; `source` is the file in this repo.
    source = ROOT / "scripts/racecar-manifest.jsonl"
    if not source.is_file():
        # racecar's own delivery manifest, which only a racecar checkout holds.
        raise FileNotFoundError(
            f"{source} is not here: this reads racecar's own delivered checkers, which "
            "only a racecar checkout holds"
        )
    manifest = source.read_text().splitlines()
    sources = [str(json.loads(l).get("source", "")) for l in manifest if l.strip()]
    named = [s for s in sources if s.endswith(".py")]
    files = [ROOT / s for s in named if (ROOT / s).is_file()]
    # A floor: reading zero checkers would report "0 absent" and exit 0, so it is refused.
    if len(files) < len(named):
        missing = [s for s in named if not (ROOT / s).is_file()]
        raise SystemExit(
            f"perimeter: {len(missing)} of {len(named)} manifest sources do not exist "
            f"({', '.join(missing[:3])}{', …' if len(missing) > 3 else ''}). The derived "
            "perimeter would be computed from a partial set and would understate the gap. "
            "Regenerate the manifest:\n"
            "  python -m racecar sync --write-manifest"
        )
    keys: dict[str, dict[str, Any]] = collections.defaultdict(
        lambda: {"scripts": set(), "sites": set()}
    )
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            k = site = None
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get"
                and n.args
                and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str)
            ):
                k = n.args[0].value
                site = ".".join(chain(n.func.value) + [k])
            elif (
                isinstance(n, ast.Subscript)
                and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)
            ):
                k = n.slice.value
                site = ".".join(chain(n))
            if (
                not k
                or k in FOREIGN
                or k in INTERNAL
                or k.startswith("_")
                or k.isupper()
            ):
                continue
            keys[k]["scripts"].add(f.name)
            keys[k]["sites"].add(site or k)
    return keys


def run(data: Path | None = None, *, write: bool = False) -> dict[str, Any]:
    """The derived perimeter against what `data` declares: every key the tree lacks, placed.

    `data` sets only the declared side; `derive()` always reads racecar's own delivered
    checkers.

    The record: `placeable`, `{key, group, scripts}` for each key a group's table already
    owns; `unplaced`, `{key, sites, scripts}` for each that needs a principle; and `wrote`,
    the nodes `write` created, None without it. Raises `NotADirectoryError` when `data` is
    not a directory.
    """
    if data is not None and not data.is_dir():
        raise NotADirectoryError(f"{data} is not a directory")
    nodes = load(data)
    have = {v["name"].replace("-", "_") for v in nodes.values()} | {
        re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", str(v.get("site", "")))[-1]
        for v in nodes.values()
        if v.get("site")
    }
    groups = {nid: n for nid, n in nodes.items() if n["kind"] == "group"}

    # A placeable key always carries the group id it goes under; --write indexes on it.
    placeable: dict[str, tuple[dict[str, Any], str]] = {}
    unplaced: dict[str, dict[str, Any]] = {}
    for k, info in sorted(derive().items()):
        if k.replace("-", "_") in have:
            continue
        best = None
        for site in info["sites"]:
            for gid in groups:
                flat = {
                    x
                    for node in nodes.values()
                    if node.get("serves") == gid
                    for x in re.findall(
                        r"[A-Za-z_][A-Za-z0-9_-]*", str(node.get("site", ""))
                    )
                }
                if any(
                    seg in flat
                    for seg in site.split(".")[:-1]
                    if seg not in ("tool", "racecar")
                ):
                    best = gid
        if best:
            placeable[k] = (info, best)
        else:
            unplaced[k] = info

    made: int | None = None
    if write:
        made = 0
        for k, (info, gid) in placeable.items():
            d = groups[gid]["path"] / k.replace("_", "-")
            if d.exists():
                continue
            d.mkdir()
            scripts = "\n".join(f"  - {s}" for s in sorted(info["scripts"]))
            (d / "README.md").write_text(
                f"---\npnode: [../README.md]\nbearing: draft\n"
                f"site: {sorted(info['sites'])[0]}\n"
                f"derivation: DERIVED — this node exists because a checker reads the key\n"
                f"class: TODO\nchecked_by:\n  python:\n{scripts}\n---\n\n"
                f"# {k.replace('_','-')}\n\n## Notes\n\nTODO.\n",
                encoding="utf-8",
            )
            made += 1
    return {
        "placeable": [
            {"key": k, "group": gid, "scripts": sorted(info["scripts"])}
            for k, (info, gid) in placeable.items()
        ],
        "unplaced": [
            {
                "key": k,
                "sites": sorted(info["sites"]),
                "scripts": sorted(info["scripts"]),
            }
            for k, info in unplaced.items()
        ],
        "wrote": made,
    }


def _show(data: Path | None, *, write: bool) -> int:
    """Run the placement and print it as text; 0, or 1 when `data` is not a directory."""
    try:
        record = run(data, write=write)
    except (NotADirectoryError, FileNotFoundError) as err:
        print(text.refusal("perimeter: error", err), file=sys.stderr)
        return 1
    print(text.perimeter(record))
    return 0


def main(
    data: Path | None = None, *, write: bool = False, as_json: bool = False
) -> int:
    """Report the derived perimeter against what `data` declares; under `as_json` the text
    goes to stderr and stdout carries the exit code, the result this verb declares."""
    return run_json(as_json, lambda: _show(data, write=write))


if __name__ == "__main__":
    not_a_command()
