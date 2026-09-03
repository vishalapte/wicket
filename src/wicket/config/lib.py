"""The worker: generic ``{primary: [items, ...]}`` map CRUD on disk.

No argparse, no ``if __name__ == "__main__"``, no CLI, no validation — strictly
mechanical, per the package convention every other verb's ``lib.py`` follows.
All three owner-authored maps (account-aliases, domain-aliases, domain-routes)
share this one shape, so one engine serves all three; `wicket.config.api` binds
it to a path and the primary/item validators the resource actually needs.
"""

from __future__ import annotations

import json
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


def write_map(path: Path, mapping: Map) -> None:
    """Write ``mapping`` back, sorted keys and deterministic list order.

    The mail root itself must already exist (`wicket.env.require_mail_root` is
    the api's job, called before this); this never creates a directory.
    """
    ordered = {primary: sorted(mapping[primary]) for primary in sorted(mapping)}
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def list_entries(path: Path) -> Map:
    """Every ``primary -> [items]`` entry, unmodified."""
    return read_map(path)


def create_entry(path: Path, primary: str, items: list[str]) -> Map:
    """Add a brand-new ``primary``. Refuses to overwrite one that already exists."""
    mapping = read_map(path)
    if primary in mapping:
        raise ValueError(f"{primary!r} already exists in {path} -- use update")
    mapping[primary] = sorted(set(items))
    write_map(path, mapping)
    return mapping


def update_entry(path: Path, primary: str, add: list[str], remove: list[str]) -> Map:
    """Add/remove items on an existing ``primary``. Refuses an unknown one."""
    mapping = read_map(path)
    if primary not in mapping:
        raise ValueError(f"{primary!r} not found in {path} -- use create")
    items = (set(mapping[primary]) | set(add)) - set(remove)
    mapping[primary] = sorted(items)
    write_map(path, mapping)
    return mapping


def delete_entry(path: Path, primary: str, item: str | None = None) -> Map:
    """Delete one ``item`` under ``primary``, or the whole ``primary`` when ``item`` is None.

    Deleting the last item under a primary removes the primary too, rather than
    leaving a ``primary: []`` entry no reader expects.
    """
    mapping = read_map(path)
    if primary not in mapping:
        raise ValueError(f"{primary!r} not found in {path}")
    if item is None:
        del mapping[primary]
    else:
        remaining = [i for i in mapping[primary] if i != item]
        if len(remaining) == len(mapping[primary]):
            raise ValueError(f"{item!r} not found under {primary!r} in {path}")
        if remaining:
            mapping[primary] = remaining
        else:
            del mapping[primary]
    write_map(path, mapping)
    return mapping
