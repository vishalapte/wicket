"""The helper every CLI test shares: run a node's ``main(argv)`` and read what it wrote."""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable, Iterator

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

    Every ``WICKET_*`` variable is unset too: an installed shell sets each map's path to
    the owner's live file, and a path variable outranks the mail root. A test that needs
    a mail root or a variable sets its own; this only removes the defaults.
    """
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path_factory.mktemp("mail-root"))
    for name in [name for name in os.environ if name.startswith("WICKET_")]:
        monkeypatch.delenv(name)


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


@pytest.fixture(autouse=True)
def _no_logging_left() -> Iterator[None]:
    """Put wicket's logging back as the test found it.

    A face's ``main`` sets it up (`env.configure_logging`): two handlers on the ``wicket``
    logger, its level, and both excepthooks. After each test those handlers are removed,
    the level and the hooks restored, and the process account cleared, so no test sees
    another's. `_no_real_state` already points ``wicket.log`` at a temp dir.
    """
    logger = logging.getLogger(env.LOGGER_NAME)
    level = logger.level
    hooks = (sys.excepthook, threading.excepthook)
    yield
    for handler in [
        h
        for h in logger.handlers
        if isinstance(h, (env.StderrHandler, env.WicketLogHandler))
    ]:
        logger.removeHandler(handler)
    logger.setLevel(level)
    sys.excepthook, threading.excepthook = hooks
    env.set_process_account(None)
