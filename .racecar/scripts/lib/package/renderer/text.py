"""Every package verb's text view, rendered from the record the verb returns.

`scripts/package.py` and `python -m racecar.package` both hand a verb's record here.
`--json` prints the record itself. Nothing here computes.

Complexity: O(n) in the record's findings
"""

from __future__ import annotations

from lib import not_a_command

_ADVISORY = (
    "Advisory: a shipped commit is the owner's to rewrite or leave "
    "(shared/OWNERSHIP.md). Rewriting published history is /racecar-commit-audit's "
    "second half, and it is a deliberate decision, not a cleanup."
)


def unreadable(exc: Exception) -> str:
    """A plan file `commit` could not read."""
    return f"commit: cannot read the plan: {exc}"


def create_refused() -> str:
    """`create` on the delivered route: it cannot run here, and where it can."""
    return (
        "package: cannot run create — it scaffolds from racecar's templates and renders "
        "the server with racecar's generator, neither of which is delivered; run "
        "`python -m racecar.package create` from a racecar checkout"
    )


def audit(findings: list[dict[str, str]], *, strict: bool) -> str:
    """`audit`: the findings grouped by rule, then the count and the advisory."""
    if not findings:
        return "check_commit_history: OK"
    by_rule: dict[str, list[dict[str, str]]] = {}
    for finding in findings:
        by_rule.setdefault(finding["rule"], []).append(finding)
    lines = []
    for rule in sorted(by_rule):
        lines.append(f"\n  {rule}  ({len(by_rule[rule])})")
        for finding in by_rule[rule]:
            lines.append(f"    {finding['subject']}  {finding['message']}")
    lines.append(f"\ncheck_commit_history: {len(findings)} finding(s)")
    if not strict:
        lines.append(_ADVISORY)
    return "\n".join(lines)


def runbook_written(out: object) -> str:
    """Where `commit` wrote the runbook it is about to run."""
    return f"commit: runbook {out}"


if __name__ == "__main__":
    not_a_command()
