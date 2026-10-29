"""The `---`-fenced block at the top of a markdown file, and what it declares.

A block opens on the file's first line and closes on the next line that is `---` alone.
Trailing spaces after either fence and CRLF line ends are tolerated, and a file may end
right at the closing fence. Everything after the closing fence is the body.

The readings, because callers want different things:

  `split(text)`       (block, body), or (None, text) when there is no block
  `block(text)`       the block's text, or None
  `load(source)`      the block as a mapping, or {} when absent or not a mapping
  `mapping(head)`     the block's text as a mapping; else raises `Invalid`, saying why
  `value(text, key)`  one key's value, read from that key's own lines, or None

`load` swallows a YAML error, so one file whose `---` header is not YAML does not stop a
walk; a caller that must report a bad block calls `mapping`. `value` parses one key and never
the whole block, so a sibling key that is not strict YAML cannot erase the key asked for.

The standard library is enough for `split` and `block`; `load` imports PyYAML when it is
called, so a stdlib-only script (`bump_version.py`, which runs with no venv) can import
this module.

Complexity: O(n) in the file's length
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_BLOCK = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


class Invalid(ValueError):
    """A block that is present but is not a YAML mapping; the message says why."""


def split(text: str) -> tuple[str | None, str]:
    """(block, body), or (None, text) when the file opens with no closed block."""
    match = _BLOCK.match(text)
    if not match:
        return None, text
    return match.group(1), text[match.end() :]


def block(text: str) -> str | None:
    """The block's text between its fences, or None when there is none."""
    return split(text)[0]


def mapping(head: str) -> dict[str, Any]:
    """The block's text as a mapping; raises `Invalid` when not YAML or not a map."""
    import yaml  # pylint: disable=import-outside-toplevel  # see the module docstring

    try:
        loaded = yaml.safe_load(head)
    except yaml.YAMLError as exc:
        raise Invalid(f"YAML parse error — {exc}") from exc
    if not isinstance(loaded, dict):
        raise Invalid("top-level must be a mapping")
    return loaded


def parse(text: str) -> tuple[dict[str, Any], str]:
    """(mapping, body). A block absent or not a YAML mapping reads as ({}, text)."""
    head, body = split(text)
    if head is None:
        return {}, text
    try:
        return mapping(head), body
    except Invalid:
        return {}, text


def value(text: str, key: str) -> Any:
    """`key`'s value, flow or block form, parsed from that key's own lines; else None.

    None when there is no block, no such top-level key, or the key's own lines are not
    YAML. Both forms read the same:

        pnode: [../README.md]
        pnode:
          - ../README.md
    """
    import yaml  # pylint: disable=import-outside-toplevel  # see the module docstring

    head = block(text)
    if head is None:
        return None
    lines = head.splitlines()
    for i, line in enumerate(lines):
        match = re.match(rf"^{re.escape(key)}:[ \t]*(.*)$", line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            fragment = f"{key}: {inline}"
        else:
            nested = [n for n in _indented(lines[i + 1 :]) if n.strip()]
            if not nested:
                return None
            fragment = f"{key}:\n" + "\n".join(nested)
        try:
            loaded = yaml.safe_load(fragment)
        except yaml.YAMLError:
            return None
        return loaded.get(key) if isinstance(loaded, dict) else None
    return None


def _indented(rest: list[str]) -> list[str]:
    """The indented continuation lines of a block-form key, up to the next top key."""
    out: list[str] = []
    for line in rest:
        if line.strip() and not line[:1].isspace():
            break
        out.append(line)
    return out


def load(source: Path | str) -> dict[str, Any]:
    """The block as a mapping, from a path or from text. A missing file reads as {}."""
    if isinstance(source, Path):
        if not source.is_file():
            return {}
        source = source.read_text(encoding="utf-8", errors="replace")
    return parse(source)[0]
