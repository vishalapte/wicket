"""Tests for ./install: defaults.json into settings.json "env", then the CLI launcher.

Each run points CLAUDE_CONFIG_DIR and HOME at a tmp dir, so ~/.claude and
~/.local/bin are never touched and `~` expands to a known place. Its stdin is a real
terminal (a pty) holding the answer to the settings prompt, "y" unless a test says
otherwise, or /dev/null for a run with no terminal. A test that needs
another defaults.json copies the script and pyproject.toml beside one it writes, and
links .racecar/ and src/ there, because the script reads the files next to itself.
"""

from __future__ import annotations

import json
import os
import pty
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
    tmp: Path, *args: str, script: Path = INSTALL, answer: str | None = "y"
) -> subprocess.CompletedProcess[str]:
    """Run ./install with `answer` typed at a terminal, or with no terminal when None."""
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(home), CLAUDE_CONFIG_DIR=str(_settings(tmp).parent))
    command = [sys.executable, str(script), *args]
    if answer is None:
        return subprocess.run(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    terminal, child = pty.openpty()
    try:
        os.write(terminal, f"{answer}\n".encode())
        return subprocess.run(
            command, env=env, stdin=child, capture_output=True, text=True, check=False
        )
    finally:
        os.close(terminal)
        os.close(child)


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
    for name in (".racecar", "src"):
        (repo / name).symlink_to(REPO / name)
    (repo / "defaults.json").write_text(defaults, encoding="utf-8")
    return repo / "install"


def _launcher(tmp: Path) -> Path:
    return tmp / "home" / ".local" / "bin" / "wicket"


def test_dry_run_writes_no_launcher(tmp_path: Path) -> None:
    result = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert f"would write  {_launcher(tmp_path)}" in result.stdout
    assert not _launcher(tmp_path).exists()


def test_a_run_writes_an_executable_launcher_of_this_checkout(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    launcher = _launcher(tmp_path)
    assert os.access(launcher, os.X_OK)
    text = launcher.read_text(encoding="utf-8")
    assert "racecar-cli-launcher:" in text.splitlines()[1]
    assert f"REPO='{REPO}'" in text
    assert "PKG='wicket'" in text


def _settings_hold(tmp: Path, settings: dict[str, Any]) -> None:
    _settings(tmp).parent.mkdir(parents=True, exist_ok=True)
    _settings(tmp).write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def test_dry_run_prints_the_lines_asks_nothing_and_writes_nothing(
    tmp_path: Path,
) -> None:
    _settings_hold(tmp_path, {"env": {"WICKET_DATA_DIR": "/old"}})
    before = _settings(tmp_path).read_text(encoding="utf-8")
    result = _run(tmp_path, "--dry-run")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    root = tmp_path / "home" / ".data" / "mail"
    assert '-env.WICKET_DATA_DIR = "/old"' in lines
    assert f'+env.WICKET_DATA_DIR = "{root}"' in lines
    assert f'+env.WICKET_ACCOUNT_ALIASES = "{root}/account-aliases.json"' in lines
    assert "Replace" not in result.stdout
    assert _settings(tmp_path).read_text(encoding="utf-8") == before


def test_an_add_only_run_writes_without_asking_even_with_no_terminal(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, answer=None)
    assert result.returncode == 0, result.stderr
    assert "+env.WICKET_DATA_DIR = " in result.stdout
    assert not any(line.startswith("-") for line in result.stdout.splitlines())
    assert "Replace" not in result.stdout
    assert set(_read(tmp_path)["env"]) == _set_names()
    assert _launcher(tmp_path).exists()


def test_a_change_asks_and_n_keeps_the_old_value_but_writes_the_additions(
    tmp_path: Path,
) -> None:
    _settings_hold(tmp_path, {"env": {"WICKET_DATA_DIR": "/old"}})
    result = _run(tmp_path, answer="n")
    assert result.returncode == 0, result.stderr
    assert f"Replace {_settings(tmp_path)}? [y/N]" in result.stdout
    env = _read(tmp_path)["env"]
    assert env["WICKET_DATA_DIR"] == "/old"
    assert set(env) == _set_names()  # the additions, written either way
    assert _launcher(tmp_path).exists()

    _run(tmp_path, answer="y")
    assert _read(tmp_path)["env"]["WICKET_DATA_DIR"] != "/old"


def test_a_change_with_no_terminal_is_not_written(tmp_path: Path) -> None:
    _settings_hold(tmp_path, {"env": {"WICKET_DATA_DIR": "/old"}})
    result = _run(tmp_path, answer=None)
    assert result.returncode == 0, result.stderr
    assert "run ./install in a terminal to confirm" in result.stdout
    assert "Replace" not in result.stdout
    assert _read(tmp_path)["env"]["WICKET_DATA_DIR"] == "/old"


def test_a_removal_asks(tmp_path: Path) -> None:
    script = _script_with(tmp_path, json.dumps({"WICKET_DATA_DIR": "~/m"}))
    retired = {"retired": [["SessionEnd", "old-hook.sh"]]}
    script.with_name("hooks.json").write_text(json.dumps(retired), encoding="utf-8")
    hook = {"type": "command", "command": "/x/old-hook.sh"}
    _settings_hold(
        tmp_path,
        {
            "env": {"WICKET_DATA_DIR": str(tmp_path / "home" / "m")},
            "hooks": {"SessionEnd": [{"matcher": "", "hooks": [hook]}]},
        },
    )
    declined = _run(tmp_path, script=script, answer="n")
    assert declined.returncode == 0, declined.stderr
    lines = declined.stdout.splitlines()
    assert '-hooks.SessionEnd[0].hooks[0].command = "/x/old-hook.sh"' in lines
    assert f"Replace {_settings(tmp_path)}? [y/N]" in declined.stdout
    assert _read(tmp_path)["hooks"]["SessionEnd"][0]["hooks"] == [hook]

    accepted = _run(tmp_path, script=script, answer="y")
    assert accepted.returncode == 0, accepted.stderr
    assert "hooks" not in _read(tmp_path)


def test_nothing_to_change_asks_nothing(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 0
    again = _run(tmp_path, answer="n")
    assert again.returncode == 0
    assert "settings.json already up to date" in again.stdout
    assert "Replace" not in again.stdout
    assert not any(line.startswith(("+", "-")) for line in again.stdout.splitlines())


def test_a_value_that_is_not_changing_is_never_printed(tmp_path: Path) -> None:
    _settings_hold(tmp_path, {"env": {"KEEP_ME": "s3cret"}, "model": "m"})
    result = _run(tmp_path, "--dry-run")
    assert result.returncode == 0
    assert "+env.WICKET_DATA_DIR = " in result.stdout
    assert "s3cret" not in result.stdout
    assert "KEEP_ME" not in result.stdout


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
