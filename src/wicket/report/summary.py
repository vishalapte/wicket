"""Read-only reports over the manifest: senders by volume, addresses seen.

No IMAP, no mutation: it loads the year-sharded store and summarizes. ``from`` /
``to`` are parsed defensively with ``getaddresses`` so a row in either format
(a bare address, a list, or a raw header) yields the same addresses.
"""

from __future__ import annotations

from collections import Counter
from email.utils import getaddresses
from pathlib import Path

from wicket.manifest import Row, load_store


def addresses(value: object) -> list[str]:
    """Lowercased addresses parsed from a ``from``/``to`` value (any format)."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [str(value)]
    return [a.strip().lower() for _, a in getaddresses(items) if a.strip() and "@" in a]


def _from_address(row: Row) -> str | None:
    parsed = addresses(row.get("from"))
    return parsed[0] if parsed else None


def sender_counts(store_dir: Path) -> list[tuple[str, int]]:
    """``(sender, message count)`` for every From address, descending by count."""
    counter: Counter[str] = Counter()
    for row in load_store(store_dir).values():
        sender = _from_address(row)
        if sender:
            counter[sender] += 1
    return counter.most_common()


def all_addresses(store_dir: Path) -> list[str]:
    """Every distinct address ever seen in From or To, sorted."""
    seen: set[str] = set()
    for row in load_store(store_dir).values():
        seen.update(addresses(row.get("from")))
        seen.update(addresses(row.get("to")))
    return sorted(seen)


def summary(store_dir: Path) -> dict[str, int]:
    """One-screen counts: messages, held vs gone, observed-only, distinct addrs."""
    rows = list(load_store(store_dir).values())
    downloaded = [r for r in rows if r.get("downloaded")]
    gone = sum(1 for r in downloaded if r.get("deleted"))
    senders: set[str] = set()
    addrs: set[str] = set()
    for row in rows:
        sender = _from_address(row)
        if sender:
            senders.add(sender)
            addrs.add(sender)
        addrs.update(addresses(row.get("to")))
    return {
        "messages": len(rows),
        "downloaded": len(downloaded),
        "downloaded_gone": gone,
        "downloaded_present": len(downloaded) - gone,
        "observed_only": sum(
            1 for r in rows if not r.get("downloaded") and "from" in r
        ),
        "senders": len(senders),
        "addresses": len(addrs),
    }
