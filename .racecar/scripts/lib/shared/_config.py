"""`config.toml`, read once: what a setting is, what a search list is, and the names each reads.

Every reader of `config.toml` asks here, so no two decide differently what counts as a setting.
The file is TOML, read with stdlib `tomllib`.

- A **setting** is a table carrying `tiers`; any other table is a grouping level.
- A **search list** is a table under the reserved top-level `search`: `[search.<name>]` with
  `env`, the variable holding a colon-separated list of files, and `default`, the list used when
  that variable is unset (`arch-python/ENVVAR.md` §3).

Complexity: O(n) in the declaration's tables.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_REL = "config.toml"

#: The top-level name that holds search lists, never settings.
SEARCH = "search"


@dataclass(frozen=True)
class Declaration:
    """What `config.toml` declares: settings by dotted name, search lists by name."""

    settings: dict[str, Any] = field(default_factory=dict)
    searches: dict[str, Any] = field(default_factory=dict)


def _flatten(table: dict[str, Any], prefix: tuple[str, ...] = ()) -> dict[str, Any]:
    """`{dotted name: setting}` from TOML's nested tables."""
    out: dict[str, Any] = {}
    for key, value in table.items():
        if not isinstance(value, dict):
            continue
        if "tiers" in value:
            out[".".join((*prefix, key))] = value
        else:
            out.update(_flatten(value, (*prefix, key)))
    return out


def parse(text: str) -> Declaration:
    """The declaration in `text`. Raises `tomllib.TOMLDecodeError` when it is not TOML."""
    data = tomllib.loads(text)
    search = data.get(SEARCH)
    rest = {k: v for k, v in data.items() if k != SEARCH}
    searches = dict(search) if isinstance(search, dict) else {}
    return Declaration(settings=_flatten(rest), searches=searches)


def load(root: Path) -> Declaration | None:
    """The declaration at `root`, or None when there is no `config.toml`."""
    path = root / CONFIG_REL
    if not path.is_file():
        return None
    return parse(path.read_text(encoding="utf-8"))


def env_names(spec: Mapping[str, Any]) -> list[str]:
    """The environment variables one setting's `env` tiers name, in tier order."""
    return [
        str(tier["value"])
        for tier in spec.get("tiers") or []
        if isinstance(tier, dict) and tier.get("type") == "env" and tier.get("value")
    ]


def search_env_names(declaration: Declaration) -> list[str]:
    """The variable each search list reads its files from."""
    return [
        str(body["env"])
        for body in declaration.searches.values()
        if isinstance(body, dict) and isinstance(body.get("env"), str)
    ]


def search_paths(
    root: Path, body: Mapping[str, Any], environ: Mapping[str, str] | None = None
) -> list[Path]:
    """The files one search list names, in order: its variable's list when set, else `default`.

    A leading `~` expands to the home directory. A relative path is relative to `root`. Files
    that do not exist are kept here; the reader skips them, as `PATH` does.
    """
    raw = (os.environ if environ is None else environ).get(str(body.get("env", "")))
    entries = raw.split(":") if raw is not None else list(body.get("default") or [])
    out: list[Path] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry:
            continue
        path = Path(entry).expanduser()
        out.append(path if path.is_absolute() else root / path)
    return out
