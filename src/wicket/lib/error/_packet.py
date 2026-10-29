"""The error packet's types, and `packet()`, which builds one that is valid.

Every face of a racecar program reports a failure as one `ErrorPacket`: the CLI
on stderr under `--json`, REST inside RFC 9457 problem details, MCP as an
error result. The shape and the per-code facts are declared once, in
`scripts/error.json`; `_schema.py` is that file as data, and this module reads
it rather than restating it.

Standard library only, and copied byte for byte into each package's
`lib/error/`, so a program reports errors the same way wherever it is
installed. Do not edit the copy: `upgrade` replaces it.
"""

from __future__ import annotations

from typing import Any, NamedTuple, TypedDict, Unpack

from ._schema import SCHEMA


class Expected(TypedDict, total=False):
    """What would have been accepted: exactly one of the three keys."""

    type: str
    choices: list[str]
    count: int


class Param(TypedDict):
    """One thing at fault: a param, or a statement about no one param."""

    pointer: str | None
    value: Any
    expected: Expected | None
    suggestions: list[str] | None
    detail: str | None


class Request(TypedDict):
    """The network request an error came from."""

    base_url: str
    method: str
    path: str
    status: int | None


class ErrorPacket(TypedDict):
    """One failure, on any face. `scripts/error.json` describes each field."""

    category: str
    code: str
    upstream_code: str | None
    noun: str
    verb: str | None
    params: list[Param]
    request: Request | None
    retry_after: int | None
    message: str
    doc_url: str | None


class Code(NamedTuple):
    """What the schema fixes for one code."""

    category: str
    exit: int
    statuses: tuple[int, ...]
    retryable: bool


def _codes(schema: dict[str, Any]) -> dict[str, Code]:
    return {
        entry["const"]: Code(
            entry["x-category"],
            entry["x-exit"],
            tuple(entry["x-status"]),
            entry["x-retryable"],
        )
        for entry in schema["$defs"]["code"]["oneOf"]
    }


#: Every code, read from the schema: the one table, not a second one.
CODES: dict[str, Code] = _codes(SCHEMA)

#: Which schema this copy holds; `upgrade` compares it with racecar's.
SCHEMA_ID: str = SCHEMA["$id"]


def param(
    pointer: str | None,
    *,
    value: Any = None,
    expected: Expected | None = None,
    suggestions: list[str] | None = None,
    detail: str | None = None,
) -> Param:
    """One entry of `ErrorPacket.params`.

    `pointer` is a JSON Pointer into the verb's input (`#/mode`), or None for a
    statement about no one field. Pass no `value` for a secret.
    """
    return {
        "pointer": pointer,
        "value": value,
        "expected": expected,
        "suggestions": suggestions,
        "detail": detail,
    }


class Fields(TypedDict, total=False):
    """What a caller may set beyond the code, the noun and the message."""

    verb: str | None
    params: list[Param]
    request: Request | None
    retry_after: int | None
    upstream_code: str | None


def packet(
    code: str,
    *,
    noun: str,
    message: str,
    **fields: Unpack[Fields],
) -> ErrorPacket:
    """An `ErrorPacket` for `code`, its category and `doc_url` filled in.

    Raises `ValueError` for a code the schema does not declare.
    """
    if code not in CODES:
        raise ValueError(f"{code!r} is not an error code in {SCHEMA_ID}")
    return {
        "category": CODES[code].category,
        "code": code,
        "upstream_code": fields.get("upstream_code"),
        "noun": noun,
        "verb": fields.get("verb"),
        "params": list(fields.get("params") or []),
        "request": fields.get("request"),
        "retry_after": fields.get("retry_after"),
        "message": message,
        "doc_url": f"{SCHEMA_ID}#{code}",
    }
