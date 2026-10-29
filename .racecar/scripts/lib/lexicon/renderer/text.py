"""Every lexicon verb's text view, rendered from the record the verb returns.

`scripts/lexicon.py` and `python -m racecar.lexicon` both call a verb for its record and hand
the record here; only this module prints. `--json` prints the record itself. Nothing here
computes: every number a line shows is already in the record.

Complexity: O(n) in the record's rows
"""

from __future__ import annotations

from typing import Any

from lib import not_a_command


def finding(found: dict[str, str]) -> str:
    """One finding record as the line a person reads: `subject: message`."""
    return f"{found['subject']}: {found['message']}"


def listing(table: list[dict[str, Any]]) -> str:
    """`list`: one line per (domain, noun, verb, params) row, then the count."""
    lines = [
        f"  {row['domain']:10s} {row['noun']:14s} {row['verb'] or '-':14s} "
        + " ".join("--" + p for p in row["params"])
        for row in table
    ]
    lines.append(f"lexicon: {len(table)} (domain, noun, verb, params) row(s)")
    return "\n".join(line.rstrip() for line in lines)


def words(table: list[dict[str, Any]], kinds: list[str]) -> str:
    """`list --kind`: one line per word, then the count."""
    lines = []
    for row in table:
        extra = " ".join(f"{k}={v}" for k, v in row["fields"].items())
        lines.append(
            f"  {row['domain']:10s} {row['kind']:10s} {row['name']:20s} "
            f"{row['noun'] or '-':16s} {extra}".rstrip()
        )
    lines.append(f"lexicon: {len(table)} node(s) of kind {', '.join(kinds)}")
    return "\n".join(lines)


def matrix(rows: list[dict[str, Any]]) -> str:
    """`check --answers`: one line per (tuple, check). `.` passed, `!` has a gap beneath it."""
    lines = []
    seen = ""
    for row in rows:
        if row["tuple"] != seen:
            seen = row["tuple"]
            lines.append("")
            lines.append(f"  {seen}")
        mark = "." if row["ok"] else "!"
        lines.append(
            f"    {mark} {row['check']:12s} {', '.join(row['implemented'])[:60]}"
        )
        for gap in row["missing"]:
            lines.append(f"      -> {gap}")
    checks = len({r["check"] for r in rows})
    tuples = len({r["tuple"] for r in rows})
    unmet = sum(1 for r in rows if not r["ok"])
    lines.append("")
    lines.append(
        f"lexicon: {len(rows)} answer(s) over {tuples} tuple(s) x {checks} check(s), "
        f"{unmet} with a gap"
    )
    return "\n".join(lines)


def _cell(declared: int, found: int | None) -> str:
    """One `count marker` cell, five columns wide, the marker last.

    `=` matches the lexicon, `>` carries more, `<` carries less, and `-` means the route does
    not exist here.
    """
    if found is None:
        return "      -"[-5:]
    return f"{found:>3} " + (
        "=" if found == declared else (">" if found > declared else "<")
    )


def coverage(nouns: list[dict[str, Any]], selected: list[str]) -> str:
    """The coverage table: one row per noun, the lexicon's counts beside each route's."""
    if not nouns:
        return ""
    lines = [
        f"lexicon: {', '.join(selected)}",
        f"{'noun':<31}{'verbs':>5} | {'bin':>5} {'cli':>5} "
        f"{'params':>15} | {'bin':>5} {'cli':>5}",
    ]
    for row in nouns:
        cells = (
            _cell(row["verbs"], row["verbs_bin"]),
            _cell(row["verbs"], row["verbs_cli"]),
            _cell(row["params"], row["params_bin"]),
            _cell(row["params"], row["params_cli"]),
        )
        # Column one, and only column one: a reader scans the left edge.
        lead = "!" if any(c[-1] in "<>" for c in cells) else " "
        lines.append(
            (
                f"{lead} {row['noun']:<30}{row['verbs']:>4} | {cells[0]} {cells[1]}"
                f" {row['params']:>15} | {cells[2]} {cells[3]}"
            ).rstrip()
        )
    # The legend goes below the table.
    lines += [
        "",
        "  bin  scripts/<noun>.py      cli  python -m <pkg>.<noun>",
        "  =    matches the lexicon    >    carries more    <    carries less",
        "  -    no such route here     !    this row does not reconcile (column 1)",
    ]
    return "\n".join(lines)


def check_notes(record: dict[str, Any], *, answers: bool = False) -> str:
    """What `check` says before its verdict: the answers, what it stubbed, shadowed kinds.

    Separate from `check` because under `--json` these still reach a person, on stderr,
    while stdout carries the record.
    """
    lines = []
    if answers:
        lines.append(matrix(record["answers"]))
    lines += [f"  stubbed  {path}" for path in record["stubbed"]]
    lines += [
        f"lexicon: kind {kind!r} is declared in more than one corpus — {loser} is "
        "shadowed by the first joined corpus (info, not a finding)"
        for kind, loser in record["shadowed"]
    ]
    return "\n".join(lines)


def check(
    record: dict[str, Any], *, answers: bool = False, strict: bool = False
) -> str:
    """`check`: its notes, the coverage table, every finding shown, then the verdict.

    A finding is shown when it is Major, or any finding under `strict`. With nothing
    declared in the named domains there is nothing to grade, and it says so.
    """
    selected = record["domains"]
    if not record["graded"]:
        return f"lexicon: nothing declared in {', '.join(selected)} — nothing to check"
    lines = [part for part in (check_notes(record, answers=answers),) if part]
    table = coverage(record["nouns"], selected)
    if table:
        lines.append(table)
    shown = [f for f in record["findings"] if f["severity"] == "Major" or strict]
    lines += [f"  {f['severity']:<8} {finding(f)}" for f in shown]
    if shown:
        where = "domains" if len(selected) > 1 else "domain"
        lines.append(
            f"lexicon: {len(shown)} finding(s) in {where} {', '.join(selected)}"
        )
    else:
        skipped = record["not_graded"]
        lines.append(
            "lexicon: OK" + (f" — not graded: {'; '.join(skipped)}" if skipped else "")
        )
    return "\n".join(lines)


def create(record: dict[str, list[str]]) -> str:
    """`create`: each path written or planned, then what happened."""
    if "declared" in record:
        made = record["declared"]
        if not made:
            return "lexicon: already declared; nothing changed"
        return "\n".join(f"  declared  {path}" for path in made)
    applied = "created" in record
    made = record["created" if applied else "would_create"]
    lines = [f"  created  {path}" for path in made]
    lines.append(
        f"lexicon create: {len(made)} path(s) {'created' if applied else 'would be created'}"
    )
    return "\n".join(lines)


def derive(record: dict[str, list[str]], *, apply: bool = False) -> str:
    """`derive`: the create commands it would run, or the paths running them wrote."""
    commands = record["commands"]
    if apply:
        lines = [f"  declared  {path}" for path in record["declared"]]
        lines.append(f"lexicon derive: {len(commands)} create(s) run")
        return "\n".join(lines)
    lines = list(commands)
    lines.append(
        f"lexicon derive: {len(commands)} create(s) would run; --apply runs them"
        if commands
        else "lexicon derive: the lexicon declares everything the cli implements"
    )
    return "\n".join(lines)


def refusal(label: str, err: object) -> str:
    """One line naming what could not run and why: `<label>: <reason>`."""
    return f"{label}: {err}"


def cannot_run(err: object) -> str:
    """The line for a run the lexicon could not do at all."""
    return f"lexicon: cannot run — {err}"


def no_lexicon(root: object) -> str:
    """The line for a repo that holds no lexicon, so there is nothing to grade."""
    return f"lexicon: no lexicon in {root} — nothing to check"


def wrote(destination: object) -> str:
    """The confirmation that the JSON document went to a file rather than stdout."""
    return f"lexicon: wrote {destination}"


if __name__ == "__main__":
    not_a_command()
