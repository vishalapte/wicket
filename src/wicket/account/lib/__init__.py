"""The account worker: what the api and the face share, with no knowledge of who calls it.

The account's rules (list, create and delete an ``imap.json`` link) are decisions, so
they live in ``api``; this holds the refusal they raise, which the face also reads. No
argument parsing, no printing.
"""

from __future__ import annotations

from wicket.errors import ApiError


class LinkRefused(ApiError):
    """``create`` or ``delete`` refused: ``code`` is its reason, which is also the error
    packet's code for it (``conflict`` or ``not_found``)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message, code)

    @property
    def code(self) -> str:
        """The refusal's reason, as the error packet names it."""
        return self.reason
