"""The account noun: an account is a folder in ~/.config/wicket/accounts holding imap.json.

Each case is the owner's (2026-10-27, 2026-10-28): ``create`` makes ``accounts/<name>/`` and
links its ``imap.json`` to ``<--config>/imap.json``; the same link again is a silent exit
0; a missing ``--config`` folder and an ``imap.json`` naming another target are errors on
stderr, exit 2, stdout empty; ``delete`` removes an ``imap.json`` link, renames a real file
``imap_YYYYMMDD_HHMM.json``, keeps the folder, and with no ``imap.json`` is a silent exit
0; ``list`` gives each folder holding an ``imap.json`` and that entry's target, as a JSON
list of pairs. No test opens an IMAP connection or reads the mail root.
"""

from __future__ import annotations

import imaplib
import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, get_type_hints

import pytest

import wicket.__main__ as root
import wicket.schema
from wicket import env
from wicket.account import __main__ as cli
from wicket.account import api

Run = Callable[..., tuple[int, str, str]]


@pytest.fixture(name="config")
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No IMAP; ~/.config/wicket not yet made; one account's config folder."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the account noun opened an IMAP connection")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse)
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    folder = tmp_path / "gmail" / "you@gmail.com"
    folder.mkdir(parents=True)
    return folder


def _links(tmp_path: Path) -> dict[str, str]:
    """Each account folder's imap.json link and its target."""
    root_dir = tmp_path / "wicket" / "accounts"
    return {
        p.name: str((p / "imap.json").readlink())
        for p in root_dir.iterdir()
        if (p / "imap.json").is_symlink()
    }


SAMPLES: dict[str, Callable[[Path], dict[str, Any]]] = {
    "list": lambda _c: {},
    "create": lambda c: {"name": "you@gmail.com", "config": c},
    "delete": lambda _c: {"name": "you@gmail.com"},
}


@pytest.mark.parametrize("verb", sorted(api.VERBS))
def test_account_verbs_return_the_type_they_declare(verb: str, config: Path) -> None:
    fn = api.VERBS[verb]
    result = fn(**SAMPLES[verb](config))
    assert wicket.schema.conforms(result, get_type_hints(fn)["return"]) == []


def test_account_output_publishes_one_schema_per_verb() -> None:
    assert sorted(str(params["phase"]) for params, _ in cli.output()) == sorted(
        api.VERBS
    )


def test_the_root_lists_the_account_noun() -> None:
    assert "account" in dict(root.commands())


# --- create ------------------------------------------------------------------


def test_create_makes_the_folder_and_the_link_and_prints_nothing(
    config: Path, tmp_path: Path, run_cli: Run
) -> None:
    assert not (tmp_path / "wicket").exists()
    code, out, err = run_cli(
        cli.main, ["create", "you@gmail.com", "--config", str(config)]
    )
    assert (code, out, err) == (0, "", "")
    assert (tmp_path / "wicket" / "accounts" / "you@gmail.com").is_dir()  # a folder
    assert _links(tmp_path) == {"you@gmail.com": str(config / "imap.json")}
    assert not (config / "imap.json").exists()  # its target waits for the first fetch


def test_create_of_the_same_link_is_a_silent_no_op(
    config: Path, tmp_path: Path, run_cli: Run
) -> None:
    argv = ["create", "you@gmail.com", "--config", str(config)]
    run_cli(cli.main, argv)
    assert run_cli(cli.main, argv) == (0, "", "")
    code, out, _ = run_cli(cli.main, [*argv, "--json"])
    assert code == 0
    assert json.loads(out) == {
        "name": "you@gmail.com",
        "config": str(config / "imap.json"),
        "changed": False,
    }
    assert _links(tmp_path) == {"you@gmail.com": str(config / "imap.json")}


def test_create_refuses_a_link_to_another_target(
    config: Path, tmp_path: Path, run_cli: Run
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    run_cli(cli.main, ["create", "you@gmail.com", "--config", str(config)])
    argv = ["create", "you@gmail.com", "--config", str(other)]
    code, out, err = run_cli(cli.main, argv)
    assert (code, out) == (2, "")
    assert err == f"link exists with different target ({config / 'imap.json'})\n"
    code, out, err = run_cli(cli.main, [*argv, "--json"])
    assert (code, out) == (2, "")
    refusal = json.loads(err)
    assert refusal["code"] == "conflict"
    assert refusal["message"] == (
        f"link exists with different target ({config / 'imap.json'})"
    )
    assert _links(tmp_path) == {"you@gmail.com": str(config / "imap.json")}  # as it was


def test_create_refuses_a_config_path_that_does_not_exist(
    tmp_path: Path, run_cli: Run, config: Path
) -> None:
    del config
    argv = ["create", "new@x.com", "--config", str(tmp_path / "absent")]
    code, out, err = run_cli(cli.main, argv)
    assert (code, out, err) == (2, "", "--config path does not exist\n")
    code, out, err = run_cli(cli.main, [*argv, "--json"])
    assert (code, out) == (2, "")
    refusal = json.loads(err)
    assert (refusal["code"], refusal["message"]) == (
        "not_found",
        "--config path does not exist",
    )
    assert not (tmp_path / "wicket").exists()  # nothing written


# --- delete ------------------------------------------------------------------


def test_delete_removes_the_link_never_its_target_and_keeps_the_folder(
    config: Path, tmp_path: Path, run_cli: Run
) -> None:
    (config / "imap.json").write_text("{}")
    run_cli(cli.main, ["create", "you@gmail.com", "--config", str(config)])
    assert run_cli(cli.main, ["delete", "you@gmail.com"]) == (0, "", "")
    folder = tmp_path / "wicket" / "accounts" / "you@gmail.com"
    assert folder.is_dir() and list(folder.iterdir()) == []  # the folder stays
    assert (config / "imap.json").is_file()  # the file it pointed to is untouched
    assert run_cli(cli.main, ["list"]) == (0, "", "")  # an empty folder is no account


def test_delete_of_no_imap_json_is_a_silent_no_op(
    config: Path, tmp_path: Path, run_cli: Run
) -> None:
    del config
    assert run_cli(cli.main, ["delete", "nobody@x.com"]) == (0, "", "")
    (tmp_path / "wicket" / "accounts" / "empty@x.com").mkdir(parents=True)
    code, out, err = run_cli(cli.main, ["delete", "empty@x.com", "--json"])
    assert (code, json.loads(out), err) == (
        0,
        {"name": "empty@x.com", "changed": False},
        "",
    )
    assert (tmp_path / "wicket" / "accounts" / "empty@x.com").is_dir()


def _minutes() -> set[str]:
    """The rename's possible names: this minute's and the next (a delete may cross one)."""
    now = datetime.now()
    return {f"imap_{t:%Y%m%d_%H%M}.json" for t in (now, now + timedelta(minutes=1))}


def test_delete_renames_a_real_file_and_keeps_its_mode(
    config: Path, tmp_path: Path, run_cli: Run
) -> None:
    del config
    folder = tmp_path / "wicket" / "accounts" / "you@gmail.com"
    folder.mkdir(parents=True)
    (folder / "imap.json").write_text('{"secret": 1}')
    os.chmod(folder / "imap.json", 0o600)
    before = _minutes()
    code, out, err = run_cli(cli.main, ["delete", "you@gmail.com"])
    assert (code, out, err) == (0, "", "")
    names = [p.name for p in folder.iterdir()]
    assert len(names) == 1 and names[0] in before | _minutes()
    kept = folder / names[0]
    assert kept.read_text() == '{"secret": 1}'  # the credential survives
    assert kept.stat().st_mode & 0o777 == 0o600
    assert run_cli(cli.main, ["list"]) == (0, "", "")  # and the folder is no account


def test_delete_refuses_when_the_new_name_is_taken_and_moves_nothing(
    config: Path, tmp_path: Path, run_cli: Run
) -> None:
    del config
    folder = tmp_path / "wicket" / "accounts" / "you@gmail.com"
    folder.mkdir(parents=True)
    (folder / "imap.json").write_text("new")
    for name in _minutes():
        (folder / name).write_text("old")
    code, out, err = run_cli(cli.main, ["delete", "you@gmail.com"])
    assert (code, out) == (2, "")
    assert err.endswith(" exists; nothing moved\n")
    code, out, err = run_cli(cli.main, ["delete", "you@gmail.com", "--json"])
    assert (code, out) == (2, "")
    assert json.loads(err)["code"] == "conflict"
    assert (folder / "imap.json").read_text() == "new"  # nothing moved
    assert all((folder / name).read_text() == "old" for name in _minutes())


# --- list --------------------------------------------------------------------


def test_list_gives_each_name_and_its_imap_json_target(
    config: Path, tmp_path: Path, run_cli: Run
) -> None:
    run_cli(cli.main, ["create", "you@gmail.com", "--config", str(config)])
    accounts = tmp_path / "wicket" / "accounts"
    (accounts / "work@corp.com").mkdir()
    (accounts / "work@corp.com" / "imap.json").symlink_to(tmp_path / "by-hand.json")
    (accounts / "file@x.com").mkdir()
    (accounts / "file@x.com" / "imap.json").write_text("{}")  # a real file: its path
    (accounts / "empty@x.com").mkdir()  # no imap.json: no account
    (accounts / ".DS_Store").write_text("")  # a file is no account
    code, out, err = run_cli(cli.main, ["list"])
    assert (code, err) == (0, "")
    expected = [
        ["file@x.com", str(accounts / "file@x.com" / "imap.json")],
        ["work@corp.com", str(tmp_path / "by-hand.json")],
        ["you@gmail.com", str(config / "imap.json")],
    ]
    assert [line.split() for line in out.splitlines()] == expected
    code, out, _ = run_cli(cli.main, ["list", "--json"])
    assert code == 0
    assert json.loads(out) == {"accounts": expected}


def test_list_with_no_folder(config: Path, run_cli: Run) -> None:
    del config
    assert run_cli(cli.main, ["list"]) == (0, "", "")
