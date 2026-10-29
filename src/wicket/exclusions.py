"""The excluded-lists map: mailing lists whose mail is never fetched or filed.

The fourth owner-authored map at the mail root, ``excluded-lists.json``, in the shape the
other three share (`wicket.maps`): ``{"*": [list-id, ...], "<account>": [list-id, ...]}``.
``"*"`` is every account; an account's own entry adds to it for that account alone
(owner, 2026-10-29: "general if account is not specified and specific to account if
specified"). A list-id is the text inside a message's ``List-Id`` header
(``made-of-meat.googlegroups.com``), so this is a mailing-list exclusion and nothing
else. ``fetch`` leaves an excluded list out of every search; ``ingest`` skips its mail;
``catalog`` records the whole mailbox. `wicket.exclude` writes the map, through
`parse_excluded_lists`, the rule this module reads it by.

Shared layer: imports only `wicket.env`.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path

from wicket.env import (
    EXCLUDED_LISTS_FILE_ENV_VAR,
    EXCLUDED_LISTS_FILENAME,
    MAIL_ROOT,
    normalize_account,
)

# The key every account reads.
EVERY_ACCOUNT = "*"

# RFC 2919's list-id: a dot-atom (atext and dots; no brackets, no spaces). Matched whole,
# and searched with its angle brackets, so a part of one list's name never matches another.
LIST_ID_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$"
)

ExcludedLists = dict[str, list[str]]


def excluded_lists_path(mail_root: Path | None = None) -> Path:
    """The one home for the map (``<mail-root>/excluded-lists.json``).

    ``$WICKET_EXCLUDED_LISTS_FILE``, when set, names the file directly and wins.
    """
    override = os.environ.get(EXCLUDED_LISTS_FILE_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return (mail_root or MAIL_ROOT) / EXCLUDED_LISTS_FILENAME


def normalize_list_id(raw: str) -> str:
    """A ``List-Id`` value or a list-id as the map holds it: the bracketed id, lowercased.

    ``List-Id: Made of meat <made-of-meat.googlegroups.com>`` reads as
    ``made-of-meat.googlegroups.com``; a bare id is lowercased. '' when there is none.
    """
    text = raw.strip()
    if "<" in text and text.endswith(">"):
        text = text[text.rindex("<") + 1 : -1]
    return text.strip().lower()


def parse_excluded_lists(
    raw: object, path: Path, accounts: Iterable[str]
) -> ExcludedLists:
    """The map's rule over a map in memory; ``path`` names it in errors.

    A key is ``"*"`` or an account in ``accounts`` (the folders holding ``imap.json`` in
    the accounts dir the run resolves), so a mistyped key, or a bucket, refuses as a
    mistyped ``--mail-account`` does: nothing is silently excluded from nothing. Each
    item is a whole list-id (`LIST_ID_RE`). One list may sit under ``"*"`` and under an
    account. Raises ``ValueError``.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level value must be an object")
    known = {normalize_account(a) for a in accounts}
    parsed: ExcludedLists = {}
    for key, items in raw.items():
        if key != EVERY_ACCOUNT and normalize_account(str(key)) not in known:
            raise ValueError(
                f"{path}: {key!r} is not {EVERY_ACCOUNT!r} or an account folder "
                "holding imap.json"
            )
        if not isinstance(items, list):
            raise ValueError(f"{path}: value for {key!r} must be a list")
        for item in items:
            if not isinstance(item, str) or not LIST_ID_RE.match(item):
                raise ValueError(
                    f"{path}: {item!r} under {key!r} is not a list-id "
                    "(made-of-meat.googlegroups.com)"
                )
        name = key if key == EVERY_ACCOUNT else normalize_account(str(key))
        parsed[name] = sorted({normalize_list_id(i) for i in items})
    return parsed


def load_excluded_lists(path: Path, accounts: Iterable[str]) -> ExcludedLists:
    """The map at ``path`` by `parse_excluded_lists`, or ``{}`` when the file is absent."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return parse_excluded_lists(raw, path, accounts)


def excluded_for(account: str, mapping: ExcludedLists) -> list[str]:
    """The lists ``account`` never fetches or files: ``"*"``'s, then its own, sorted."""
    own = mapping.get(normalize_account(account), [])
    return sorted({*mapping.get(EVERY_ACCOUNT, []), *own})
