"""Tests for the config verb: the generic map engine (lib), the per-resource
validation + path resolution (api), and the argparse tree (cli).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wicket import env
from wicket.config import api, cli
from wicket.config.lib import (
    create_entry,
    delete_entry,
    list_entries,
    read_map,
    update_entry,
    write_map,
)

# --- lib: the generic {primary: [items]} engine ----------------------------


def test_list_entries_empty_when_file_absent(tmp_path: Path) -> None:
    assert list_entries(tmp_path / "nope.json") == {}


def test_create_entry_writes_sorted(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    result = create_entry(path, "travel", ["ba@xcv.org", "hyatt@xcv.org"])
    assert result == {"travel": ["ba@xcv.org", "hyatt@xcv.org"]}
    assert json.loads(path.read_text()) == {"travel": ["ba@xcv.org", "hyatt@xcv.org"]}


def test_create_entry_refuses_existing_primary(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    create_entry(path, "travel", ["ba@xcv.org"])
    with pytest.raises(ValueError, match="already exists"):
        create_entry(path, "travel", ["delta@xcv.org"])


def test_update_entry_adds_and_removes(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    create_entry(path, "travel", ["ba@xcv.org", "hyatt@xcv.org"])
    result = update_entry(path, "travel", add=["delta@xcv.org"], remove=["ba@xcv.org"])
    assert result == {"travel": ["delta@xcv.org", "hyatt@xcv.org"]}


def test_update_entry_refuses_unknown_primary(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        update_entry(tmp_path / "map.json", "travel", add=["x@y.com"], remove=[])


def test_delete_entry_one_item_keeps_primary(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    create_entry(path, "travel", ["ba@xcv.org", "hyatt@xcv.org"])
    result = delete_entry(path, "travel", "ba@xcv.org")
    assert result == {"travel": ["hyatt@xcv.org"]}


def test_delete_entry_last_item_drops_primary(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    create_entry(path, "travel", ["ba@xcv.org"])
    result = delete_entry(path, "travel", "ba@xcv.org")
    assert result == {}


def test_delete_entry_whole_primary(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    create_entry(path, "travel", ["ba@xcv.org", "hyatt@xcv.org"])
    assert delete_entry(path, "travel") == {}


def test_delete_entry_unknown_item_raises(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    create_entry(path, "travel", ["ba@xcv.org"])
    with pytest.raises(ValueError, match="not found"):
        delete_entry(path, "travel", "nope@xcv.org")


def test_read_map_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        read_map(path)


def test_write_map_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    write_map(path, {"b": ["y", "x"], "a": ["z"]})
    text = path.read_text(encoding="utf-8")
    assert text == json.dumps({"a": ["z"], "b": ["x", "y"]}, indent=2) + "\n"


# --- api: validation + path resolution per resource -------------------------


def test_account_aliases_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "you@gmail.com").mkdir()
    api.create("account-aliases", "travel", ["hyatt@xcv.org"])
    assert api.list_map("account-aliases") == {"travel": ["hyatt@xcv.org"]}
    assert env.load_account_aliases() == {
        "travel": "travel",
        "hyatt@xcv.org": "travel",
    }


def test_account_aliases_rejects_invalid_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "you@gmail.com").mkdir()
    with pytest.raises(ValueError, match="invalid primary address"):
        api.create("account-aliases", "not an address", ["x@y.com"])


def test_account_aliases_rejects_invalid_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "you@gmail.com").mkdir()
    with pytest.raises(ValueError, match="invalid address"):
        api.create("account-aliases", "travel", ["not-an-address"])


def test_domain_routes_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "you@gmail.com").mkdir()
    api.create("domain-routes", "shopping", ["ikea.com"])
    api.update("domain-routes", "shopping", add=["llbean.com"], remove=[])
    assert api.list_map("domain-routes") == {"shopping": ["ikea.com", "llbean.com"]}


def test_domain_aliases_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "you@gmail.com").mkdir()
    api.create("domain-aliases", "delta.com", ["*.delta.com"])
    assert api.list_map("domain-aliases") == {"delta.com": ["*.delta.com"]}


def test_unknown_resource_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "you@gmail.com").mkdir()
    with pytest.raises(ValueError, match="unknown config resource"):
        api.list_map("nope")


def test_account_aliases_file_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "you@gmail.com").mkdir()
    custom = tmp_path.parent / "custom-aliases.json"
    monkeypatch.setenv(env.ACCOUNT_ALIASES_FILE_ENV_VAR, str(custom))
    api.create("account-aliases", "travel", ["hyatt@xcv.org"])
    assert custom.exists()
    assert not (tmp_path / "account-aliases.json").exists()


def test_require_mail_root_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path / "not-mounted")
    with pytest.raises(ValueError, match="does not exist"):
        api.list_map("account-aliases")


# --- cli: the argparse tree + dispatch ---------------------------------------


def test_parser_resolves_full_tree() -> None:
    args = cli.parser().parse_args(
        ["account", "aliases", "create", "--primary", "travel", "--item", "x@y.com"]
    )
    assert args.config_resource == "account-aliases"
    assert args.config_verb == "create"
    assert args.primary == "travel"
    assert args.item == ["x@y.com"]


def test_parser_resolves_domain_routes_delete() -> None:
    args = cli.parser().parse_args(
        ["domain", "routes", "delete", "--primary", "shopping"]
    )
    assert args.config_resource == "domain-routes"
    assert args.config_verb == "delete"
    assert args.item is None


def test_dispatch_list_prints_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "you@gmail.com").mkdir()
    args = cli.parser().parse_args(["account", "aliases", "list"])
    assert cli.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_dispatch_reports_validation_error_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "you@gmail.com").mkdir()
    args = cli.parser().parse_args(
        [
            "account",
            "aliases",
            "create",
            "--primary",
            "bad primary",
            "--item",
            "x@y.com",
        ]
    )
    assert cli.dispatch(args) == 2
    assert "invalid primary" in capsys.readouterr().err
