"""The Finding audit-result model."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Finding:
    """One audit result: a severity, the file it concerns, a rule id, and a message."""

    severity: str  # "Blocker" or "Finding"
    file: str
    rule: str
    message: str

    def render(self) -> str:
        """Format this finding as one fixed-width audit line."""
        return f"  {self.severity:7s}  {self.file:32s}  {self.rule:42s}  {self.message}"
