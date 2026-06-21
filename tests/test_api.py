"""Tests for the per-verb api layer and the public library surface."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import wicket.config as config
from wicket.catalog import api as catalog_api
from wicket.fetch import api as fetch_api
from wicket.fetch.api import held_messages
from wicket.manifest import shard_path, write_shard
from wicket.report.api import addresses, manifest, report, senders


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


# --- report api over a seeded store --------------------------------------


def test_report_summarizes_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAIL_ROOT", _seed(tmp_path))
    counts = report("you@gmail.com")
    assert counts["messages"] == 3
    assert counts["downloaded"] == 2
    assert counts["observed_only"] == 1


def test_senders_ranked_by_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAIL_ROOT", _seed(tmp_path))
    got = dict(senders("you@gmail.com"))
    assert got == {"fares@delta.com": 1, "sales@acme.com": 1, "news@delta.com": 1}


def test_addresses_distinct_and_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAIL_ROOT", _seed(tmp_path))
    assert addresses("you@gmail.com") == [
        "fares@delta.com",
        "news@delta.com",
        "sales@acme.com",
        "you@gmail.com",
    ]


# --- account / state resolution ------------------------------------------


def test_resolve_account_arg_env_discover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(config.ACCOUNT_ENV_VAR, raising=False)
    # explicit arg wins, and is normalized (+tag stripped, lowercased)
    assert config.resolve_account("You+tag@Gmail.com") == "you@gmail.com"
    # env var when no arg
    monkeypatch.setenv(config.ACCOUNT_ENV_VAR, "env@gmail.com")
    assert config.resolve_account(None) == "env@gmail.com"
    # discovery of the sole ~/mail account when neither is set
    monkeypatch.delenv(config.ACCOUNT_ENV_VAR, raising=False)
    monkeypatch.setattr(config, "MAIL_ROOT", _seed(tmp_path))
    assert config.resolve_account(None) == "you@gmail.com"


def test_resolve_account_raises_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(config.ACCOUNT_ENV_VAR, raising=False)
    monkeypatch.setattr(config, "MAIL_ROOT", tmp_path / "empty")
    with pytest.raises(ValueError, match="no account"):
        config.resolve_account(None)


def test_resolve_state_dir_is_per_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "state"
    monkeypatch.setattr(config, "DEFAULT_STATE_DIR", base)
    path = config.resolve_state_dir("You+tag@Gmail.com")
    assert path == base / "you@gmail.com"
    assert path.is_dir()
    # override replaces the base; the account segment still applies
    other = tmp_path / "elsewhere"
    assert config.resolve_state_dir("you@gmail.com", other) == other / "you@gmail.com"


# --- catalog() wiring (no real IMAP) -------------------------------------


def test_catalog_resolves_paths_and_passes_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAIL_ROOT", tmp_path / "mail")
    monkeypatch.setattr(config, "DEFAULT_STATE_DIR", tmp_path / "state")
    monkeypatch.delenv(config.ACCOUNT_ENV_VAR, raising=False)

    seen: dict[str, object] = {}

    def fake_load_credentials(path: Path, *, interactive: bool) -> tuple[str, str]:
        seen["creds_path"] = path
        seen["interactive"] = interactive
        return ("you@gmail.com", "pw")

    def fake_sweep(**kwargs: object) -> dict[str, int]:
        seen.update(kwargs)
        return {"messages": 0, "shards": 0, "removed": 0}

    monkeypatch.setattr(catalog_api, "load_credentials", fake_load_credentials)
    monkeypatch.setattr(catalog_api, "sweep", fake_sweep)

    catalog_api.catalog(
        "You@gmail.com", options=catalog_api.CatalogOptions(dry_run=True)
    )

    assert seen["interactive"] is False  # default
    assert seen["dry_run"] is True
    assert seen["store_dir"] == tmp_path / "mail" / "you@gmail.com" / "manifest"
    assert seen["creds_path"] == tmp_path / "state" / "you@gmail.com" / "imap.json"


# --- fetch() wiring + mutual exclusivity ----------------------------------


def test_fetch_options_require_exactly_one_of_domains_or_query() -> None:
    # The invariant lives in FetchOptions, so an ambiguous request cannot even
    # be constructed (no account, no IMAP, no monkeypatching needed).
    with pytest.raises(ValueError, match="exactly one"):
        fetch_api.FetchOptions()  # neither
    with pytest.raises(ValueError, match="exactly one"):
        fetch_api.FetchOptions(domains="a.com", query="x")  # both


def test_fetch_wires_query_and_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAIL_ROOT", tmp_path / "mail")
    monkeypatch.setattr(config, "DEFAULT_STATE_DIR", tmp_path / "state")
    monkeypatch.delenv(config.ACCOUNT_ENV_VAR, raising=False)

    seen: dict[str, object] = {}

    def fake_load_credentials(path: Path, *, interactive: bool) -> tuple[str, str]:
        seen["creds_path"] = path
        seen["interactive"] = interactive
        return ("you@gmail.com", "pw")

    def fake_download(**kwargs: object) -> dict[str, int]:
        seen.update(kwargs)
        return {"downloaded": 0, "failed": 0, "held": 0}

    monkeypatch.setattr(fetch_api, "load_credentials", fake_load_credentials)
    monkeypatch.setattr(fetch_api, "download", fake_download)

    fetch_api.fetch(
        "You@gmail.com",
        options=fetch_api.FetchOptions(domains="acme.com", dry_run=True),
    )

    assert seen["interactive"] is False  # default
    ctx = seen["ctx"]
    assert ctx.dry_run is True
    assert ctx.dest == tmp_path / "mail" / "you@gmail.com" / "archive"
    assert "acme.com" in str(seen["query"])
    assert seen["creds_path"] == tmp_path / "state" / "you@gmail.com" / "imap.json"


def test_sweep_years_incremental_full_and_explicit(tmp_path: Path) -> None:
    store = tmp_path / "manifest"
    # first run (empty store) -> None, so lib.sweep derives the full range
    assert catalog_api._sweep_years(store, catalog_api.CatalogOptions()) is None

    write_shard(shard_path(store, 2023), {"a@x": {"id": "a@x"}})
    write_shard(shard_path(store, 2025), {"b@x": {"id": "b@x"}})
    now = datetime.now(timezone.utc).year

    # incremental default: latest shard year through the current UTC year
    assert catalog_api._sweep_years(
        store, catalog_api.CatalogOptions()
    ) == list(range(2025, now + 1))
    # --full forces a complete sweep (None lets the worker derive the range)
    assert (
        catalog_api._sweep_years(store, catalog_api.CatalogOptions(full=True)) is None
    )
    # explicit years win over both
    assert catalog_api._sweep_years(
        store, catalog_api.CatalogOptions(years=[2024])
    ) == [2024]
