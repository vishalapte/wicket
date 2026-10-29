"""An account with no folder in ~/.config/wicket/accounts is a no-op for fetch and catalog.

Owner, 2026-10-27: "a missing link is a no-op" and "exit code is what matters. print
nothing". A folder without ``imap.json`` is no no-op (owner, 2026-10-28): it prompts, or
refuses with exit 2 when it cannot. No test reaches the network: `imaplib.IMAP4_SSL`
refuses in every test.
"""

from __future__ import annotations

import imaplib
import sys
from pathlib import Path

import pytest

import wicket.__main__ as root
from wicket import auth, env
from wicket.catalog import cli as catalog_cli
from wicket.fetch import cli as fetch_cli

ME = "you@gmail.com"
DISPATCH = {"fetch": fetch_cli.dispatch, "catalog": catalog_cli.dispatch}


@pytest.fixture(autouse=True)
def _no_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A store for the account, and a ~/.config/wicket that has no folder for it."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a no-op opened an IMAP connection")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse)
    (tmp_path / "mail" / ME).mkdir(parents=True)
    (tmp_path / "wicket").mkdir()
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path / "mail")
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))


@pytest.mark.parametrize(
    ("dispatch", "argv"),
    [
        (fetch_cli.dispatch, ["fetch", "--mail-account", ME, "--non-interactive"]),
        (catalog_cli.dispatch, ["catalog", "--mail-account", ME, "--non-interactive"]),
    ],
)
def test_a_missing_link_exits_0_and_prints_nothing(
    dispatch: object,
    argv: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert callable(dispatch)
    code = dispatch(root.parser().parse_args(argv))
    out, err = capsys.readouterr()
    assert (code, out, err) == (0, "", "")
    assert list((tmp_path / "wicket").iterdir()) == []  # no folder made in its place
    assert not (tmp_path / "xdg").exists()  # no HALT, no log line


@pytest.mark.parametrize("verb", ["fetch", "catalog"])
def test_a_folder_without_imap_json_refuses_non_interactively(
    verb: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "wicket" / "accounts" / ME
    folder.mkdir(parents=True)
    argv = [verb, "--mail-account", ME, "--non-interactive"]
    code = DISPATCH[verb](root.parser().parse_args(argv))
    out, err = capsys.readouterr()
    assert (code, out) == (2, "")
    assert err.startswith(f"auth error: No credentials at {folder / 'imap.json'} ")


@pytest.mark.parametrize("verb", ["fetch", "catalog"])
def test_a_folder_without_imap_json_prompts_interactively(
    verb: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = tmp_path / "wicket" / "accounts" / ME
    folder.mkdir(parents=True)
    asked: list[Path] = []

    def prompt(path: Path) -> tuple[str, str]:
        asked.append(path)
        raise auth.AuthError("aborted: email or password looked invalid.")

    monkeypatch.setattr(auth, "_prompt_for_secret", prompt)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    code = DISPATCH[verb](root.parser().parse_args([verb, "--mail-account", ME]))
    assert code == 2 and asked == [folder / "imap.json"]
    assert "aborted" in capsys.readouterr().err
