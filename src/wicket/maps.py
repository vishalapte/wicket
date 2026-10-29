"""The owner-authored maps on disk: ``{primary: [items, ...]}``, read, edited, written.

All three maps (account-aliases, domain-aliases, domain-routes) share this one shape, so one
engine serves the nouns that edit them (`wicket.alias`, `wicket.store`). The edits are pure:
each takes a map and returns a new one, so a caller can check the result with the readers'
own rules (`env.parse_account_aliases`, `domains.parse_domain_aliases`,
`domains.parse_domain_routes`) before anything reaches disk. No validation lives here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

Map = dict[str, list[str]]


def read_map(path: Path) -> Map:
    """The map at ``path``, or ``{}`` when the file does not exist yet."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level value must be an object")
    return raw


def render_map(mapping: Map) -> str:
    """The file text: sorted keys, sorted items, two-space indent."""
    ordered = {primary: sorted(mapping[primary]) for primary in sorted(mapping)}
    return json.dumps(ordered, indent=2) + "\n"


def write_maps(changes: dict[Path, Map]) -> None:
    """Replace each file whole, all or as few as a failure allows.

    Each map is written to a temporary file beside its target and renamed over it, so a
    reader never sees a half-written file. If a later rename fails, the files already
    replaced are put back from what they held before. A crash between two renames still
    leaves only the first replaced; each file is valid on its own, and re-running the
    command finishes the job. The mail root must already exist (the api's
    `require_mail_root`); this never creates a directory.
    """
    before = {path: path.read_bytes() if path.exists() else None for path in changes}
    staged: list[tuple[Path, Path]] = []
    try:
        for path, mapping in changes.items():
            temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temp.write_text(render_map(mapping), encoding="utf-8")
            staged.append((temp, path))
        done: list[Path] = []
        try:
            for temp, path in staged:
                temp.replace(path)
                done.append(path)
        except OSError:
            for path in done:
                old = before[path]
                if old is None:
                    path.unlink()
                else:
                    path.write_bytes(old)
            raise
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)


def create_entry(mapping: Map, primary: str, items: list[str]) -> Map:
    """Add a new ``primary``. Refuses one that already exists."""
    if primary in mapping:
        raise ValueError(f"{primary!r} already exists -- use update")
    return {**mapping, primary: sorted(set(items))}


def update_entry(mapping: Map, primary: str, add: list[str], remove: list[str]) -> Map:
    """Add and remove items on an existing ``primary``. Refuses an unknown one."""
    if primary not in mapping:
        raise ValueError(f"{primary!r} not found -- use create")
    return {
        **mapping,
        primary: sorted((set(mapping[primary]) | set(add)) - set(remove)),
    }


def add_items(mapping: Map, primary: str, items: list[str]) -> Map:
    """Add ``items`` under ``primary``, creating it when it is new."""
    return {**mapping, primary: sorted(set(mapping.get(primary, [])) | set(items))}


def remove_items(mapping: Map, primary: str, items: list[str] | None) -> Map:
    """Remove ``items`` from ``primary``, or the whole ``primary`` when ``items`` is None.

    An item or primary already absent is skipped, so re-running a delete that stopped half
    way finishes it. A primary left with no items is removed rather than kept as ``[]``.
    """
    result = dict(mapping)
    if items is None:
        result.pop(primary, None)
        return result
    remaining = sorted(set(result.get(primary, [])) - set(items))
    if remaining:
        result[primary] = remaining
    else:
        result.pop(primary, None)
    return result
