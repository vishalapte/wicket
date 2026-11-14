"""Tests for the idempotent local reconcile (ADR 0002)."""

from __future__ import annotations

from pathlib import Path

from wicket.manifest import read_shard, shard_path, write_shard
from wicket.reconcile import reconcile


def test_downloaded_reflects_disk(tmp_path: Path) -> None:
    store, dest = tmp_path / "store", tmp_path / "dest"
    write_shard(
        shard_path(store, 2024),
        {
            "a@x": {
                "id": "a@x",
                "thread_id": "t",
                "downloaded": True,
                "stem": "0123456789abcdef",
                "from": "billing@acme.com",
                "to": ["you@gmail.com"],
                "date": "2024-03-01T00:00:00+00:00",
            }
        },
    )
    stats = reconcile(store, dest)  # file absent -> downloaded False
    assert read_shard(shard_path(store, 2024))["a@x"]["downloaded"] is False
    assert stats["downloaded_fixed"] == 1

    (dest / "2024" / "03").mkdir(parents=True)
    (dest / "2024" / "03" / "0123456789abcdef.eml").write_bytes(b"x")
    reconcile(store, dest)  # file present -> downloaded True
    assert read_shard(shard_path(store, 2024))["a@x"]["downloaded"] is True


def test_idempotent(tmp_path: Path) -> None:
    store, dest = tmp_path / "store", tmp_path / "dest"
    write_shard(
        shard_path(store, 2024),
        {
            "a@x": {
                "id": "a@x",
                "thread_id": "t",
                "from": "billing@acme.com",
                "to": ["you@gmail.com"],
                "date": "2024-03-01T00:00:00+00:00",
            }
        },
    )
    reconcile(store, dest)
    first = shard_path(store, 2024).read_bytes()
    reconcile(store, dest)
    assert shard_path(store, 2024).read_bytes() == first
