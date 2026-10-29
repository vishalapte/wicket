"""A verb's output schema, generated from the return type of its api function.

A verb's result shape is written once, as the TypedDict its api function returns.
``output()`` publishes that type through ``returns`` rather than restating it, and a test
checks a real result against the same type with ``conforms``. Standard library only.

A result may hold ``str``, ``int``, ``float``, ``bool``, ``None``, ``list[...]``,
``dict[str, ...]``, ``Literal[...]``, unions of those, and nested TypedDicts. Anything
else raises ``TypeError``: a result that is not JSON cannot reach every surface unchanged.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Callable
from typing import Any, Literal, get_args, get_origin, get_type_hints, is_typeddict

_SCALARS: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    types.NoneType: "null",
}
_UNIONS = (typing.Union, types.UnionType)


def returns(fn: Callable[..., Any]) -> dict[str, Any]:
    """The JSON Schema of what ``fn`` returns, read from its return annotation."""
    return json_schema(get_type_hints(fn)["return"])


def json_schema(tp: Any) -> dict[str, Any]:
    """The JSON Schema of ``tp``; ``TypeError`` for a type a result may not hold."""
    if tp in _SCALARS:
        return {"type": _SCALARS[tp]}
    origin, args = get_origin(tp), get_args(tp)
    if origin is Literal:
        return {"enum": list(args)}
    if origin in _UNIONS:
        return {"anyOf": [json_schema(arg) for arg in args]}
    if origin is list and args:
        return {"type": "array", "items": json_schema(args[0])}
    if origin is dict and args and args[0] is str:
        return {"type": "object", "additionalProperties": json_schema(args[1])}
    return _object_schema(tp)


def _object_schema(tp: Any) -> dict[str, Any]:
    """A TypedDict as a closed object: every key typed, the required ones listed."""
    if not is_typeddict(tp):
        raise TypeError(f"{tp!r} is not a JSON type a result may hold")
    hints = get_type_hints(tp)
    return {
        "type": "object",
        "properties": {key: json_schema(value) for key, value in hints.items()},
        "required": sorted(tp.__required_keys__),
        "additionalProperties": False,
    }


def conforms(value: Any, tp: Any, at: str = "$") -> list[str]:
    """Every way ``value`` departs from ``tp``, as ``<path>: <problem>``; empty if none."""
    if tp in _SCALARS:
        return _scalar_problems(value, tp, at)
    origin, args = get_origin(tp), get_args(tp)
    if origin is Literal:
        return [] if value in args else [f"{at}: {value!r} is not one of {list(args)}"]
    if origin in _UNIONS:
        matched = any(not conforms(value, arg, at) for arg in args)
        return [] if matched else [f"{at}: {value!r} matches none of {tp}"]
    if origin in (list, dict) and args:
        return _container_problems(value, origin, args, at)
    return _object_problems(value, tp, at)


def _scalar_problems(value: Any, tp: Any, at: str) -> list[str]:
    """A scalar of the declared JSON type. ``bool`` is never taken for a number."""
    if tp is types.NoneType:
        ok = value is None
    elif tp is bool:
        ok = isinstance(value, bool)
    elif tp is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        ok = isinstance(value, tp) and not isinstance(value, bool)
    return [] if ok else [f"{at}: expected {_SCALARS[tp]}, got {type(value).__name__}"]


def _container_problems(
    value: Any, origin: Any, args: tuple[Any, ...], at: str
) -> list[str]:
    """A list whose every item conforms, or a string-keyed dict whose every value does."""
    if origin is list:
        if not isinstance(value, list):
            return [f"{at}: expected array, got {type(value).__name__}"]
        return [
            p
            for i, item in enumerate(value)
            for p in conforms(item, args[0], f"{at}[{i}]")
        ]
    if not isinstance(value, dict):
        return [f"{at}: expected object, got {type(value).__name__}"]
    problems = [
        f"{at}: key {key!r} is not a string"
        for key in value
        if not isinstance(key, str)
    ]
    return problems + [
        p for k, v in value.items() for p in conforms(v, args[1], f"{at}.{k}")
    ]


def _object_problems(value: Any, tp: Any, at: str) -> list[str]:
    """A dict with every required key of the TypedDict, no other key, each value conforming."""
    if not is_typeddict(tp):
        raise TypeError(f"{tp!r} is not a JSON type a result may hold")
    if not isinstance(value, dict):
        return [f"{at}: expected object, got {type(value).__name__}"]
    hints = get_type_hints(tp)
    problems = [
        f"{at}: missing key {k!r}" for k in sorted(tp.__required_keys__ - value.keys())
    ]
    problems += [
        f"{at}: unexpected key {k!r}" for k in sorted(value.keys() - hints.keys())
    ]
    return problems + [
        p
        for k, v in value.items()
        if k in hints
        for p in conforms(v, hints[k], f"{at}.{k}")
    ]
