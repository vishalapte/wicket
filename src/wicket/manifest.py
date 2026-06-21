"""The unified year-sharded message manifest (ADR 0002).

One JSON Lines shard per UTC year at ``<store-dir>/<YYYY>.jsonl``, one row per
message keyed by the provider-neutral ``id``. Both verbs write here: ``catalog``
writes the observation fields and ``deleted``; ``fetch`` writes the settlement
fields (``downloaded``, ``path``). A catalog rewrite of a shard refreshes
observation and preserves settlement, so the two never clobber each other.

Pure stdlib, operates on paths and dicts: it is the shared store below the
verbs, fully testable without a mailbox.

The store is a *cache* (ADR 0002): ``downloaded`` is ground-truthed by the
``.eml`` on disk and ``domain`` is derived from observation, so the whole thing
is rebuildable from mailbox + disk by re-running the verbs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Settlement fields a catalog rewrite carries forward (fetch owns them).
SETTLEMENT_FIELDS = ("downloaded", "path")

Row = dict[str, object]
Shard = dict[str, Row]  # keyed by `id`


def shard_path(store_dir: Path, year: int) -> Path:
    """Path of the shard holding messages whose UTC `date` year is `year`."""
    return store_dir / f"{year}.jsonl"


def read_shard(path: Path) -> Shard:
    """Load one shard into ``{id: row}``; an absent shard is empty."""
    rows: Shard = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[str(row["id"])] = row
    return rows


def write_shard(path: Path, rows: Shard) -> None:
    """Atomically replace a shard, rows id-sorted and keys sorted.

    Deterministic so an unchanged store rewrites byte-identically (idempotent).
    Private mailbox metadata, so the dir is 0700 and the file 0600.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for _id, row in sorted(rows.items()):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def store_shards(store_dir: Path) -> list[Path]:
    """The shard files in a store dir, sorted; empty if the dir is absent."""
    return sorted(store_dir.glob("*.jsonl")) if store_dir.exists() else []


def latest_shard_year(store_dir: Path) -> int | None:
    """The most recent UTC year present in the store, or None when empty."""
    years = [int(p.stem) for p in store_shards(store_dir) if p.stem.isdigit()]
    return max(years) if years else None


def load_store(store_dir: Path) -> Shard:
    """Load every shard into one ``{id: row}`` map."""
    rows: Shard = {}
    for path in store_shards(store_dir):
        rows.update(read_shard(path))
    return rows


def merge_settlement(prior: Shard, settlements: Shard) -> Shard:
    """Merge settlement fields into matching rows, adding rows that are new.

    fetch owns ``downloaded`` / ``path`` and may run before catalog has observed
    a message, so a settlement with no prior observation row becomes a new,
    partial row (ADR 0002 progressive enrichment).
    """
    merged = dict(prior)
    for id_, fields in settlements.items():
        row = dict(merged.get(id_, {}))
        row.update(fields)
        merged[id_] = row
    return merged


def merge_catalog(prior: Shard, observed: Shard, *, complete: bool) -> Shard:
    """Merge a fresh catalog of `observed` rows into a shard's `prior` rows.

    An observed row keeps its fresh observation fields and inherits the prior
    row's settlement fields; its ``deleted`` is False (it is in the mailbox).

    A prior row that was NOT re-observed is handled only when the catalog is
    ``complete`` (a full sweep of the shard's year, the sole basis on which
    absence proves deletion):

    * if it was ``downloaded`` -> kept with ``deleted = True`` (the local
      ``.eml`` is now the only copy);
    * otherwise it is dropped (gone and never kept is no row, not a tombstone).

    On an incomplete catalog (incremental or ``--years`` subset) an unobserved
    prior row is preserved untouched, because absence cannot prove deletion.
    """
    merged: Shard = {}
    for id_, row in observed.items():
        new = dict(row)
        carried = prior.get(id_, {})
        for field in SETTLEMENT_FIELDS:
            if field in carried:
                new[field] = carried[field]
        new["deleted"] = False
        merged[id_] = new
    for id_, row in prior.items():
        if id_ in observed:
            continue
        if not complete:
            merged[id_] = row
        elif row.get("downloaded"):
            gone = dict(row)
            gone["deleted"] = True
            merged[id_] = gone
        # else: gone and not downloaded -> dropped (no tombstone)
    return merged
