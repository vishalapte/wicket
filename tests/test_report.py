"""Tests for the read-only manifest reports."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from wicket.manifest import shard_path, write_shard
from wicket.root.lib.report import (
    account_counts,
    addresses,
    all_addresses,
    bucket_rows,
    by_year,
    last_download,
    sender_counts,
    summary,
)


def test_summary_counts(tmp_path: Path) -> None:
    write_shard(
        shard_path(tmp_path, 2024),
        {
            "1@x": {
                "id": "1@x",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "a@acme.com",
                "downloaded": True,
                "deleted": True,
            },
            "2@x": {
                "id": "2@x",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "a@acme.com",
                "to": ["you@g.com"],
                "downloaded": True,
            },
            "3@x": {
                "id": "3@x",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "b@globex.com",
                "to": ["you@g.com"],
            },
        },
    )
    s = summary(tmp_path)
    assert s["messages"] == 3
    assert (s["downloaded"], s["downloaded_gone"], s["downloaded_present"]) == (2, 1, 1)
    assert s["observed_only"] == 1
    assert s["senders"] == 2
    assert s["addresses"] == 3  # a@acme, b@globex, you@g


def test_addresses_parses_every_format() -> None:
    assert addresses("billing@acme.com") == ["billing@acme.com"]
    assert addresses("Acme <Billing@Acme.com>") == ["billing@acme.com"]
    assert addresses(["a@x.com", "b@y.com"]) == ["a@x.com", "b@y.com"]
    assert addresses("Undisclosed-Recipient, <>") == []
    assert addresses(None) == []


def test_sender_counts_descending(tmp_path: Path) -> None:
    write_shard(
        shard_path(tmp_path, 2024),
        {
            "1@x": {
                "id": "1@x",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "a@acme.com",
                "to": ["you@gmail.com"],
            },
            "2@x": {
                "id": "2@x",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "a@acme.com",
                "to": ["you@gmail.com"],
            },
            "3@x": {
                "id": "3@x",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "Bob <b@globex.com>",
                "to": ["a@acme.com"],
            },
        },
    )
    assert sender_counts(tmp_path) == [("a@acme.com", 2), ("b@globex.com", 1)]


def test_all_addresses_union_of_from_and_to(tmp_path: Path) -> None:
    write_shard(
        shard_path(tmp_path, 2024),
        {
            "1@x": {
                "id": "1@x",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "a@acme.com",
                "to": ["you@gmail.com"],
            },
            "2@x": {
                "id": "2@x",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "Bob <b@globex.com>",
                "to": ["a@acme.com"],
            },
        },
    )
    assert all_addresses(tmp_path) == [
        "a@acme.com",
        "b@globex.com",
        "you@gmail.com",
    ]


# --- bucket view (a query across stores, not a relocation) ----------------


def test_bucket_rows_folds_the_domain_and_claims_only_its_own(tmp_path: Path) -> None:
    routes = {"delta.com": "travel", "travel": "travel", "ikea.com": "shopping"}
    folds = {"*.delta.com": "delta.com", "delta.com": "delta.com"}
    store = tmp_path / "manifest"
    write_shard(
        store / "2026.jsonl",
        {
            "a": {
                "id": "a",
                "date": "2026-01-01T00:00:00+00:00",
                "from": "news@t.delta.com",
                "downloaded": True,
            },
            "b": {
                "id": "b",
                "date": "2026-01-01T00:00:00+00:00",
                "from": "offers@ikea.com",
                "downloaded": True,
            },
            "c": {
                "id": "c",
                "date": "2026-01-01T00:00:00+00:00",
                "from": "them@acme.com",
                "downloaded": False,
            },
        },
    )

    got = bucket_rows(store, "travel", routes, folds)
    assert [(domain, row["id"]) for domain, row in got] == [("delta.com", "a")]


def test_an_excluded_row_is_counted_apart_and_listed_nowhere(tmp_path: Path) -> None:
    write_shard(
        shard_path(tmp_path, 2024),
        {
            "1@x": {
                "id": "1@x",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "a@acme.com",
                "to": ["you@g.com"],
                "downloaded": True,
            },
            "1@x|gmail:2": {
                "id": "1@x|gmail:2",
                "date": "2024-01-01T00:00:00+00:00",
                "from": "copy@dup.com",
                "to": ["you@g.com"],
                "excluded": "duplicate of 1@x",
            },
        },
    )
    s = summary(tmp_path)
    assert (s["messages"], s["excluded"], s["observed_only"]) == (1, 1, 0)
    assert sender_counts(tmp_path) == [("a@acme.com", 1)]
    assert "copy@dup.com" not in all_addresses(tmp_path)


def test_observed_only_leaves_out_a_message_deleted_before_download(
    tmp_path: Path,
) -> None:
    write_shard(
        shard_path(tmp_path, 2026),
        {
            "1@x": {
                "id": "1@x",
                "date": "2026-01-01T00:00:00+00:00",
                "from": "a@acme.com",
            },
            "2@x": {
                "id": "2@x",
                "date": "2026-01-01T00:00:00+00:00",
                "from": "a@acme.com",
                "deleted": True,
            },
        },
    )
    s = summary(tmp_path)
    assert (s["messages"], s["observed_only"]) == (2, 1)


def test_by_year_counts_local_and_not_local_per_shard(tmp_path: Path) -> None:
    write_shard(
        shard_path(tmp_path, 2025),
        {
            "1@x": {
                "id": "1@x",
                "date": "2025-01-01T00:00:00+00:00",
                "downloaded": True,
            },
            "2@x": {"id": "2@x", "date": "2025-01-01T00:00:00+00:00"},
            "3@x": {
                "id": "3@x",
                "date": "2025-01-01T00:00:00+00:00",
                "deleted": True,
            },  # gone before download: neither
            "4@x": {
                "id": "4@x",
                "date": "2025-01-01T00:00:00+00:00",
                "list_id": "news.example.com",
            },  # excluded: neither
            "5@x": {
                "id": "5@x",
                "date": "2025-01-01T00:00:00+00:00",
                "excluded": "duplicate of 1@x",
            },  # excluded: neither
            "6@x": {
                "id": "6@x",
                "date": "2025-01-01T00:00:00+00:00",
            },  # also in 2026's shard, which wins
        },
    )
    write_shard(
        shard_path(tmp_path, 2026),
        {
            "6@x": {
                "id": "6@x",
                "date": "2026-01-01T00:00:00+00:00",
                "downloaded": True,
            },
            "7@x": {
                "id": "7@x",
                "date": "2026-01-01T00:00:00+00:00",
                "downloaded": True,
                "deleted": True,
            },  # only copy: local
        },
    )
    assert by_year(tmp_path, {"news.example.com"}) == {2025: (1, 1), 2026: (2, 0)}


def test_account_counts_sorts_each_row_once(tmp_path: Path) -> None:
    write_shard(
        shard_path(tmp_path, 2025),
        {
            "1@x": {
                "id": "1@x",
                "date": "2025-01-01T00:00:00+00:00",
                "downloaded": True,
            },
            "2@x": {
                "id": "2@x",
                "date": "2025-01-01T00:00:00+00:00",
            },  # no from: still not downloaded
            "3@x": {
                "id": "3@x",
                "date": "2025-01-01T00:00:00+00:00",
                "deleted": True,
            },  # gone before download: none
            "4@x": {
                "id": "4@x",
                "date": "2025-01-01T00:00:00+00:00",
                "list_id": "news.example.com",
                "downloaded": True,
            },
            "5@x": {
                "id": "5@x",
                "date": "2025-01-01T00:00:00+00:00",
                "excluded": "duplicate of 1@x",
            },
            "6@x": {
                "id": "6@x",
                "date": "2025-01-01T00:00:00+00:00",
            },  # also in 2026's shard, which wins
        },
    )
    write_shard(
        shard_path(tmp_path, 2026),
        {
            "6@x": {
                "id": "6@x",
                "date": "2026-01-01T00:00:00+00:00",
                "downloaded": True,
            },
            "7@x": {
                "id": "7@x",
                "date": "2026-01-01T00:00:00+00:00",
                "downloaded": True,
                "deleted": True,
            },  # only copy
        },
    )
    assert account_counts(tmp_path, {"news.example.com"}) == {
        "downloaded": 3,
        "excluded": 2,
        "not_downloaded": 1,
    }
    years = by_year(tmp_path, {"news.example.com"})
    assert sum(local for local, _ in years.values()) == 3  # the same rule
    assert sum(not_local for _, not_local in years.values()) == 1


def _file(path: Path, when: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (when.timestamp(), when.timestamp()))


def test_last_download_is_the_newest_eml_in_local_time(tmp_path: Path) -> None:
    archive, staging = tmp_path / "archive", tmp_path / "staging"
    _file(archive / "acme.com" / "2026-10" / "1@x.eml", datetime(2026, 10, 30, 9, 5))
    _file(staging / "t1" / "2@x@20261103.eml", datetime(2026, 11, 3, 17, 1))
    _file(archive / "acme.com" / "notes.txt", datetime(2026, 12, 1, 0, 0))  # not .eml
    assert last_download([archive, staging]) == "2026-11-03 17:01"
    assert last_download([archive]) == "2026-10-30 09:05"


def test_last_download_none_when_no_eml(tmp_path: Path) -> None:
    (tmp_path / "archive").mkdir()
    assert last_download([tmp_path / "archive", tmp_path / "staging"]) is None
