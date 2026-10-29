"""Idempotent local reconcile of the store.

No IMAP. Across every shard, ``reconcile`` sets ``downloaded`` to whatever the
dest tree says (ground truth) and derives ``domain`` from observation. These are
the offline, deterministic truths a verb re-establishes before acting (ADR
0002): running it twice changes nothing. The rules it derives with live in
`wicket.filing`, the one home for where mail is filed.
"""

from __future__ import annotations

from pathlib import Path

from wicket.filing import thread_domain
from wicket.manifest import Row, eml_on_disk, read_shard, store_shards, write_shard


def reconcile(
    store_dir: Path,
    dest: Path,
    imap_email: str,
    domain_aliases: dict[str, str],
) -> dict[str, int]:
    """Verify ``downloaded`` against disk and derive ``domain`` from observation.

    Per shard, every row's ``downloaded`` is set to whether its ``.eml`` is on
    disk, and every not-yet-downloaded row's ``domain`` is derived from its
    thread's earliest observed message (the From-first rule; None when it can't
    be resolved). A downloaded row keeps its settled domain: its ``.eml`` path is
    committed and is not moved. Offline, deterministic,
    idempotent.
    """
    rows_total = downloaded_fixed = domain_set = 0
    for shard in store_shards(store_dir):
        rows = read_shard(shard)
        new_rows: dict[str, Row] = {id_: dict(row) for id_, row in rows.items()}
        for row in new_rows.values():
            held = eml_on_disk(row, dest)
            if row.get("downloaded", False) != held:
                downloaded_fixed += 1
            row["downloaded"] = held
        by_thread: dict[str, list[Row]] = {}
        for row in new_rows.values():
            by_thread.setdefault(str(row.get("thread_id", "")), []).append(row)
        for members in by_thread.values():
            observed = [m for m in members if "from" in m]
            derived = (
                thread_domain(observed, imap_email, domain_aliases)
                if observed
                else None
            )
            for m in members:
                prior = m.get("domain")
                if m.get("downloaded"):
                    # The .eml path is committed; its first segment is the domain.
                    path = m.get("path")
                    domain = str(path).split("/", 1)[0] if path else prior
                elif observed:
                    domain = derived  # None when unresolvable
                else:
                    domain = prior  # no observation, no path: keep
                if prior != domain:
                    domain_set += 1
                m["domain"] = domain
        rows_total += len(new_rows)
        write_shard(shard, new_rows)
    return {
        "rows": rows_total,
        "downloaded_fixed": downloaded_fixed,
        "domain_set": domain_set,
    }
