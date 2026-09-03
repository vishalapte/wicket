"""Tests for env path/account helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wicket import env
from wicket.env import (
    ACCOUNT_ALIASES_FILENAME,
    account_aliases_path,
    discover_account,
    identities,
    load_account_aliases,
    resolve_account,
)


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


# --- account aliases, identities, and the closed-account guard -------------


def _aliases(root: Path, mapping: dict[str, list[str]]) -> Path:
    (root / ACCOUNT_ALIASES_FILENAME).write_text(json.dumps(mapping), encoding="utf-8")
    return root


def test_load_account_aliases_flattens_and_normalizes(tmp_path: Path) -> None:
    _aliases(tmp_path, {"You@Gmail.com": ["burner@duck.com", "Foo+tag@bar.com"]})
    got = load_account_aliases(account_aliases_path(tmp_path))
    assert got == {
        "you@gmail.com": "you@gmail.com",  # a primary maps to itself
        "burner@duck.com": "you@gmail.com",
        "foo@bar.com": "you@gmail.com",  # +tag stripped, case folded
    }


def test_load_account_aliases_absent_file_is_silent(tmp_path: Path) -> None:
    assert load_account_aliases(account_aliases_path(tmp_path)) == {}


def test_load_account_aliases_rejects_ambiguous_alias(tmp_path: Path) -> None:
    _aliases(tmp_path, {"a@x.com": ["shared@z.com"], "b@y.com": ["shared@z.com"]})
    with pytest.raises(ValueError, match="aliased to both"):
        load_account_aliases(account_aliases_path(tmp_path))


def test_load_account_aliases_rejects_non_address(tmp_path: Path) -> None:
    _aliases(tmp_path, {"a@x.com": ["not-an-address"]})
    with pytest.raises(ValueError, match="invalid alias address"):
        load_account_aliases(account_aliases_path(tmp_path))


def test_identities_covers_aliases_accounts_and_wildcard_domains(
    tmp_path: Path,
) -> None:
    mapping = load_account_aliases(
        _aliases(
            tmp_path,
            {"travel": ["hyatt@xcv.org"], "shopping": ["*@shop.you.com"]},
        )
        / ACCOUNT_ALIASES_FILENAME
    )
    mine = identities(mapping, accounts={"you@gmail.com", "travel", "shopping"})

    assert "hyatt@xcv.org" in mine  # a listed burner
    assert "you@gmail.com" in mine  # a store you own, never listed in the file
    assert "anything@shop.you.com" in mine  # any address under a catch-all
    assert "them@acme.com" not in mine  # a counterparty
    assert "travel" not in mine.addresses  # a bucket is a destination, not an address


def test_resolve_account_maps_an_alias_to_its_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "you@gmail.com").mkdir()
    _aliases(tmp_path, {"you@gmail.com": ["burner@duck.com"]})
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    # An address you merely receive at resolves to the account that owns it,
    # instead of minting a store of its own.
    assert resolve_account("burner@duck.com") == "you@gmail.com"


def test_resolve_account_rejects_an_unknown_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "you@gmail.com").mkdir()
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    with pytest.raises(ValueError, match="unknown account"):
        resolve_account("typo@gmial.com")  # a typo must not become a store


def test_resolve_account_bootstraps_when_no_account_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(env, "MAIL_ROOT", empty)
    # The set is closed only once it exists; a first account is still free.
    assert resolve_account("first@gmail.com") == "first@gmail.com"


# --- fail closed on an unmounted vault ------------------------------------


def test_require_mail_root_refuses_an_absent_root(tmp_path: Path) -> None:
    # A locked Cryptomator vault leaves no directory (or an empty mount point).
    # Treating that as "a fresh store" would mkdir an account and write mail in
    # PLAINTEXT underneath the vault. So: refuse, and say why.
    with pytest.raises(ValueError, match="not mounted"):
        env.require_mail_root(tmp_path / "vault" / "mail")


def test_require_mail_root_returns_a_present_root(tmp_path: Path) -> None:
    assert env.require_mail_root(tmp_path) == tmp_path


def test_resolve_account_fails_closed_when_the_root_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path / "gone")
    # Not "no account" — that would read as an empty store and bootstrap one.
    with pytest.raises(ValueError, match="not mounted"):
        env.resolve_account("you@gmail.com")


def test_no_store_is_created_when_the_root_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "gone"
    monkeypatch.setattr(env, "MAIL_ROOT", root)
    with pytest.raises(ValueError, match="not mounted"):
        env.resolve_account("you@gmail.com")
    assert not root.exists()  # nothing was conjured on the way out
