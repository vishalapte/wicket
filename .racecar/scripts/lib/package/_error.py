"""The package noun's exit codes, and the one refusal its verbs raise.

`OK`, `FINDINGS` and `UNMET` are the codes `racecar.lib._exit` fixes (0, 1, 2), restated
here because delivered code runs with no racecar installed and cannot import that module.

`PackageError` is a verb that could not be carried out: its message is the line a person
reads on stderr and its `code` the exit the command answers with. A verb's `run` raises it
rather than printing, so `main` is the one place the line is written.

Complexity: O(1)
"""

from __future__ import annotations

from lib import not_a_command

OK = 0
FINDINGS = 1
UNMET = 2


class PackageError(Exception):
    """A request the package noun could not carry out, and the exit code it answers with."""

    def __init__(self, message: str, code: int = UNMET) -> None:
        """Hold the line a person reads and the code the command exits with."""
        super().__init__(message)
        self.code = code


if __name__ == "__main__":
    not_a_command()
