"""Every surface verb's text view, rendered from the record the verb returns.

`scripts/surface.py` and `python -m racecar.surface` both reach a verb's `main`, which hands
its record here. `--json` prints the record itself.
Each function returns the lines for one stream; the verb's `main` says which stream.

Complexity: O(R) in the record's rows
"""

from __future__ import annotations

from typing import Any

from lib import not_a_command

from .._record import face_line


def notes(said: list[str]) -> list[str]:
    """What a run had to say about the faces it acts on; stderr, before anything else."""
    return [f"surface: {line}" for line in said]


def tally(count: int, what: str) -> str:
    """The closing stderr line of `check` and `update`: how many, or OK when none."""
    return f"surface: {count} {what}" if count else "surface: OK"


def change_line(record: dict[str, str], faces: int = 1) -> str:
    """One `update`/`upgrade` record as the line a person reads."""
    why = f": {record['why']}" if "why" in record else ""
    line = f"{record['action']:<9} {record['kind']}: {record['text']}{why}"
    return face_line(record["surface"], line, faces)


def create(shown: dict[str, list[str]]) -> list[str]:
    """`create`: each path declared, each note, each refusal."""
    return (
        [f"declared  {path}" for path in shown["declared"]]
        + list(shown["notes"])
        + [f"refused   {line}" for line in shown["refused"]]
    )


def create_refused(count: int) -> str:
    """`create`'s stderr line when anything was refused."""
    return (
        f"surface: {count} refused; each line says why, and code "
        "that does not conform is `upgrade`'s to bring into line"
    )


def check(found: list[dict[str, str]], faces: int) -> list[str]:
    """`check`: one line per finding."""
    return [
        face_line(f["surface"], f"{f['kind']}: {f['finding']}", faces) for f in found
    ]


def update(due: list[dict[str, str]], faces: int) -> list[str]:
    """`update`: one line per change due."""
    return [change_line(r, faces) for r in due]


def upgrade(result: dict[str, list[dict[str, str]]], faces: int) -> list[str]:
    """`upgrade`: one line per change made, then one per change left."""
    lines = [
        face_line(change["surface"], f"{change['status']:<9} {change['detail']}", faces)
        for change in result["changed"]
    ]
    return lines + [change_line(record, faces) for record in result["remaining"]]


def upgrade_tally(result: dict[str, list[dict[str, str]]]) -> str:
    """`upgrade`'s stderr line: how many changes were made, and how many are left."""
    return (
        f"surface: {len(result['changed'])} change(s) made, "
        f"{len(result['remaining'])} left"
    )


def upgrade_warning(result: dict[str, list[dict[str, str]]]) -> str | None:
    """`upgrade`'s closing WARNING when the cli still does not conform, else None.

    It warns and stops nothing.
    """
    left = len(result["remaining"])
    if not left:
        return None
    return (
        f"WARNING: the cli does not conform to racecar's canonical form ({left} item(s) "
        "above). Until it does, `surface create` refuses new nouns, and racecar changes "
        "that assume the form can fail when they arrive: a `cli.py` that imports "
        "`lib/error/` stops at import in a package without it. Run `python3 "
        ".racecar/scripts/surface.py upgrade --surface cli`, then the items it leaves."
    )


def listing(found: list[dict[str, str]], faces: int) -> list[str]:
    """`list`: each declared noun and verb, and whether the face binds it, in columns."""
    width = max((len(r["noun"]) for r in found), default=0)
    verbs = max((len(r["verb"]) for r in found), default=0)
    out = []
    for r in found:
        line = f"{r['noun']:<{width}}  {r['verb']:<{verbs}}  {r['built']:<7}  {r['noun_state']}"
        out.append(f"  {face_line(r['surface'], line, faces)}")
    return out


def check_json(found: list[dict[str, Any]]) -> list[str]:
    """`check-json`: one line per command, whether its `--json` printed JSON, and its exit."""
    width = max((len(f"{r['noun']} {r['verb']}") for r in found), default=0)
    out = []
    for r in found:
        said = {True: "json", False: "not json", None: "nothing"}[r["json"]]
        if r["exit"] is None:
            said = "not run"
        elif r["needs"]:
            said = "needs " + ", ".join(r["needs"])
        name = f"{r['noun']} {r['verb']}"
        out.append(f"  {name:<{width}}  {said:<8}  exit {r['exit']}")
    return out


def check_json_tally(judged: int, failed: int) -> str:
    """`check-json`'s stderr line: how many of the commands judged printed no JSON."""
    return f"surface: {failed} of {judged} run did not print JSON under --json"


def check_json_no_cli() -> str:
    """`check-json`'s one stderr line when no cli face is named, so nothing ran."""
    return "surface: check-json runs cli commands, and no cli is named"


def generate(shown: list[dict[str, Any]]) -> list[str]:
    """`generate`: each face's README block state, and its presweep."""
    out = []
    for r in shown:
        if r["state"] in ("stale", "written"):
            done = "would be rewritten" if r["state"] == "stale" else "rewritten"
            out.append(f"  {r['state']:<9} {r['readme']}  (## CLI block {done})")
        for leaf in r["unreadable"]:
            out.append(f"  output() unreadable  {leaf}")
        if r["no_output"]:
            out.append(
                f"  no output()  {len(r['no_output'])} verb(s) render no schema: "
                + ", ".join(r["no_output"])
            )
    return out


def generate_tally(shown: list[dict[str, Any]]) -> str:
    """`generate`'s stderr line: each face and the state its rendering is in."""
    states = ", ".join(f"{r['surface']} {r['state']}" for r in shown)
    return f"surface generate --docs: {states or 'nothing to do'}"


def refusal(label: str, err: object) -> str:
    """One line naming what could not run and why: `<label>: <reason>`."""
    return f"{label}: {err}"


if __name__ == "__main__":
    not_a_command()
