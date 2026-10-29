"""The helper every CLI test shares: run a node's ``main(argv)`` and read what it wrote."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from wicket import env

Main = Callable[[list[str] | None], int]


@pytest.fixture(name="run_cli")
def _run_cli(
    capsys: pytest.CaptureFixture[str],
) -> Callable[[Main, list[str]], tuple[int, str, str]]:
    """(exit code, stdout, stderr) of one invocation, whether it returned or exited."""

    def run(main: Main, argv: list[str]) -> tuple[int, str, str]:
        try:
            code = main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


@pytest.fixture(autouse=True)
def _no_real_mail_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the mail root at an empty scratch dir, so no test can read or write the real one.

    A test that needs a mail root sets its own; this only removes the default.
    """
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path_factory.mktemp("mail-root"))
    monkeypatch.delenv(env.ACCOUNT_ALIASES_FILE_ENV_VAR, raising=False)


@pytest.fixture(autouse=True)
def _no_real_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep every test off the owner's run dir and credentials.

    The halt and log dirs follow ``$XDG_STATE_HOME`` and the credential base
    follows ``env.DEFAULT_STATE_DIR``; both point at scratch dirs.
    """
    monkeypatch.setenv(
        env.STATE_HOME_ENV_VAR, str(tmp_path_factory.mktemp("state-home"))
    )
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path_factory.mktemp("state"))
