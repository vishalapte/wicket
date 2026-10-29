"""The error packet's schema as data, generated from `scripts/error.json`.

Do not edit: racecar writes this file from the JSON, and `upgrade` replaces
it when the schema's `$id` moves. The types and helpers that read it are in
`_packet.py`.
"""

from typing import Any

# fmt: off
SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:racecar:schema:error-packet:1",
    "title": "ErrorPacket",
    "description": (
        "The one error every racecar face returns: the cli on stderr under "
        "--json, REST inside RFC 9457 problem details, MCP as an error "
        "result's structuredContent, and a describe verb's `error` field. A "
        "consumer rebuilds the packet from this schema alone."
    ),
    "type": "object",
    "required": [
        "category",
        "code",
        "upstream_code",
        "noun",
        "verb",
        "params",
        "request",
        "retry_after",
        "message",
        "doc_url",
    ],
    "additionalProperties": False,
    "properties": {
        "category": {
            "enum": ["usage", "refused", "server", "transport", "local"],
            "description": (
                "usage: the request was malformed. refused: well formed, and "
                "the api declined it. server: the server failed running it. "
                "transport: the request or its reply did not get through. "
                "local: the failure is on the caller's own machine."
            ),
        },
        "code": {"$ref": "#/$defs/code"},
        "upstream_code": {
            "type": ["string", "null"],
            "description": (
                "The server's own code, verbatim, when a server code was "
                "mapped onto `code`."
            ),
        },
        "noun": {
            "type": "string",
            "description": (
                "artifact; for a root verb (login, describe), the package name"
            ),
        },
        "verb": {
            "type": ["string", "null"],
            "description": "get; null for unknown_verb",
        },
        "params": {
            "type": "array",
            "items": {"$ref": "#/$defs/param"},
            "description": (
                "What is at fault; empty when nothing is. All entries, except "
                "for required_one_of_params, where any one will do."
            ),
        },
        "request": {
            "oneOf": [{"type": "null"}, {"$ref": "#/$defs/request"}],
            "description": (
                "The network request this error came from; null when none was "
                "made."
            ),
        },
        "retry_after": {
            "type": ["integer", "null"],
            "minimum": 0,
            "description": (
                "Seconds to wait before retrying (a 429's Retry-After); null "
                "when not given."
            ),
        },
        "message": {
            "type": "string",
            "description": "One sentence, for a person.",
        },
        "doc_url": {
            "type": ["string", "null"],
            "description": (
                "The schema's $id at the code's anchor, e.g. "
                "urn:racecar:schema:error-packet:1#rate_limited: absolute, so "
                "it resolves against any copy of the schema a consumer holds."
            ),
        },
    },
    "allOf": [
        {
            "if": {
                "properties": {
                    "code": {
                        "enum": [
                            "required_param",
                            "required_one_of_params",
                            "required_param_value",
                            "invalid_param_value",
                            "ambiguous_param",
                            "conflicting_params",
                            "unknown_param",
                            "unknown_verb",
                            "usage",
                        ],
                    },
                },
            },
            "then": {"properties": {"category": {"const": "usage"}}},
        },
        {
            "if": {
                "properties": {
                    "code": {
                        "enum": [
                            "unauthenticated",
                            "forbidden",
                            "not_found",
                            "conflict",
                            "limit_exceeded",
                            "rate_limited",
                            "not_available",
                            "refused",
                        ],
                    },
                },
            },
            "then": {"properties": {"category": {"const": "refused"}}},
        },
        {
            "if": {
                "properties": {
                    "code": {"enum": ["server_error", "unavailable"]},
                },
            },
            "then": {"properties": {"category": {"const": "server"}}},
        },
        {
            "if": {
                "properties": {
                    "code": {
                        "enum": [
                            "unreachable",
                            "tls_failed",
                            "timeout",
                            "redirect_refused",
                            "not_problem",
                            "not_json",
                        ],
                    },
                },
            },
            "then": {"properties": {"category": {"const": "transport"}}},
        },
        {
            "if": {"properties": {"code": {"enum": ["local_error"]}}},
            "then": {"properties": {"category": {"const": "local"}}},
        },
    ],
    "$defs": {
        "param": {
            "type": "object",
            "required": [
                "pointer",
                "value",
                "expected",
                "suggestions",
                "detail",
            ],
            "additionalProperties": False,
            "properties": {
                "pointer": {
                    "type": ["string", "null"],
                    "description": (
                        "A JSON Pointer into the verb's input (#/mode, "
                        "#/content/svc/display_name); the cli prints a "
                        "top-level one as --mode. null for an error about no "
                        "one field, and for unknown_verb."
                    ),
                },
                "value": {
                    "description": (
                        "What the caller sent, as any JSON value; always null "
                        "for a secret (a param whose input_schema says "
                        "writeOnly: true)."
                    ),
                },
                "expected": {
                    "oneOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "required": ["type"],
                            "additionalProperties": False,
                            "properties": {"type": {"type": "string"}},
                        },
                        {
                            "type": "object",
                            "required": ["choices"],
                            "additionalProperties": False,
                            "properties": {
                                "choices": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                        {
                            "type": "object",
                            "required": ["count"],
                            "additionalProperties": False,
                            "properties": {
                                "count": {"type": "integer", "minimum": 1},
                            },
                        },
                    ],
                    "description": "What would have been accepted.",
                },
                "suggestions": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "Close matches to retry with.",
                },
                "detail": {
                    "type": ["string", "null"],
                    "description": (
                        "What is wrong with this entry, for a person; the "
                        "whole statement when pointer is null."
                    ),
                },
            },
        },
        "request": {
            "type": "object",
            "required": ["base_url", "method", "path", "status"],
            "additionalProperties": False,
            "properties": {
                "base_url": {
                    "type": "string",
                    "description": (
                        "Which server: http://localhost:8000 or production."
                    ),
                },
                "method": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": (
                        "The request path with its query string, e.g. "
                        "/site/restore?mode=replace."
                    ),
                },
                "status": {
                    "type": ["integer", "null"],
                    "description": (
                        "The reply's HTTP status; null when no reply came "
                        "back."
                    ),
                },
            },
        },
        "code": {
            "oneOf": [
                {
                    "const": "required_param",
                    "$anchor": "required_param",
                    "x-category": "usage",
                    "x-exit": 2,
                    "x-status": [400],
                    "x-retryable": False,
                    "description": "A param the verb needs is missing.",
                },
                {
                    "const": "required_one_of_params",
                    "$anchor": "required_one_of_params",
                    "x-category": "usage",
                    "x-exit": 2,
                    "x-status": [400],
                    "x-retryable": False,
                    "description": (
                        "One of a group of params is needed and none was "
                        "given; params lists the group, any one will do."
                    ),
                },
                {
                    "const": "required_param_value",
                    "$anchor": "required_param_value",
                    "x-category": "usage",
                    "x-exit": 2,
                    "x-status": [400],
                    "x-retryable": False,
                    "description": (
                        "A param that takes a value was given none."
                    ),
                },
                {
                    "const": "invalid_param_value",
                    "$anchor": "invalid_param_value",
                    "x-category": "usage",
                    "x-exit": 2,
                    "x-status": [400],
                    "x-retryable": False,
                    "description": (
                        "A value has the wrong type, is not one of the "
                        "choices, or failed the api's validation."
                    ),
                },
                {
                    "const": "ambiguous_param",
                    "$anchor": "ambiguous_param",
                    "x-category": "usage",
                    "x-exit": 2,
                    "x-status": [400],
                    "x-retryable": False,
                    "description": (
                        "An abbreviated flag matches several params."
                    ),
                },
                {
                    "const": "conflicting_params",
                    "$anchor": "conflicting_params",
                    "x-category": "usage",
                    "x-exit": 2,
                    "x-status": [400],
                    "x-retryable": False,
                    "description": "Params that cannot be used together.",
                },
                {
                    "const": "unknown_param",
                    "$anchor": "unknown_param",
                    "x-category": "usage",
                    "x-exit": 2,
                    "x-status": [400],
                    "x-retryable": False,
                    "description": (
                        "A param the verb does not have, including a stray "
                        "value."
                    ),
                },
                {
                    "const": "unknown_verb",
                    "$anchor": "unknown_verb",
                    "x-category": "usage",
                    "x-exit": 2,
                    "x-status": [404],
                    "x-retryable": False,
                    "description": "A word that is not a verb of the noun.",
                },
                {
                    "const": "usage",
                    "$anchor": "usage",
                    "x-category": "usage",
                    "x-exit": 2,
                    "x-status": [400],
                    "x-retryable": False,
                    "description": (
                        "Any other malformed request; read message and "
                        "params[].detail."
                    ),
                },
                {
                    "const": "unauthenticated",
                    "$anchor": "unauthenticated",
                    "x-category": "refused",
                    "x-exit": 2,
                    "x-status": [401],
                    "x-retryable": False,
                    "description": (
                        "No credential, or one the server does not accept: "
                        "fix the key or log in."
                    ),
                },
                {
                    "const": "forbidden",
                    "$anchor": "forbidden",
                    "x-category": "refused",
                    "x-exit": 2,
                    "x-status": [403],
                    "x-retryable": False,
                    "description": (
                        "The credential may not do this, or the verb is off "
                        "here (writes disabled)."
                    ),
                },
                {
                    "const": "not_found",
                    "$anchor": "not_found",
                    "x-category": "refused",
                    "x-exit": 2,
                    "x-status": [404],
                    "x-retryable": False,
                    "description": "The target does not exist.",
                },
                {
                    "const": "conflict",
                    "$anchor": "conflict",
                    "x-category": "refused",
                    "x-exit": 2,
                    "x-status": [409],
                    "x-retryable": False,
                    "description": (
                        "The target already exists or changed underneath the "
                        "request: fetch it, then decide."
                    ),
                },
                {
                    "const": "limit_exceeded",
                    "$anchor": "limit_exceeded",
                    "x-category": "refused",
                    "x-exit": 2,
                    "x-status": [403],
                    "x-retryable": False,
                    "description": (
                        "A quota or safety limit was reached (a plan limit, a "
                        "page-count guard); retrying will not help."
                    ),
                },
                {
                    "const": "rate_limited",
                    "$anchor": "rate_limited",
                    "x-category": "refused",
                    "x-exit": 2,
                    "x-status": [429],
                    "x-retryable": True,
                    "description": (
                        "Too many requests: wait retry_after seconds, then "
                        "retry."
                    ),
                },
                {
                    "const": "not_available",
                    "$anchor": "not_available",
                    "x-category": "refused",
                    "x-exit": 2,
                    "x-status": [501],
                    "x-retryable": False,
                    "description": (
                        "The verb has no endpoint on this face; nothing was "
                        "sent."
                    ),
                },
                {
                    "const": "refused",
                    "$anchor": "refused",
                    "x-category": "refused",
                    "x-exit": 2,
                    "x-status": [422],
                    "x-retryable": False,
                    "description": (
                        "Any other refusal by the api; upstream_code may say "
                        "more."
                    ),
                },
                {
                    "const": "server_error",
                    "$anchor": "server_error",
                    "x-category": "server",
                    "x-exit": 2,
                    "x-status": [500, 502],
                    "x-retryable": False,
                    "description": (
                        "The server failed while running a valid request: "
                        "surface it, do not retry blindly."
                    ),
                },
                {
                    "const": "unavailable",
                    "$anchor": "unavailable",
                    "x-category": "server",
                    "x-exit": 2,
                    "x-status": [503, 504],
                    "x-retryable": True,
                    "description": (
                        "The server is up but cannot serve now (overloaded, "
                        "in maintenance, a gateway timed out): wait "
                        "retry_after seconds if given, then retry."
                    ),
                },
                {
                    "const": "unreachable",
                    "$anchor": "unreachable",
                    "x-category": "transport",
                    "x-exit": 2,
                    "x-status": [],
                    "x-retryable": True,
                    "description": "No connection to the server.",
                },
                {
                    "const": "tls_failed",
                    "$anchor": "tls_failed",
                    "x-category": "transport",
                    "x-exit": 2,
                    "x-status": [],
                    "x-retryable": False,
                    "description": (
                        "The TLS handshake failed: a wrong host or a bad "
                        "certificate, not a server that is down."
                    ),
                },
                {
                    "const": "timeout",
                    "$anchor": "timeout",
                    "x-category": "transport",
                    "x-exit": 2,
                    "x-status": [],
                    "x-retryable": True,
                    "description": "The server did not answer in time.",
                },
                {
                    "const": "redirect_refused",
                    "$anchor": "redirect_refused",
                    "x-category": "transport",
                    "x-exit": 2,
                    "x-status": [],
                    "x-retryable": False,
                    "description": (
                        "The server redirected; the credential is not sent "
                        "onward."
                    ),
                },
                {
                    "const": "not_problem",
                    "$anchor": "not_problem",
                    "x-category": "transport",
                    "x-exit": 2,
                    "x-status": [],
                    "x-retryable": False,
                    "description": (
                        "An error reply with no packet whose status maps to "
                        "no code (an HTML 404, say)."
                    ),
                },
                {
                    "const": "not_json",
                    "$anchor": "not_json",
                    "x-category": "transport",
                    "x-exit": 2,
                    "x-status": [],
                    "x-retryable": False,
                    "description": "A success reply whose body is not JSON.",
                },
                {
                    "const": "local_error",
                    "$anchor": "local_error",
                    "x-category": "local",
                    "x-exit": 2,
                    "x-status": [],
                    "x-retryable": False,
                    "description": (
                        "A failure on the caller's own machine: a config file "
                        "that cannot be read, an output path that cannot be "
                        "written. params points at the path param when there "
                        "is one."
                    ),
                },
            ],
            "description": (
                "Each code fixes its category (x-category), the cli exit code "
                "(x-exit), its HTTP statuses (x-status), and whether retrying "
                "can help (x-retryable). x-status: the first is what a server "
                "sends for this code; empty for codes only a client makes. A "
                "reply with no packet decodes by status only when exactly one "
                "code lists that status (401, 409, 429, 422, 501, 500, 502, "
                "503, 504); a status several codes share (400, 403, 404) "
                "decodes to not_problem."
            ),
        },
    },
}
# fmt: on
