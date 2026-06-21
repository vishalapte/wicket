"""Public library surface for consumers (meridian, factotum).

Locate an account's manifest and archive and iterate the held ``.eml`` without
reaching into the verb internals. ``held_messages`` and ``manifest`` are
re-exported from the package root; the consumer parses the ``.eml`` itself. A
usage example is in the repo README, "Use as a library".
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from wicket.config import discover_account, resolve_archive_dir, resolve_store_dir
from wicket.manifest import Row, load_store


def _account(account: str | None) -> str:
    resolved = account or discover_account()
    if resolved is None:
        raise ValueError(
            "no account: pass account=..., or keep exactly one under ~/mail"
        )
    return resolved


def manifest(account: str | None = None) -> dict[str, Row]:
    """The whole manifest for ``account`` (default: the sole ``~/mail`` account)."""
    return load_store(resolve_store_dir(_account(account)))


def held_messages(
    account: str | None = None, domains: set[str] | None = None
) -> Iterator[tuple[Row, Path]]:
    """Yield ``(row, eml_path)`` for every downloaded message.

    Optionally restrict to a set of filing ``domains`` (e.g. the airline domains
    a travel parser cares about). The caller reads and parses the ``.eml``.
    """
    acct = _account(account)
    archive = resolve_archive_dir(acct)
    for row in load_store(resolve_store_dir(acct)).values():
        if not row.get("downloaded"):
            continue
        if domains is not None and row.get("domain") not in domains:
            continue
        path = row.get("path")
        if path:
            yield row, archive / str(path)
