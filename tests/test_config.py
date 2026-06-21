"""Tests for config path/account helpers."""

from __future__ import annotations

from pathlib import Path

from wicket.config import discover_account


def test_discover_account_returns_sole_directory(tmp_path: Path) -> None:
    (tmp_path / "you@gmail.com").mkdir()
    assert discover_account(tmp_path) == "you@gmail.com"


def test_discover_account_none_when_empty_or_missing(tmp_path: Path) -> None:
    assert discover_account(tmp_path) is None
    assert discover_account(tmp_path / "nope") is None


def test_discover_account_none_when_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "a@x.com").mkdir()
    (tmp_path / "b@y.com").mkdir()
    assert discover_account(tmp_path) is None
