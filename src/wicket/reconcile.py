"""Idempotent local reconcile of the store.

No IMAP. Across every shard, ``reconcile`` sets ``downloaded`` to whatever the
store's ``messages/`` says (ground truth, by each row's ``stem``): the offline,
deterministic truth a verb re-establishes before acting (ADR 0002). Running it
twice changes nothing.
"""

from __future__ import annotations

from pathlib import Path

from wicket.manifest import (
    eml_on_disk,
    message_files,
    read_shard,
    store_shards,
    write_shard,
)


def reconcile(store_dir: Path, dest: Path) -> dict[str, int]:
    """Verify ``downloaded`` against disk.

    Per shard, every row's ``downloaded`` is set to whether its ``.eml`` is under
    ``dest``, the store's ``messages/`` (by the row's ``stem``). Offline,
    deterministic, idempotent.
    """
    rows_total = downloaded_fixed = 0
    files = message_files(dest)
    for shard in store_shards(store_dir):
        rows = read_shard(shard)  # fresh dicts, changed in place
        for row in rows.values():
            held = eml_on_disk(row, files)
            if row.get("downloaded", False) != held:
                downloaded_fixed += 1
            row["downloaded"] = held
        rows_total += len(rows)
        write_shard(shard, rows)
    return {"rows": rows_total, "downloaded_fixed": downloaded_fixed}
