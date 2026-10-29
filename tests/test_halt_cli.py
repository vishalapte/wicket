"""The halt noun: each verb returns the type it declares, publishes its schema, and sets
and clears a HALT only through `wicket.auth`, which logs each one.

What every node shares (bare invocation, argument errors) is tested once, over all nodes,
in ``test_cli.py``. This file holds what only halt knows. No test opens an IMAP
connection: `imaplib.IMAP4_SSL` refuses in every test, and the state, credential and mail
dirs are temp dirs.
"""

from __future__ import annotations

import imaplib
import json
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, get_type_hints

import pytest

import wicket.__main__ as root
import wicket.schema
from wicket import auth, env
from wicket.errors import ApiError
from wicket.halt import __main__ as cli
from wicket.halt import api

ME = "you@gmail.com"
WORK = "work@corp.com"

Run = Callable[..., tuple[int, str, str]]

# One real call per verb, for the test that checks each result against its type.
SAMPLES: dict[str, dict[str, Any]] = {
    "list": {},
    "set": {"mail_account": ME},
    "clear": {"all_": True},
}


@pytest.fixture(autouse=True)
def _offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No IMAP; a temp state home; two accounts in ~/.config/wicket/accounts; no mail root."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the halt noun opened an IMAP connection")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    accounts = tmp_path / "wicket" / "accounts"
    for account in (ME, WORK):
        (tmp_path / "gmail" / account).mkdir(parents=True)
        (accounts / account).mkdir(parents=True)
        (accounts / account / "imap.json").symlink_to(
            tmp_path / "gmail" / account / "imap.json"
        )  # a link to a file not made yet is an account
    (accounts / ".DS_Store").write_text("")  # a file is no account
    (accounts / "empty@x.com").mkdir()  # a folder without imap.json is no account
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path / "absent-mail")
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)


def _base() -> Path:
    return Path(os.environ["XDG_STATE_HOME"]) / "wicket"


def _halt(account: str = ME) -> Path:
    return _base() / "halt" / account


def _log(account: str = ME) -> list[str]:
    path = _base() / "log" / f"{account}.log"
    return path.read_text().splitlines() if path.exists() else []


def _plant(text: str, account: str = ME, *, hours_old: float = 0.0) -> Path:
    path = _halt(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    then = time.time() - hours_old * 3600
    os.utime(path, (then, then))
    return path


@pytest.mark.parametrize("verb", sorted(api.VERBS))
def test_halt_verbs_return_the_type_they_declare(verb: str) -> None:
    fn = api.VERBS[verb]
    result = fn(**SAMPLES[verb])
    assert wicket.schema.conforms(result, get_type_hints(fn)["return"]) == []


def test_halt_output_publishes_one_schema_per_verb() -> None:
    phases = sorted(str(params["phase"]) for params, _ in cli.output())
    assert phases == sorted(api.VERBS)


def test_the_root_lists_the_halt_noun() -> None:
    """A bare `python -m wicket` prints every noun in `commands()` (test_cli runs it)."""
    assert "halt" in dict(root.commands())


# --- set ---------------------------------------------------------------------


def test_set_touches_an_empty_halt_and_logs_it(run_cli: Run) -> None:
    code, out, err = run_cli(cli.main, ["set", "--mail-account", "You+tag@Gmail.com"])
    assert (code, err) == (0, "")
    assert _halt().read_text() == ""  # a touched file: its timestamp is when
    assert out.startswith(f"halted {ME} at ")
    assert _log() and _log()[-1].endswith(
        "HALT set by hand (python -m wicket.halt set)"
    )


def test_set_keeps_a_halt_in_force_and_its_time(run_cli: Run) -> None:
    halt = _plant("", hours_old=4)
    then = halt.stat().st_mtime
    code, out, _ = run_cli(cli.main, ["set", "--mail-account", ME, "--json"])
    assert code == 0
    row = json.loads(out)["halts"][0]
    assert (row["account"], row["changed"]) == (ME, False)
    assert abs(datetime.fromisoformat(row["halted"]).timestamp() - then) < 1
    assert halt.stat().st_mtime == then  # the first HALT wins, never touched again
    assert _log() == []  # nothing was set, so nothing is logged
    code, out, _ = run_cli(cli.main, ["set", "--mail-account", ME])
    assert out.startswith(f"already halted {ME} since ") and "(kept)" in out


def test_a_set_halt_never_expires(run_cli: Run) -> None:
    run_cli(cli.main, ["set", "--mail-account", ME])
    old = time.time() - 500 * 3600
    os.utime(_halt(), (old, old))
    with pytest.raises(auth.Halted):
        auth.open_mailbox(env.DEFAULT_STATE_DIR / ME / "imap.json", interactive=False)
    assert _halt().exists()
    assert not any("cleared" in line for line in _log())


def test_set_all_halts_every_account_in_config_wicket(run_cli: Run) -> None:
    code, out, _ = run_cli(cli.main, ["set", "--all", "--json"])
    assert code == 0
    rows = json.loads(out)["halts"]
    assert [(r["account"], r["changed"]) for r in rows] == [(WORK, True), (ME, True)]
    assert sorted(p.name for p in (_base() / "halt").iterdir()) == [WORK, ME]
    for account in (ME, WORK):
        assert len(_log(account)) == 1  # one log line per account


def test_set_one_account_needs_no_mail_root(run_cli: Run) -> None:
    assert not env.MAIL_ROOT.exists()
    code, out, _ = run_cli(cli.main, ["set", "--mail-account", "New+tag@X.com"])
    assert code == 0 and out.startswith("halted new@x.com at ")
    assert _halt("new@x.com").exists()  # an account with no folder is fine
    assert not env.MAIL_ROOT.exists()


def test_set_refuses_a_name_that_is_no_address(run_cli: Run) -> None:
    code, out, err = run_cli(cli.main, ["set", "--mail-account", "nobody"])
    assert (code, out) == (2, "")
    assert "not an email address: 'nobody'" in err
    assert not (_base() / "halt").exists()


# --- clear -------------------------------------------------------------------


def test_clear_removes_a_halt_through_clear_halt_and_logs_it(run_cli: Run) -> None:
    _plant("", hours_old=500)
    code, out, err = run_cli(cli.main, ["clear", "--mail-account", ME])
    assert (code, err) == (0, "")
    assert out.startswith(f"cleared {ME} (halted ")
    assert not _halt().exists() and list((_base() / "halt").iterdir()) == []
    assert _log()[-1].endswith("HALT cleared")


def test_clear_of_an_absent_halt_writes_nothing(run_cli: Run) -> None:
    code, out, _ = run_cli(cli.main, ["clear", "--mail-account", ME, "--json"])
    assert code == 0
    assert json.loads(out) == {"cleared": [], "not_halted": [ME]}
    assert not (_base() / "halt").exists() and _log() == []
    code, out, _ = run_cli(cli.main, ["clear", "--mail-account", ME])
    assert out == f"not halted: {ME}\n"


def test_clear_all_clears_every_account_in_config_wicket(run_cli: Run) -> None:
    _plant("")
    _plant("", "gone@old.com")  # not in ~/.config/wicket/accounts
    code, out, _ = run_cli(cli.main, ["clear", "--all", "--json"])
    assert code == 0
    result = json.loads(out)
    assert [r["account"] for r in result["cleared"]] == [ME]
    assert result["not_halted"] == [WORK]
    assert [p.name for p in (_base() / "halt").iterdir()] == ["gone@old.com"]
    assert len(_log()) == 1 and _log("gone@old.com") == []
    code, out, _ = run_cli(cli.main, ["clear", "--all"])
    assert out == f"not halted: {WORK}\nnot halted: {ME}\n"


@pytest.mark.parametrize(
    ("minutes_old", "flags", "cleared"),
    [
        (961, ["--min", "960"], True),  # over N minutes: cleared
        (960, ["--min", "960"], False),  # exactly N minutes: left
        (959, ["--min", "960"], False),  # younger: left
        (0, [], True),  # no --min: cleared, however young
    ],
)
def test_clear_min_clears_only_a_halt_over_n_minutes_old(
    run_cli: Run, minutes_old: int, flags: list[str], cleared: bool
) -> None:
    _plant("", hours_old=minutes_old / 60)
    argv = ["clear", "--mail-account", ME, *flags, "--json"]
    code, out, err = run_cli(cli.main, argv)
    assert (code, err) == (0, "")
    result = json.loads(out)
    assert [r["account"] for r in result["cleared"]] == ([ME] if cleared else [])
    assert result["not_halted"] == ([] if cleared else [ME])
    assert _halt().exists() is not cleared
    assert _log() == [] if not cleared else _log()[-1].endswith("HALT cleared")


def test_clear_all_min_clears_each_halt_over_n_minutes_old(run_cli: Run) -> None:
    _plant("", hours_old=17)
    _plant("", WORK, hours_old=1)
    code, out, _ = run_cli(cli.main, ["clear", "--all", "--min", "960"])
    assert code == 0
    assert out.startswith(f"cleared {ME} (halted ")
    assert out.endswith(f"not halted: {WORK}\n")
    assert not _halt().exists() and _halt(WORK).exists()
    assert _log()[-1].endswith("HALT cleared") and _log(WORK) == []


@pytest.mark.parametrize(
    ("bad", "said"),
    [
        ("1.5", "argument --min: invalid int value: '1.5'"),
        ("x", "argument --min: invalid int value: 'x'"),
        ("", "argument --min: invalid int value: ''"),
        ("960m", "argument --min: invalid int value: '960m'"),
        ("-1", "--min takes 0 or more minutes, not -1"),
    ],
)
def test_clear_min_refuses_anything_but_a_whole_number(
    run_cli: Run, bad: str, said: str
) -> None:
    _plant("", hours_old=500)
    code, out, err = run_cli(cli.main, ["clear", "--mail-account", ME, "--min", bad])
    assert (code, out) == (2, "") and said in err
    assert _halt().exists() and _log() == []


# --- --all and --mail-account: exactly one ------------------------------------


@pytest.mark.parametrize("verb", ["set", "clear"])
def test_all_and_mail_account_are_exclusive_and_one_is_required(
    verb: str, run_cli: Run
) -> None:
    code, out, err = run_cli(cli.main, [verb, "--all", "--mail-account", ME])
    assert (code, out) == (2, "") and "not allowed with argument" in err
    code, out, err = run_cli(cli.main, [verb])
    assert (code, out) == (2, "")
    assert err.rstrip().splitlines()[-1].endswith("needs --mail-account or --all")
    with pytest.raises(ApiError, match="exactly one"):
        api.VERBS[verb](mail_account=ME, all_=True)
    with pytest.raises(ApiError, match="exactly one"):
        api.VERBS[verb]()
    assert not (_base() / "halt").exists() and _log() == []


# --- list --------------------------------------------------------------------


def test_list_shows_each_halt_as_text_and_as_one_json_record(run_cli: Run) -> None:
    _plant("", hours_old=3.2)
    _plant("", WORK, hours_old=50)
    code, out, err = run_cli(cli.main, ["list"])
    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[0].split() == ["account", "halted", "age"]
    assert lines[1].split()[0] == WORK and lines[1].split()[-1] == "2d02h"
    assert lines[2].split()[0] == ME and lines[2].split()[-1] == "3h12m"
    when = datetime.fromtimestamp(_halt().stat().st_mtime).astimezone()
    assert f"{when:%Y-%m-%d %H:%M %Z}" in lines[2]  # the file's timestamp, local
    code, out, _ = run_cli(cli.main, ["list", "--json"])
    record = json.loads(out)
    assert list(record) == ["halts"]
    assert [h["account"] for h in record["halts"]] == [WORK, ME]
    assert set(record["halts"][1]) == {"account", "halted", "age_seconds"}
    halted = datetime.fromisoformat(record["halts"][1]["halted"])
    assert abs(halted.timestamp() - _halt().stat().st_mtime) < 1
    assert isinstance(record["halts"][1]["age_seconds"], int)


def test_list_with_nothing_halted(run_cli: Run) -> None:
    code, out, _ = run_cli(cli.main, ["list"])
    assert (code, out) == (0, "no account is halted\n")
