"""A checker's findings, collected in order and printed as `<prog>: <severity>: <message>`.

Three severities. An error gates: `emit` returns 1 when one was recorded. A warning and an
info note are printed and gate nothing. The difference between those two is what `OK`
means: a run with a warning is not OK, and says how many there were, while an info note
is context the reader may skip.

Complexity: O(n) in findings
"""

from __future__ import annotations


class Findings:
    """Severity-tagged findings, in the order they were recorded."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def error(self, msg: str) -> None:
        """Record a finding that fails the run."""
        self.entries.append(("error", msg))

    def warning(self, msg: str) -> None:
        """Record a finding the reader should act on that does not fail the run."""
        self.entries.append(("warning", msg))

    def info(self, msg: str) -> None:
        """Record a note that neither fails the run nor stops it being OK."""
        self.entries.append(("info", msg))

    @property
    def error_count(self) -> int:
        """Number of errors recorded."""
        return sum(1 for sev, _ in self.entries if sev == "error")

    @property
    def warning_count(self) -> int:
        """Number of warnings recorded."""
        return sum(1 for sev, _ in self.entries if sev == "warning")


def emit(f: Findings, prog: str, tail: str = "") -> int:
    """Print every finding, then `OK` or the counts; 1 if any error was recorded, else 0.

    `tail` follows the counts on the same line when the run is not OK: a checker whose
    errors all have one remedy says it once there rather than on every line.
    """
    for severity, msg in f.entries:
        print(f"{prog}: {severity}: {msg}")
    if not f.error_count and not f.warning_count:
        print(f"{prog}: OK")
        return 0
    counts = f"{f.error_count} errors"
    if f.warning_count:
        counts += f", {f.warning_count} warnings"
    print(f"{prog}: {counts}{tail}")
    return 1 if f.error_count else 0
