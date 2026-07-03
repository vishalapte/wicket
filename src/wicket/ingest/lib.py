"""Ingest a flat folder of ``.eml`` into the manifest + archive, additively.

The offline sibling of ``catalog`` + ``fetch``: it observes AND settles in one
pass from bodies already on disk (an Outlook / Apple Mail drag-export of RFC822
``.eml``), for a mailbox wicket cannot reach over IMAP.

**Additive, never destructive.** The source folder is a volatile inbox; the
archive is the durable record. A message already in the manifest (by the
provider-neutral ``message_key``) keeps its ``.eml`` and its row byte-for-byte;
only messages whose key is absent are filed. Nothing is ever deleted, so removing
a file from the source folder can never remove it from the archive. Re-running
with nothing new is a no-op.

Threads are reconstructed deterministically from headers (no subject-matching):
connected components over ``Message-ID`` <-> ``References``/``In-Reply-To``,
augmented by the Outlook ``Thread-Index`` conversation id. The filing domain is
the shared counterparty rule (`reconcile.compute_domain`) applied to a thread's
earliest message. When a thread already has an archived member, the new members
adopt that member's ``thread_id`` and ``domain`` so a reply never splits from its
thread or lands in a different folder.

No argparse, no CLI, no network: the CLI layer (`wicket.ingest.__main__`) parses
argv and the api resolves paths; this worker computes and writes.
"""

from __future__ import annotations

import base64
import binascii
import email
import email.policy
import email.utils
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from wicket.domains import DOMAIN_RE
from wicket.manifest import Row, read_shard, shard_path, write_shard
from wicket.message import message_key, normalize_message_id
from wicket.reconcile import compute_domain

# The export origin, tagged into the fallback key when a message carries no
# ``Message-ID``. Kept as "outlook" for continuity with the existing store
# (whose fallback keys were minted that way); every real header still keys on
# its portable ``Message-ID`` and is unaffected.
_PROVIDER_TAG = "outlook"
UNFILED = "_unfiled"


class UnionFind:
    """Minimal union-find over opaque string nodes for thread grouping."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, node: str) -> str:
        """Return the component root of ``node`` (path-halving as it walks)."""
        self.parent.setdefault(node, node)
        root = node
        while self.parent[root] != root:
            self.parent[root] = self.parent[self.parent[root]]
            root = self.parent[root]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _local_msgid(key: str) -> str:
    """Compact 16-hex archive filename for a portable key (Gmail X-GM-MSGID parity).

    Deterministic (same key -> same name, so re-runs are idempotent) and
    collision-safe at this scale; the full key stays the manifest ``id``.
    """
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _referenced_ids(msg: email.message.Message) -> list[str]:
    """Normalized message-ids this message replies to (References + In-Reply-To)."""
    out: list[str] = []
    for header in ("References", "In-Reply-To"):
        raw = str(msg.get(header, "") or "")
        for tok in raw.replace("<", " <").split():
            normalized = normalize_message_id(tok)
            if normalized:
                out.append(normalized)
    return out


def _conversation_key(msg: email.message.Message) -> str | None:
    """Outlook Thread-Index conversation id: hex of the shared leading 22 bytes."""
    raw = str(msg.get("Thread-Index", "") or "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw)
    except (ValueError, binascii.Error):
        return None
    return decoded[:22].hex() if len(decoded) >= 22 else None


def _domain_of(address: str) -> str | None:
    address = address.strip().lower()
    if "@" not in address:
        return None
    domain = address.split("@", 1)[1]
    return domain if domain and DOMAIN_RE.match(domain) else None


def _unfiled_fallback(to_addrs: list[str], account: str) -> str | None:
    """Owner rule: strip your own domain from To; if one external remains, use it."""
    own = account.split("@", 1)[1] if "@" in account else ""
    external = {d for a in to_addrs if (d := _domain_of(a)) and d != own}
    return next(iter(external)) if len(external) == 1 else None


def _parse_eml(path: Path) -> dict[str, object]:
    """Parse one ``.eml`` into the fields threading, filing, and the row need."""
    raw = path.read_bytes()
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    message_id = str(msg.get("Message-ID", "") or "")
    native = hashlib.sha1(raw).hexdigest()
    key = message_key(message_id=message_id, provider=_PROVIDER_TAG, native_id=native)

    parsed: datetime | None = None
    date_header = msg.get("Date")
    if date_header:
        try:
            parsed = email.utils.parsedate_to_datetime(str(date_header))
        except (TypeError, ValueError):
            parsed = None
    if parsed is None:
        parsed = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    dt = parsed.astimezone(timezone.utc)

    from_header = str(msg.get("From", "") or "")
    _, from_addr = email.utils.parseaddr(from_header)
    to_addrs = sorted(
        {
            addr.strip().lower()
            for _, addr in email.utils.getaddresses([str(msg.get("To", "") or "")])
            if addr
        }
    )
    return {
        "id": key,
        "raw": raw,
        "date": dt,
        "from_header": from_header,
        "from": from_addr.strip().lower(),
        "to": to_addrs,
        "subject": str(msg.get("Subject", "") or ""),
        "size": len(raw),
        "refs": _referenced_ids(msg),
        "conv": _conversation_key(msg),
    }


def _assign_threads(messages: list[dict[str, object]]) -> dict[str, str]:
    """Return {id -> thread_id} via References/In-Reply-To + Thread-Index.

    ``thread_id`` is the earliest member's id in its connected component.
    """
    uf = UnionFind()
    for m in messages:
        mid = str(m["id"])
        uf.find(mid)
        for ref in m["refs"]:  # type: ignore[union-attr]
            uf.union(mid, ref)
        if m["conv"]:
            uf.union(mid, f"tidx:{m['conv']}")

    components: dict[str, list[dict[str, object]]] = {}
    for m in messages:
        components.setdefault(uf.find(str(m["id"])), []).append(m)

    thread_of: dict[str, str] = {}
    for members in components.values():
        root = min(members, key=lambda m: (m["date"], str(m["id"])))
        thread_id = str(root["id"])
        for m in members:
            thread_of[str(m["id"])] = thread_id
    return thread_of


def _computed_domain(members: list[dict[str, object]], account: str) -> str | None:
    """Filing domain from the thread's earliest message that resolves to one."""
    ordered = sorted(members, key=lambda m: (m["date"], str(m["id"])))
    for m in ordered:
        domain = compute_domain(
            str(m["from_header"]), ", ".join(m["to"]), account, {}  # type: ignore[arg-type]
        )
        if domain is None:
            domain = _unfiled_fallback(m["to"], account)  # type: ignore[arg-type]
        if domain is not None:
            return domain
    return None


def _dedup_folder(files: list[Path]) -> tuple[list[dict[str, object]], int]:
    """Parse every file, keeping the first of each portable id; count the rest."""
    seen: dict[str, dict[str, object]] = {}
    dup = 0
    for parsed in (_parse_eml(p) for p in files):
        mid = str(parsed["id"])
        if mid in seen:
            dup += 1
        else:
            seen[mid] = parsed
    return list(seen.values()), dup


def _load_existing(store_dir: Path) -> dict[str, Row]:
    """Every row already in the store, id -> row, across all year shards."""
    existing: dict[str, Row] = {}
    for shard in sorted(store_dir.glob("*.jsonl")) if store_dir.exists() else []:
        existing.update(read_shard(shard))
    return existing


def _file_plan(
    members_by_thread: dict[str, list[dict[str, object]]],
    existing: dict[str, Row],
    account: str,
) -> dict[str, tuple[str, str]]:
    """Per folder-thread, the (thread_id, domain) new members file under.

    Reuse an archived member's thread_id and domain so a new reply stays joined
    to its thread and lands in the same folder; else compute both.
    """
    plan: dict[str, tuple[str, str]] = {}
    for tid, members in members_by_thread.items():
        archived = [existing[str(m["id"])] for m in members if str(m["id"]) in existing]
        if archived:
            anchor = min(archived, key=lambda r: str(r.get("date", "")))
            final_tid = str(anchor.get("thread_id", tid))
            domain = str(
                anchor.get("domain") or _computed_domain(members, account) or UNFILED
            )
        else:
            final_tid = tid
            domain = _computed_domain(members, account) or UNFILED
        plan[tid] = (final_tid, domain)
    return plan


def _row_for(m: dict[str, object], final_tid: str, domain: str) -> tuple[Row, str]:
    """Build the manifest row and archive rel-path for one new message."""
    mid = str(m["id"])
    mgid = _local_msgid(mid)
    dt: datetime = m["date"]  # type: ignore[assignment]
    rel = f"{domain}/{dt.strftime('%Y-%m')}/{mgid}.eml"
    row: Row = {
        "id": mid,
        "msgid": mgid,
        "thread_id": final_tid,
        "date": dt.isoformat(),
        "from": m["from"],
        "to": m["to"],
        "subject": m["subject"],
        "size": m["size"],
        "labels": None,  # provider folders/labels, absent in a flat export
        "deleted": False,
        "downloaded": True,
        "domain": domain,
        "path": rel,
    }
    return row, rel


def _commit(
    new_by_year: dict[int, dict[str, Row]],
    to_write: dict[str, tuple[str, bytes]],
    store_dir: Path,
    archive_dir: Path,
) -> None:
    """Copy new bodies, then union new rows into each year shard (additive only).

    A rewritten shard's pre-existing rows serialize byte-identically (same
    deterministic write_shard), so nothing already archived is disturbed.
    """
    for rel, raw in to_write.values():
        target = archive_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(raw)
    for year, rows in new_by_year.items():
        path = shard_path(store_dir, year)
        merged = read_shard(path)
        for mid, row in rows.items():
            if mid not in merged:  # never overwrite an existing row
                merged[mid] = row
        write_shard(path, merged)


def ingest_folder(
    *,
    src: Path,
    store_dir: Path,
    archive_dir: Path,
    account: str,
    dry_run: bool,
) -> dict[str, object]:
    """File the new ``.eml`` under ``src`` into the store + archive, additively.

    Returns a stats dict for the face to render. Raises ``FileNotFoundError`` when
    ``src`` holds no ``.eml``.
    """
    files = sorted(src.glob("*.eml"))
    if not files:
        raise FileNotFoundError(f"no .eml files under {src}")

    messages, dup = _dedup_folder(files)
    existing = _load_existing(store_dir)
    thread_of = _assign_threads(messages)

    members_by_thread: dict[str, list[dict[str, object]]] = {}
    for m in messages:
        members_by_thread.setdefault(thread_of[str(m["id"])], []).append(m)
    plan = _file_plan(members_by_thread, existing, account)

    new_by_year: dict[int, dict[str, Row]] = {}
    to_write: dict[str, tuple[str, bytes]] = {}  # id -> (rel_path, raw)
    domain_counter: Counter[str] = Counter()
    for m in messages:
        mid = str(m["id"])
        if mid in existing:
            continue  # already archived: untouched
        final_tid, domain = plan[thread_of[mid]]
        row, rel = _row_for(m, final_tid, domain)
        dt: datetime = m["date"]  # type: ignore[assignment]
        new_by_year.setdefault(dt.year, {})[mid] = row
        to_write[mid] = (rel, m["raw"])  # type: ignore[assignment]
        domain_counter[domain] += 1

    stats: dict[str, object] = {
        "files": len(files),
        "unique": len(messages),
        "dup": dup,
        "added": len(to_write),
        "unfiled": domain_counter.get(UNFILED, 0),
        "added_by_year": {y: len(rows) for y, rows in sorted(new_by_year.items())},
        "top_domains": domain_counter.most_common(10),
    }
    if dry_run or not to_write:
        return stats

    _commit(new_by_year, to_write, store_dir, archive_dir)
    return stats
