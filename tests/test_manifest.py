"""Tests for the unified year-sharded store (ADR 0002)."""

from __future__ import annotations

from pathlib import Path

from wicket.manifest import (
    latest_shard_year,
    load_store,
    merge_catalog,
    merge_settlement,
    read_shard,
    shard_path,
    write_shard,
)


def test_load_store_merges_all_shards(tmp_path: Path) -> None:
    write_shard(shard_path(tmp_path, 2023), {"a@x": {"id": "a@x"}})
    write_shard(shard_path(tmp_path, 2024), {"b@x": {"id": "b@x"}})
    assert set(load_store(tmp_path)) == {"a@x", "b@x"}


def test_merge_settlement_updates_existing_and_adds_new() -> None:
    prior = {"a@x": {"id": "a@x", "from": "y@z.com"}}
    settlements = {
        "a@x": {"downloaded": True, "path": "p"},
        "b@x": {"id": "b@x", "downloaded": True},
    }
    merged = merge_settlement(prior, settlements)
    assert merged["a@x"]["from"] == "y@z.com"  # observation preserved
    assert merged["a@x"]["downloaded"] is True  # settlement merged in
    assert merged["b@x"]["downloaded"] is True  # new partial row added


def test_write_read_roundtrip_and_idempotent(tmp_path: Path) -> None:
    path = shard_path(tmp_path, 2024)
    rows = {
        "b@x": {"id": "b@x", "date": "2024-02-01T00:00:00+00:00"},
        "a@x": {"id": "a@x", "date": "2024-01-01T00:00:00+00:00"},
    }
    write_shard(path, rows)
    assert read_shard(path) == rows
    first = path.read_bytes()
    write_shard(path, read_shard(path))
    assert path.read_bytes() == first  # byte-identical rewrite
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_read_absent_shard_is_empty(tmp_path: Path) -> None:
    assert read_shard(shard_path(tmp_path, 1999)) == {}


def test_merge_refreshes_observation_and_preserves_settlement() -> None:
    prior = {
        "a@x": {
            "id": "a@x",
            "from": "old@acme.com",
            "downloaded": True,
            "path": "acme.com/2024-03/18f2.eml",
        }
    }
    observed = {"a@x": {"id": "a@x", "from": "new@acme.com", "subject": "Hi"}}
    merged = merge_catalog(prior, observed, complete=True)
    assert merged["a@x"]["from"] == "new@acme.com"  # observation refreshed
    assert merged["a@x"]["subject"] == "Hi"
    assert merged["a@x"]["downloaded"] is True  # settlement preserved
    assert merged["a@x"]["path"] == "acme.com/2024-03/18f2.eml"
    assert merged["a@x"]["deleted"] is False


def test_complete_catalog_keeps_downloaded_gone_marks_deleted() -> None:
    prior = {"a@x": {"id": "a@x", "downloaded": True, "path": "p.eml"}}
    merged = merge_catalog(prior, {}, complete=True)
    assert merged["a@x"]["deleted"] is True
    assert merged["a@x"]["downloaded"] is True


def test_complete_catalog_drops_gone_unheld() -> None:
    prior = {"a@x": {"id": "a@x", "downloaded": False}}
    merged = merge_catalog(prior, {}, complete=True)
    assert "a@x" not in merged  # gone and never kept -> no row


def test_incomplete_catalog_preserves_unseen_untouched() -> None:
    prior = {"a@x": {"id": "a@x", "downloaded": False, "from": "x@y.com"}}
    merged = merge_catalog(prior, {}, complete=False)
    assert merged["a@x"] == prior["a@x"]  # absence cannot prove deletion


def test_returning_message_clears_deleted() -> None:
    prior = {"a@x": {"id": "a@x", "deleted": True, "downloaded": True, "path": "p.eml"}}
    observed = {"a@x": {"id": "a@x", "from": "x@y.com"}}
    merged = merge_catalog(prior, observed, complete=True)
    assert merged["a@x"]["deleted"] is False
    assert merged["a@x"]["downloaded"] is True


def test_latest_shard_year(tmp_path: Path) -> None:
    assert latest_shard_year(tmp_path) is None  # empty store
    write_shard(shard_path(tmp_path, 2023), {"a@x": {"id": "a@x"}})
    write_shard(shard_path(tmp_path, 2025), {"b@x": {"id": "b@x"}})
    assert latest_shard_year(tmp_path) == 2025
