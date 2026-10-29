"""How racecar reads an on/off switch that makes a check stricter.

`RACECAR_STRICT` is how an owner delegates stopping to a check: a finding that only
reports by default becomes fatal. It reads `false if not true`, so one spelling turns it
on and anything else leaves it off (`shared/ACCEPTANCE.md`, A3).

Complexity: O(1)
"""

from __future__ import annotations

import os

STRICT_ENV = "RACECAR_STRICT"


def strict() -> bool:
    """Whether this run has been asked to make its advisory findings fatal."""
    return os.environ.get(STRICT_ENV, "").strip().lower() == "true"
