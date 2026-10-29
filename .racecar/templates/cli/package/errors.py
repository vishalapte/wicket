"""The one exception a surface of __PKG__ catches: a request the api cannot run."""

from __future__ import annotations


class ApiError(Exception):
    """The api refused: bad input, or a target that does not exist.

    Never a finding about the data asked for: an empty result is data, and the api
    returns it. A surface turns this into its own refusal; the cli prints the message on
    stderr and exits with ``exit_code``.
    """

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code
