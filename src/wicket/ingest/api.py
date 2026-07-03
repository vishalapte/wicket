"""Orchestration for the ingest verb: resolve paths, dispatch to the worker.

The single place the ingest face reaches the engine. Read-only on any mailbox:
ingest reads ``.eml`` already on disk and files them additively into the manifest
+ archive, so there is no IMAP and no credentials to seed (like `report.api`). It
resolves the account (`config.resolve_account`) and its per-account store and
archive paths, dispatches into the sibling worker, and returns its stats dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wicket.config import resolve_account, resolve_archive_dir, resolve_store_dir
from wicket.ingest.lib import ingest_folder

__all__ = ["IngestOptions", "ingest"]


@dataclass(frozen=True)
class IngestOptions:
    """How to run an ingest, independent of which account.

    ``src`` is a flat folder of ``.eml``. ``source`` names the export profile
    ("local" is the only one today: an Outlook / Apple Mail drag-export). With
    ``dry_run`` the worker parses and reports what it would add but writes
    nothing.
    """

    src: Path
    source: str = "local"
    dry_run: bool = False


def ingest(account: str | None = None, *, options: IngestOptions) -> dict[str, object]:
    """File the new ``.eml`` under ``options.src`` into ``account``'s store + archive."""
    resolved = resolve_account(account)
    return ingest_folder(
        src=options.src,
        store_dir=resolve_store_dir(resolved),
        archive_dir=resolve_archive_dir(resolved),
        account=resolved,
        dry_run=options.dry_run,
    )
