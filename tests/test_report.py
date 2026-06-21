"""Tests for the read-only manifest reports."""

from __future__ import annotations

from pathlib import Path

from wicket.manifest import shard_path, write_shard
from wicket.report.summary import addresses, all_addresses, sender_counts, summary


def test_summary_counts(tmp_path: Path) -> None:
    write_shard(
        shard_path(tmp_path, 2024),
        {
            "1@x": {
                "id": "1@x",
                "from": "a@acme.com",
                "downloaded": True,
                "deleted": True,
            },
            "2@x": {
                "id": "2@x",
                "from": "a@acme.com",
                "to": ["you@g.com"],
                "downloaded": True,
            },
            "3@x": {"id": "3@x", "from": "b@globex.com", "to": ["you@g.com"]},
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
            "1@x": {"id": "1@x", "from": "a@acme.com", "to": ["you@gmail.com"]},
            "2@x": {"id": "2@x", "from": "a@acme.com", "to": ["you@gmail.com"]},
            "3@x": {"id": "3@x", "from": "Bob <b@globex.com>", "to": ["a@acme.com"]},
        },
    )
    assert sender_counts(tmp_path) == [("a@acme.com", 2), ("b@globex.com", 1)]


def test_all_addresses_union_of_from_and_to(tmp_path: Path) -> None:
    write_shard(
        shard_path(tmp_path, 2024),
        {
            "1@x": {"id": "1@x", "from": "a@acme.com", "to": ["you@gmail.com"]},
            "2@x": {"id": "2@x", "from": "Bob <b@globex.com>", "to": ["a@acme.com"]},
        },
    )
    assert all_addresses(tmp_path) == [
        "a@acme.com",
        "b@globex.com",
        "you@gmail.com",
    ]
