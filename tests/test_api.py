"""Tests for the per-verb api layer and the public library surface."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

import wicket.__main__ as root
from wicket import env
from wicket.manifest import shard_path, write_shard
from wicket.root import api as root_api
from wicket.root.api import (
    all_addresses,
    held_messages,
    manifest,
    report,
    sender_counts,
)
from wicket.root.lib.fetch import ThreadContext


def _seed(base: Path) -> Path:
    acct = "you@gmail.com"
    store = base / acct / "manifest"
    write_shard(
        shard_path(store, 2024),
        {
            "a@x": {
                "id": "a@x",
                "downloaded": True,
                "domain": "delta.com",
                "path": "delta.com/2024-03/1.eml",
                "from": "fares@delta.com",
                "to": ["you@gmail.com"],
            },
            "b@x": {
                "id": "b@x",
                "downloaded": True,
                "domain": "acme.com",
                "path": "acme.com/2024-03/2.eml",
                "from": "sales@acme.com",
                "to": ["you@gmail.com"],
            },
            "c@x": {
                "id": "c@x",
                "downloaded": False,
                "domain": "delta.com",
                "from": "news@delta.com",
                "to": ["you@gmail.com"],
            },
        },
    )
    return base


def test_held_messages_filters_downloaded_and_domains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", _seed(tmp_path))
    got = list(held_messages(account="you@gmail.com", domains={"delta.com"}))
    assert len(got) == 1  # b@x is acme.com, c@x is not downloaded
    row, eml = got[0]
    assert row["id"] == "a@x"
    assert eml == tmp_path / "you@gmail.com" / "archive" / "delta.com/2024-03/1.eml"


def test_manifest_loads_whole_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", _seed(tmp_path))
    assert set(manifest("you@gmail.com")) == {"a@x", "b@x", "c@x"}


# --- report api over a seeded store --------------------------------------


def test_report_summarizes_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", _seed(tmp_path))
    counts = report("you@gmail.com")
    assert counts["messages"] == 3
    assert counts["downloaded"] == 2
    assert counts["observed_only"] == 1


def test_senders_ranked_by_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", _seed(tmp_path))
    got = dict(sender_counts("you@gmail.com"))
    assert got == {"fares@delta.com": 1, "sales@acme.com": 1, "news@delta.com": 1}


def test_addresses_distinct_and_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", _seed(tmp_path))
    assert all_addresses("you@gmail.com") == [
        "fares@delta.com",
        "news@delta.com",
        "sales@acme.com",
        "you@gmail.com",
    ]


# --- account / state resolution ------------------------------------------


def _account_folders(base: Path, *names: str, credential: bool = True) -> None:
    """``base/accounts/<name>/``, each holding an ``imap.json`` link unless not
    ``credential``."""
    for name in names:
        folder = base / "accounts" / name
        folder.mkdir(parents=True)
        if credential:
            (folder / "imap.json").symlink_to(base / "gmail" / name / "imap.json")


@pytest.fixture(name="state")
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A present, empty mail root (so any account bootstraps), no $WICKET_ACCOUNT, and a
    ~/.config/wicket with no account folder yet."""
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(env, "MAIL_ROOT", empty)
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    return tmp_path / "wicket"


def test_resolve_account_takes_the_flag_first_and_normalizes_it(state: Path) -> None:
    _account_folders(state, "a@x.com", "b@y.com")
    assert env.resolve_account("You+tag@Gmail.com") == "you@gmail.com"


def test_resolve_account_takes_the_env_var_without_the_flag(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _account_folders(state, "a@x.com", "b@y.com")
    monkeypatch.setenv(env.ACCOUNT_ENV_VAR, "env@gmail.com")
    assert env.resolve_account(None) == "env@gmail.com"
    assert env.resolve_account("flag@gmail.com") == "flag@gmail.com"  # the flag wins


def test_resolve_account_takes_the_only_account_folder(state: Path) -> None:
    _account_folders(state, "you@gmail.com")
    _account_folders(state, "empty@gmail.com", credential=False)  # no imap.json: none
    assert env.resolve_account(None) == "you@gmail.com"


@pytest.mark.parametrize("names", [(), ("a@x.com", "b@y.com")])
def test_resolve_account_refuses_with_no_folder_or_several(
    state: Path, names: tuple[str, ...]
) -> None:
    _account_folders(state, *names)
    with pytest.raises(ValueError) as refused:
        env.resolve_account(None)
    said = str(refused.value)
    assert said.startswith(f"no account: {len(names)} folders in {state / 'accounts'} ")
    assert "pass --mail-account" in said


def test_resolve_account_no_longer_takes_the_only_store(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One store under the mail root is not an account folder: it is not the default."""
    monkeypatch.setattr(env, "MAIL_ROOT", _seed(tmp_path / "seeded"))
    with pytest.raises(ValueError, match="no account: 0 folders"):
        env.resolve_account(None)
    _account_folders(state, "you@gmail.com")
    assert env.resolve_account(None) == "you@gmail.com"


@pytest.mark.parametrize("verb", ["report", "catalog", "fetch"])
def test_a_verb_without_the_flag_exits_2_unless_one_folder_holds_imap_json(
    verb: str, state: Path, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    _account_folders(state, "a@x.com", "b@y.com")
    argv = [verb] if verb == "report" else [verb, "--non-interactive"]
    code, out, err = run_cli(root.main, argv)
    assert (code, out) == (2, "")
    assert "no account: 2 folders in " in err and "pass --mail-account" in err


def test_resolve_state_dir_is_the_accounts_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "wicket"
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", base)
    (base / "accounts" / "you@gmail.com").mkdir(parents=True)
    path = env.resolve_state_dir("You+tag@Gmail.com")
    assert path == base / "accounts" / "you@gmail.com" and path.is_dir()
    # override replaces the base; accounts/<account> still applies
    other = tmp_path / "elsewhere"
    (other / "accounts" / "you@gmail.com").mkdir(parents=True)
    assert (
        env.resolve_state_dir("you@gmail.com", other)
        == other / "accounts" / "you@gmail.com"
    )


def test_resolve_state_dir_never_creates_and_a_missing_folder_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "wicket"
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", base)
    with pytest.raises(env.NotLinked):
        env.resolve_state_dir("you@gmail.com")
    (base / "you@gmail.com").mkdir(parents=True)  # the old layout is no account
    with pytest.raises(env.NotLinked):
        env.resolve_state_dir("you@gmail.com")
    (base / "accounts").mkdir()
    (base / "accounts" / "you@gmail.com").symlink_to(tmp_path / "gone")  # to nothing
    with pytest.raises(env.NotLinked):
        env.resolve_state_dir("you@gmail.com")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["wicket"]  # nothing made


def test_a_folder_without_imap_json_still_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolving a named account asks only for its folder: the credential is then
    prompted for or refused, never skipped (owner, 2026-10-28)."""
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    (tmp_path / "wicket" / "accounts" / "you@gmail.com").mkdir(parents=True)
    path = env.resolve_state_dir("you@gmail.com")
    assert not (path / "imap.json").exists()
    assert env.account_dirs() == []  # yet it is not listed as an account


# --- catalog() wiring (no real IMAP) -------------------------------------


def test_catalog_resolves_paths_and_passes_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mail").mkdir(exist_ok=True)
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path / "mail")
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "state")
    (tmp_path / "state" / "accounts" / "you@gmail.com").mkdir(parents=True)
    (tmp_path / "state" / "accounts" / "you@gmail.com" / "imap.json").symlink_to(
        tmp_path / "gmail" / "you@gmail.com" / "imap.json"
    )
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)

    seen: dict[str, object] = {}

    def fake_load_credentials(path: Path, *, interactive: bool) -> tuple[str, str]:
        seen["creds_path"] = path
        seen["interactive"] = interactive
        return ("you@gmail.com", "pw")

    def fake_sweep(**kwargs: object) -> dict[str, int]:
        seen.update(kwargs)
        return {"messages": 0, "shards": 0, "removed": 0}

    monkeypatch.setattr(root_api, "load_credentials", fake_load_credentials)
    monkeypatch.setattr(root_api, "sweep", fake_sweep)

    root_api.catalog("You@gmail.com", options=root_api.CatalogOptions(dry_run=True))

    assert seen["interactive"] is False  # default
    assert seen["dry_run"] is True
    assert seen["store_dir"] == tmp_path / "mail" / "you@gmail.com" / "manifest"
    creds = tmp_path / "state" / "accounts" / "you@gmail.com" / "imap.json"
    assert seen["creds_path"] == creds


# --- fetch() wiring + mutual exclusivity ----------------------------------


def test_fetch_options_take_at_most_one_of_domains_or_query() -> None:
    # The invariant lives in FetchOptions, so an ambiguous request cannot even
    # be constructed (no account, no IMAP, no monkeypatching needed). Neither is
    # a request too: every message not yet got, year by year (plan rule 1).
    assert root_api.FetchOptions().query is None  # neither
    with pytest.raises(ValueError, match="at most one"):
        root_api.FetchOptions(domains="a.com", query="x")  # both


def test_fetch_wires_query_and_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mail").mkdir(exist_ok=True)
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path / "mail")
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "state")
    (tmp_path / "state" / "accounts" / "you@gmail.com").mkdir(parents=True)
    (tmp_path / "state" / "accounts" / "you@gmail.com" / "imap.json").symlink_to(
        tmp_path / "gmail" / "you@gmail.com" / "imap.json"
    )
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)

    seen: dict[str, object] = {}

    def fake_load_credentials(path: Path, *, interactive: bool) -> tuple[str, str]:
        seen["creds_path"] = path
        seen["interactive"] = interactive
        return ("you@gmail.com", "pw")

    def fake_download(**kwargs: object) -> dict[str, int]:
        seen.update(kwargs)
        return {"downloaded": 0, "failed": 0, "got": 0}

    monkeypatch.setattr(root_api, "load_credentials", fake_load_credentials)
    monkeypatch.setattr(root_api, "download", fake_download)

    root_api.fetch(
        "You@gmail.com",
        options=root_api.FetchOptions(domains="acme.com", dry_run=True),
    )

    assert seen["interactive"] is False  # default
    ctx: ThreadContext = seen["ctx"]  # type: ignore[assignment]
    assert ctx.dry_run is True
    assert ctx.dest == tmp_path / "mail" / "you@gmail.com" / "archive"  # reconciled
    assert ctx.staging_dir == tmp_path / "mail" / "you@gmail.com" / "staging"
    assert ctx.duplicates_dir == tmp_path / "mail" / ".duplicates" / "you@gmail.com"
    assert "acme.com" in str(seen["search"])
    creds = tmp_path / "state" / "accounts" / "you@gmail.com" / "imap.json"
    assert seen["creds_path"] == creds


def test_sweep_years_incremental_full_and_explicit(tmp_path: Path) -> None:
    store = tmp_path / "manifest"
    # first run (empty store) -> None, so lib.sweep derives the full range
    assert root_api._sweep_years(store, root_api.CatalogOptions()) is None

    write_shard(shard_path(store, 2023), {"a@x": {"id": "a@x"}})
    write_shard(shard_path(store, 2025), {"b@x": {"id": "b@x"}})
    now = datetime.now(timezone.utc).year

    # incremental default: latest shard year through the current UTC year
    assert root_api._sweep_years(store, root_api.CatalogOptions()) == list(
        range(2025, now + 1)
    )
    # --full forces a complete sweep (None lets the worker derive the range)
    assert root_api._sweep_years(store, root_api.CatalogOptions(full=True)) is None
    # explicit years win over both
    assert root_api._sweep_years(store, root_api.CatalogOptions(years=[2024])) == [2024]


def test_fetch_reconciles_as_the_account_not_the_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account folder named for its store logs in as another address (Fastmail's
    vishalapte@fastmail.com files into vishal@enine.com): reconcile's *you* is the
    account, as ingest --staged has it."""
    (tmp_path / "mail" / "you@enine.com").mkdir(parents=True)
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path / "mail")
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "state")
    (tmp_path / "state" / "accounts" / "you@enine.com").mkdir(parents=True)
    (tmp_path / "state" / "accounts" / "you@enine.com" / "imap.json").symlink_to(
        tmp_path / "gmail" / "you@enine.com" / "imap.json"
    )
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    seen: dict[str, object] = {}

    def fake_load_credentials(_path: Path, **_kw: object) -> tuple[str, str]:
        return ("login@fastmail.com", "pw")

    def fake_download(**kwargs: object) -> dict[str, int]:
        seen.update(kwargs)
        return {"downloaded": 0, "failed": 0, "got": 0}

    monkeypatch.setattr(root_api, "load_credentials", fake_load_credentials)
    monkeypatch.setattr(root_api, "download", fake_download)

    root_api.fetch(
        "you@enine.com", options=root_api.FetchOptions(domains="a.com", dry_run=True)
    )

    ctx: ThreadContext = seen["ctx"]  # type: ignore[assignment]
    assert ctx.account == "you@enine.com"
