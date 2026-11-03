"""Tests for env path/account helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from wicket import domains, env, exclusions
from wicket.env import (
    ACCOUNT_ALIASES_FILENAME,
    account_aliases_path,
    identities,
    load_account_aliases,
    resolve_account,
)

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


# --- where the mail root and the maps are found ----------------------------


def test_no_wicket_variable_reaches_a_test() -> None:
    # conftest unsets them; an installed shell points them at the owner's live maps.
    assert [name for name in os.environ if name.startswith("WICKET_")] == []


def _mail_root_in_a_fresh_process(home: Path, mail_root: str | None) -> str:
    """``env.MAIL_ROOT`` as a new process binds it (it is read once, at import)."""
    environ = {k: v for k, v in os.environ.items() if not k.startswith("WICKET_")}
    environ.update(HOME=str(home), PYTHONPATH=str(Path(env.__file__).parents[1]))
    if mail_root is not None:
        environ[env.DATA_DIR_ENV_VAR] = mail_root
    done = subprocess.run(
        [sys.executable, "-c", "from wicket import env; print(env.MAIL_ROOT)"],
        capture_output=True,
        text=True,
        env=environ,
        check=True,
        timeout=60,
    )
    return done.stdout.strip()


def test_the_mail_root_defaults_to_dot_data_mail(tmp_path: Path) -> None:
    got = _mail_root_in_a_fresh_process(tmp_path, None)
    assert got == str(tmp_path / ".data" / "mail")


def test_a_tilde_in_the_mail_root_override_is_expanded(tmp_path: Path) -> None:
    # A quoted "~/..." reaches wicket unexpanded; it must still mean the home dir.
    got = _mail_root_in_a_fresh_process(tmp_path, "~/vault/mail")
    assert got == str(tmp_path / "vault" / "mail")


@pytest.mark.parametrize(
    ("path_of", "name", "old_name"),
    [
        (
            account_aliases_path,
            "WICKET_ACCOUNT_ALIASES",
            "WICKET_ACCOUNT_ALIASES_FILE",
        ),
        (
            domains.domain_routes_path,
            "WICKET_DOMAIN_ROUTES",
            "WICKET_DOMAIN_ROUTES_FILE",
        ),
        (
            domains.domain_aliases_path,
            "WICKET_DOMAIN_ALIASES",
            "WICKET_DOMAIN_ALIASES_FILE",
        ),
        (
            exclusions.excluded_lists_path,
            "WICKET_EXCLUDED_LISTS",
            "WICKET_EXCLUDED_LISTS_FILE",
        ),
    ],
)
def test_each_map_override_has_its_new_name_and_not_its_old_one(
    path_of: Callable[[Path], Path],
    name: str,
    old_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(name, raising=False)
    default = path_of(tmp_path)
    monkeypatch.setenv(old_name, str(tmp_path / "old.json"))
    assert path_of(tmp_path) == default  # the old name is not read
    monkeypatch.setenv(name, str(tmp_path / "new.json"))
    assert path_of(tmp_path) == tmp_path / "new.json"
