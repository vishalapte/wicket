"""Marker-delimited generated blocks in a repo-root README.

One home for the splice: a begin marker, an end marker, and a rewrite that moves only the
bytes between them. The CLI-tree block and `graph.py`'s `## Graphs` use it. It is generic over
the section text and knows nothing of how that text was rendered.
"""

from __future__ import annotations

from pathlib import Path


def splice(existing: str, begin: str, end: str, section: str) -> str:
    """`existing` with the `begin`..`end` block replaced by `section`, or unchanged.

    `section` carries its own markers; the caller renders them.

    Idempotent: splicing the result again reproduces it exactly.

    A file missing either marker is returned unchanged; this never appends. A block exists
    only once a person seeds its empty marker pair.
    """
    if begin in existing and end in existing:
        start = existing.index(begin)
        stop = existing.index(end) + len(end)
        return existing[:start] + section + existing[stop:]
    return existing


def is_stale(readme: Path, begin: str, end: str, section: str) -> bool:
    """True when `readme`'s block is missing or disagrees with `section`.

    Compares the whole spliced file, so a damaged marker or a drifted block reads as stale:
    it answers whether `--write` would change this file.
    """
    existing = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    return splice(existing, begin, end, section) != existing
