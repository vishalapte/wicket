"""Tests for the public library surface."""

from __future__ import annotations

from pathlib import Path

import pytest

import wicket.config as config
from wicket import held_messages, manifest
from wicket.manifest import shard_path, write_shard


def _seed(root: Path) -> Path:
    acct = "you@gmail.com"
    store = root / acct / "manifest"
    write_shard(
        shard_path(store, 2024),
        {
            "a@x": {
                "id": "a@x",
                "downloaded": True,
                "domain": "delta.com",
                "path": "delta.com/2024-03/1.eml",
            },
            "b@x": {
                "id": "b@x",
                "downloaded": True,
                "domain": "acme.com",
                "path": "acme.com/2024-03/2.eml",
            },
            "c@x": {"id": "c@x", "downloaded": False, "domain": "delta.com"},
        },
    )
    return root


def test_held_messages_filters_downloaded_and_domains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAIL_ROOT", _seed(tmp_path))
    got = list(held_messages(account="you@gmail.com", domains={"delta.com"}))
    assert len(got) == 1  # b@x is acme.com, c@x is not downloaded
    row, eml = got[0]
    assert row["id"] == "a@x"
    assert eml == tmp_path / "you@gmail.com" / "archive" / "delta.com/2024-03/1.eml"


def test_manifest_loads_whole_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAIL_ROOT", _seed(tmp_path))
    assert set(manifest("you@gmail.com")) == {"a@x", "b@x", "c@x"}
