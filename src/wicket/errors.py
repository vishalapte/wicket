"""The one exception a surface of wicket catches: a request the api cannot run.

Every refusal names a code, its ``reason``, from ``error-codes.jsonl`` beside this module
(package data, read once into `CODES`): one JSON object per line, ``reason``, ``exit``,
``when`` and ``fix``. The file is the one home of wicket's closed list of refusals, of the
exit code each one returns, and of the advice each one prints. It is authored, never
generated from the code; a test holds the two in step.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib import resources
from typing import NamedTuple, ParamSpec, TypeVar

from wicket.env import event, set_process_account

CODES_FILENAME = "error-codes.jsonl"

# The code of a refusal whose class names none: the arguments conflict or a value is bad.
BAD_REQUEST = "bad_request"

# A placeholder in a code's ``fix``: ``<account>``, filled from the refusal's own facts.
PLACEHOLDER_RE = re.compile(r"<([a-z_]+)>")

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)


class Code(NamedTuple):
    """One line of ``error-codes.jsonl``: a refusal's code, its exit, when, and its fix."""

    reason: str
    exit: int
    when: str
    fix: str


def _load_codes() -> dict[str, Code]:
    """Every line of ``error-codes.jsonl``, by reason. Read once, at import."""
    text = resources.files("wicket").joinpath(CODES_FILENAME).read_text("utf-8")
    codes: dict[str, Code] = {}
    for line in text.splitlines():
        if line.strip():
            entry = json.loads(line)
            codes[entry["reason"]] = Code(
                str(entry["reason"]),
                int(entry["exit"]),
                str(entry["when"]),
                str(entry["fix"]),
            )
    return codes


CODES: dict[str, Code] = _load_codes()


class ApiError(Exception):
    """The api refused: bad input, or a target that does not exist.

    Never a finding about the data asked for: an empty result is data, and the api
    returns it. A surface turns this into its own refusal; the cli prints it on stderr
    and exits with ``exit_code``.

    ``reason`` is a line of ``error-codes.jsonl`` (`CODES`); an unlisted one raises
    ``LookupError`` here, so no refusal leaves with a code the file does not hold.
    ``exit_code`` is that line's ``exit``: no raise site states one, and passing one
    raises ``TypeError``. ``facts`` are what the code's ``fix`` and the log line name
    (``account``, ``cause``, ``lock_pid``...); a placeholder in the fix with no fact
    raises ``LookupError`` here, so no ``<placeholder>`` ever reaches a terminal. The
    text is ``message`` (what happened), then ``fix: <the code's fix>``.
    """

    def __init__(
        self, message: str, reason: str = BAD_REQUEST, **facts: object
    ) -> None:
        if reason not in CODES:
            raise LookupError(f"refusal reason {reason!r} is not in {CODES_FILENAME}")
        if "exit_code" in facts:
            raise TypeError("an exit code comes from error-codes.jsonl, never a caller")
        missing = [
            name
            for name in PLACEHOLDER_RE.findall(CODES[reason].fix)
            if facts.get(name) is None
        ]
        if missing:
            raise LookupError(
                f"refusal {reason!r} lacks the facts its fix names: {missing}"
            )
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.facts = facts
        self.exit_code = CODES[reason].exit

    def fix(self) -> str:
        """The code's fix, each placeholder filled from ``facts`` (every one has a fact)."""
        return PLACEHOLDER_RE.sub(
            lambda match: str(self.facts[match.group(1)]), CODES[self.reason].fix
        )

    def __str__(self) -> str:
        return f"{self.message}\nfix: {self.fix()}"


def _reason_of(exc: BaseException) -> str:
    """The code ``exc``'s class names (its ``reason`` class attribute), else bad_request.

    Read off the class, never the instance: ``UnicodeDecodeError`` and ``ssl.SSLError``
    carry a ``reason`` attribute of their own that is no refusal code.
    """
    reason = getattr(type(exc), "reason", None)
    return reason if isinstance(reason, str) else BAD_REQUEST


def refused(exc: BaseException, message: str | None = None) -> ApiError:
    """``exc`` as an ``ApiError``: its code and its facts, and its text unless ``message``."""
    facts = getattr(exc, "facts", None)
    return ApiError(
        str(exc) if message is None else message,
        _reason_of(exc),
        **(facts if isinstance(facts, dict) else {}),
    )


@contextmanager
def refusing(*kinds: type[Exception]) -> Iterator[None]:
    """Re-raise any of ``kinds`` (default ``ValueError``) as an ``ApiError`` (`refused`)."""
    caught = kinds or (ValueError,)
    try:
        yield
    except caught as exc:
        raise refused(exc) from exc


def refusals(func: Callable[P, R]) -> Callable[P, R]:
    """Wrap an api request function: an ``ApiError`` it raises is logged, then re-raised.

    The log record is event-only (``wicket.end outcome=refused`` with the exit, the code
    and the facts a log tool needs), so it reaches ``wicket.log`` and never the terminal,
    where the face prints the refusal's own text. Every surface calls the api, so every
    surface's refusals are logged here, once. Each request starts with the process
    account cleared (`env.set_process_account`), so a long-lived process never carries
    one request's account into the next. It is not cleared at the end: the renderer's
    events after the request returns, and the excepthook's ``wicket.end
    outcome=crashed`` line after a crash, still name it.
    """

    @functools.wraps(func)
    def request(*args: P.args, **kwargs: P.kwargs) -> R:
        set_process_account(None)
        try:
            return func(*args, **kwargs)
        except ApiError as exc:
            facts = exc.facts
            account = facts.get("account")
            logger.error(
                "",
                extra=event(
                    "wicket.end",
                    account=None if account is None else str(account),
                    outcome="refused",
                    exit=exc.exit_code,
                    reason=exc.reason,
                    cause=facts.get("cause"),
                    since=facts.get("since"),
                    lock_pid=facts.get("lock_pid"),
                ),
            )
            raise

    return request
