"""Tests for ./install's write of defaults.json into settings.json "env".

Each run points CLAUDE_CONFIG_DIR and HOME at a tmp dir, so ~/.claude is never
touched and `~` expands to a known place. A test that needs another defaults.json
copies the script and pyproject.toml beside one it writes, because the script reads
the files next to itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install"


def _set_names() -> set[str]:
    """The names defaults.json gives a value, the ones ./install writes."""
    defaults = json.loads((REPO / "defaults.json").read_text(encoding="utf-8"))
    return {name for name, value in defaults.items() if value is not None}


def _run(
    tmp: Path, *args: str, script: Path = INSTALL
) -> subprocess.CompletedProcess[str]:
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(home), CLAUDE_CONFIG_DIR=str(_settings(tmp).parent))
    return subprocess.run(
        [sys.executable, str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _settings(tmp: Path) -> Path:
    return tmp / "claude" / "settings.json"


def _read(tmp: Path) -> dict[str, Any]:
    settings: dict[str, Any] = json.loads(_settings(tmp).read_text(encoding="utf-8"))
    return settings


def _script_with(tmp: Path, defaults: str) -> Path:
    """A copy of ./install and pyproject.toml beside a defaults.json holding `defaults`."""
    repo = tmp / "repo"
    repo.mkdir()
    shutil.copy(INSTALL, repo / "install")
    shutil.copy(REPO / "pyproject.toml", repo / "pyproject.toml")
    (repo / "defaults.json").write_text(defaults, encoding="utf-8")
    return repo / "install"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    result = _run(tmp_path, "--dry-run")
    assert result.returncode == 0
    assert f"would write {_settings(tmp_path)}" in result.stdout
    assert not _settings(tmp_path).exists()


def test_writes_absolute_paths_with_the_mail_root_resolved(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 0
    env = _read(tmp_path)["env"]
    home = tmp_path / "home"
    root = str(home / ".data" / "mail")
    names = _set_names()
    assert set(env) == names
    assert env["WICKET_DATA_DIR"] == root
    assert all(name.startswith("WICKET_") for name in names)  # sets only its own
    assert env["WICKET_ACCOUNT_ALIASES"] == root + "/account-aliases.json"
    assert all(Path(env[name]).is_absolute() for name in names)
    assert not any("~" in env[name] or "<" in env[name] for name in names)


def test_null_is_skipped_and_an_existing_key_kept(tmp_path: Path) -> None:
    _settings(tmp_path).parent.mkdir()
    _settings(tmp_path).write_text(
        json.dumps({"env": {"WICKET_ACCOUNT": "you@gmail.com"}}), encoding="utf-8"
    )
    assert _run(tmp_path).returncode == 0
    assert _read(tmp_path)["env"]["WICKET_ACCOUNT"] == "you@gmail.com"

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert _run(fresh).returncode == 0
    assert "WICKET_ACCOUNT" not in _read(fresh)["env"]


def test_other_env_keys_and_hooks_are_kept(tmp_path: Path) -> None:
    hooks = {"SessionStart": [{"matcher": "startup", "hooks": [{"command": "x"}]}]}
    _settings(tmp_path).parent.mkdir()
    _settings(tmp_path).write_text(
        json.dumps(
            {
                "env": {"KEEP_ME": "1", "WICKET_DATA_DIR": "/old"},
                "hooks": hooks,
                "model": "m",
            }
        ),
        encoding="utf-8",
    )
    assert _run(tmp_path).returncode == 0
    settings = _read(tmp_path)
    assert settings["env"]["KEEP_ME"] == "1"
    assert settings["env"]["WICKET_DATA_DIR"] != "/old"
    assert settings["hooks"] == hooks
    assert settings["model"] == "m"


def test_a_second_run_changes_nothing(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 0
    before = _settings(tmp_path).read_text(encoding="utf-8")
    again = _run(tmp_path)
    assert again.returncode == 0
    assert "settings.json already up to date" in again.stdout
    assert _settings(tmp_path).read_text(encoding="utf-8") == before


def test_a_placeholder_names_another_key(tmp_path: Path) -> None:
    script = _script_with(
        tmp_path,
        json.dumps({"WICKET_DATA_DIR": "~/m", "WICKET_X": "<WICKET_DATA_DIR>/x"}),
    )
    assert _run(tmp_path, script=script).returncode == 0
    assert _read(tmp_path)["env"]["WICKET_X"] == str(tmp_path / "home" / "m" / "x")


def test_an_unknown_placeholder_is_refused_and_nothing_written(
    tmp_path: Path,
) -> None:
    script = _script_with(
        tmp_path, json.dumps({"WICKET_DATA_DIR": "~/m", "WICKET_X": "<HOME>/x"})
    )
    result = _run(tmp_path, script=script)
    assert result.returncode == 2
    assert "<HOME> names no plain string key" in result.stderr
    assert not _settings(tmp_path).exists()


def test_a_key_without_the_prefix_is_refused_and_nothing_written(
    tmp_path: Path,
) -> None:
    script = _script_with(tmp_path, json.dumps({"WICKET_DATA_DIR": "~/m", "X": "1"}))
    result = _run(tmp_path, script=script)
    assert result.returncode == 2
    assert "not this repo's (WICKET_*): X" in result.stderr
    assert not _settings(tmp_path).exists()


def test_a_bad_defaults_file_is_refused(tmp_path: Path) -> None:
    for name, text in (("list", "[]"), ("number", '{"X": 1}'), ("json", "{")):
        case = tmp_path / name
        case.mkdir()
        result = _run(case, script=_script_with(case, text))
        assert result.returncode == 2, name
        assert not _settings(case).exists()

    missing = _script_with(tmp_path, "{}")
    missing.with_name("defaults.json").unlink()
    result = _run(tmp_path, script=missing)
    assert result.returncode == 2
    assert "cannot read" in result.stderr


def test_an_unknown_argument_exits_2(tmp_path: Path) -> None:
    result = _run(tmp_path, "--force")
    assert result.returncode == 2
    assert not _settings(tmp_path).exists()
