"""The helper every CLI test shares: run a node's ``main(argv)`` and read what it wrote."""

from __future__ import annotations

from collections.abc import Callable

import pytest

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
