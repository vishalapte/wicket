"""Fold subdomain folders into their canonical (alias) domain.

Worker logic for `wicket.fold`: no argparse, no CLI, no IMAP. It operates
purely on the local archive tree and its manifest.

The archive tree is ``<dest>/<domain>/YYYY-MM/<msgid>.eml`` and the manifest
records one row per message with both a ``domain`` (the folder) and a
``path``. A *fold* takes every top-level domain folder whose canonical form
(via the alias map, ``wicket.domains.canonical_domain``) differs from
itself, and moves its ``.eml`` files into the canonical folder while
rewriting the matching manifest rows (``domain`` + ``path``) in lockstep, so
disk and ledger never drift.

The fold is alias-only and lossless:

* It folds ``us.icapenergy.com`` → ``icapenergy.com`` only because the alias
  file says so; nothing is inferred from the domain shape.
* It never overwrites. A name clash in the target folder is accepted only
  when the two files are byte-identical (the source is then a redundant
  duplicate and is removed); a differing clash aborts the whole fold.
* It never moves a file the manifest doesn't record, and never moves a file
  whose recorded path disagrees with where it physically sits (pre-existing
  drift). Either condition aborts the whole fold before anything is touched.

Idempotent: a second run finds every folder already canonical and does
nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wicket.domains import canonical_domain

_READ_CHUNK = 1 << 20  # 1 MiB hashing chunk.

# One manifest row (msgid → fields); a manifest is the whole JSON object.
Row = dict[str, object]
Manifest = dict[str, Any]


@dataclass(frozen=True)
class Move:
    """One planned relocation of a single ``.eml`` (paths are dest-relative)."""

    msgid: str
    canonical: str
    src_rel: str  # e.g. "us.icapenergy.com/2006-03/<id>.eml"
    dst_rel: str  # e.g. "icapenergy.com/2006-03/<id>.eml"
    redundant: bool  # target already holds a byte-identical copy → drop source


@dataclass
class FoldPlan:
    """The full set of moves plus any blockers that make the fold unsafe."""

    folds: dict[str, str] = field(default_factory=dict)  # source domain → canonical
    moves: list[Move] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(
        default_factory=list
    )  # (src, dst) byte-differ
    unrecorded: list[str] = field(default_factory=list)  # src with no manifest row
    mismatched: list[tuple[str, str]] = field(
        default_factory=list
    )  # (src, recorded path)

    @property
    def ok(self) -> bool:
        """True iff no blocker would make a move ambiguous or lossy."""
        return not (self.conflicts or self.unrecorded or self.mismatched)

    @property
    def to_move(self) -> list[Move]:
        return [m for m in self.moves if not m.redundant]

    @property
    def redundant(self) -> list[Move]:
        return [m for m in self.moves if m.redundant]


@dataclass(frozen=True)
class FoldResult:
    """What ``execute_fold`` did: counts, pruned dirs, and the manifest backup."""

    moved: int
    dropped: int
    removed_dirs: list[str]
    backup: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> Manifest:
    """Load and shape-check the master manifest; raise on missing/malformed.

    The fold must keep the ledger coherent, so it refuses to run without a
    well-formed one rather than silently move files off the record.
    """
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("messages"), dict):
        raise ValueError(f"{path}: not a manifest (missing 'messages' object)")
    return data


def plan_fold(
    dest: Path, messages: dict[str, Row], aliases: dict[str, str]
) -> FoldPlan:
    """Compute every move needed to fold subdomain folders under ``dest``.

    ``messages`` is the manifest's ``messages`` map (msgid → row). Pure: it
    reads the tree and the manifest but mutates nothing.
    """
    plan = FoldPlan()
    for domain_dir in sorted(p for p in dest.iterdir() if p.is_dir()):
        domain = domain_dir.name
        canonical = canonical_domain(domain, aliases)
        if canonical == domain:
            continue  # already canonical — nothing to fold
        plan.folds[domain] = canonical
        for eml in sorted(domain_dir.rglob("*.eml")):
            rel = eml.relative_to(dest)
            src_rel = str(rel)
            sub = rel.relative_to(domain)  # YYYY-MM/<id>.eml
            dst_rel = str(Path(canonical) / sub)
            msgid = eml.stem
            row = messages.get(msgid)
            if row is None:
                plan.unrecorded.append(src_rel)
                continue
            if row.get("path") != src_rel:
                plan.mismatched.append((src_rel, str(row.get("path"))))
                continue
            dst = dest / dst_rel
            redundant = False
            if dst.exists():
                if _sha256(dst) == _sha256(eml):
                    redundant = True
                else:
                    plan.conflicts.append((src_rel, dst_rel))
                    continue
            plan.moves.append(Move(msgid, canonical, src_rel, dst_rel, redundant))
    return plan


def _write_manifest(path: Path, manifest: Manifest) -> None:
    """Atomic, owner-only manifest write matching the archiver's format."""
    payload = {
        "version": manifest.get("version", 1),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "messages": dict(sorted(manifest["messages"].items())),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    os.chmod(path, 0o600)


def _prune_empty_dirs(dest: Path, domains: list[str]) -> list[str]:
    """Remove emptied source domain folders (and their empty month subdirs)."""
    removed: list[str] = []
    for domain in domains:
        root = dest / domain
        if not root.exists():
            continue
        for sub in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            if not any(sub.iterdir()):
                sub.rmdir()
                removed.append(str(sub.relative_to(dest)))
        if not any(root.iterdir()):
            root.rmdir()
            removed.append(domain)
    return removed


def execute_fold(
    dest: Path, manifest_path: Path, manifest: Manifest, plan: FoldPlan
) -> FoldResult:
    """Apply ``plan``: back up the manifest, move files, rewrite rows.

    Refuses a non-``ok`` plan (caller is expected to surface the blockers).
    Returns a stats dict. The manifest backup is written before any move so
    the prior ledger is always recoverable.
    """
    if not plan.ok:
        raise ValueError("refusing to execute a plan with unresolved blockers")
    messages = manifest["messages"]
    backup = manifest_path.with_suffix(manifest_path.suffix + ".prefold.bak")
    shutil.copy2(manifest_path, backup)

    moved = dropped = 0
    for move in plan.moves:
        src = dest / move.src_rel
        if move.redundant:
            os.remove(src)  # byte-identical copy already at the target
            dropped += 1
        else:
            dst = dest / move.dst_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, dst)  # atomic within the archive filesystem
            moved += 1
        row = messages[move.msgid]
        row["domain"] = move.canonical
        row["path"] = move.dst_rel

    _write_manifest(manifest_path, manifest)
    removed_dirs = _prune_empty_dirs(dest, list(plan.folds))
    return FoldResult(
        moved=moved, dropped=dropped, removed_dirs=removed_dirs, backup=str(backup)
    )
