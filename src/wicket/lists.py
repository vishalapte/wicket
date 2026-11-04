"""The list-folder map: group addresses whose threads file under ``archive/lists/<name>/``.

An owner-authored map at the mail root, ``list-folders.json``, in the shape the other maps
share (`wicket.maps`): ``{"<name>": [address, ...]}`` (owner, 2026-11-04: "file everything to
qseprimarycontacts@lists.ercot.com -> mail/vishal@gfinfra.com/lists/qse"). A thread whose
opener's ``To`` names one of the addresses files under ``lists/<name>`` in whichever store
files it; `wicket.filing.place` applies the rule. A name is a slug, like a bucket's; an
address belongs to one name only (`env.flatten_aliases`).

Shared layer: imports only `wicket.env`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from wicket.env import (
    ADDRESS_RE,
    BUCKET_RE,
    LIST_FOLDERS_ENV_VAR,
    LIST_FOLDERS_FILENAME,
    MAIL_ROOT,
    flatten_aliases,
)


def list_folders_path(mail_root: Path | None = None) -> Path:
    """The one home for the map (``<mail-root>/list-folders.json``).

    ``$WICKET_LIST_FOLDERS``, when set, names the file directly and wins.
    """
    override = os.environ.get(LIST_FOLDERS_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return (mail_root or MAIL_ROOT) / LIST_FOLDERS_FILENAME


def load_list_folders(path: Path | None = None) -> dict[str, str]:
    """``address -> name``, or ``{}`` when the file is absent (the map is optional)."""
    file = path or list_folders_path()
    if not file.exists():
        return {}
    return parse_list_folders(json.loads(file.read_text(encoding="utf-8")), file)


def parse_list_folders(raw: object, path: Path) -> dict[str, str]:
    """The list-folder rule over a map in memory; ``path`` names it in errors.

    Each key a slug, each item an address; the addresses lowercased, as a header's are
    read (`filing._parse_header_addresses`).
    """
    mapping = flatten_aliases(
        raw,
        path,
        primary_ok=lambda name: isinstance(name, str) and bool(BUCKET_RE.match(name)),
        alias_ok=lambda a: isinstance(a, str) and bool(ADDRESS_RE.match(a)),
        noun="list",
    )
    return {
        address.lower(): name for address, name in mapping.items() if "@" in address
    }
