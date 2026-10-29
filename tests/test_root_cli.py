"""The wicket noun: each verb returns the type it declares, and publishes its schema.

What every node shares (bare invocation, argument errors) is tested once, over all nodes,
in ``test_cli.py``. This file holds what only wicket knows. No test opens an IMAP
connection: `imaplib.IMAP4_SSL` refuses in every test, and the mail root, the state dir
and ``~/.config/wicket/accounts`` are temp dirs.
"""

from __future__ import annotations

import imaplib
from pathlib import Path
from typing import Any, get_type_hints

import pytest

import wicket.schema
from wicket import __main__ as cli
from wicket import env
from wicket.root import api

ME = "you@gmail.com"

# One real call per verb, for the test that checks each result against its type.
SAMPLES: dict[str, dict[str, Any]] = {
    "catalog": {"mail_account": ME, "years": "2026"},
    "fetch": {"mail_account": ME},
    "ingest": {"mail_account": ME, "staged": True},
    "report": {},
}


def _download_years(**_kwargs: object) -> dict[str, object]:
    """What a run over every year returns: its counts, one record per pass, no HALT."""
    counts = {
        "matched": 2,
        "got": 1,
        "candidates": 1,
        "pending": 1,
        "linked": 0,
        "downloaded": 1,
        "failed": 0,
    }
    return {
        **counts,
        "passes": [{"year": 2026, "pass": 1, **counts, "got_total": 2}],
        "halted": False,
        "halt": None,
        "halt_file": None,
    }


def _sweep(**_kwargs: object) -> dict[str, int]:
    """What a sweep of one year returns: its counts."""
    return {
        "messages": 3,
        "shards": 1,
        "removed": 0,
        "parse_skips": 0,
        "label_misses": 0,
        "boundary_drops": 0,
    }


@pytest.fixture(autouse=True)
def _offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No IMAP; a temp mail root with one store; its folder in ~/.config/wicket/accounts.

    The credentials and the workers under the api are replaced, so each sample is a
    real call through the api that returns a whole result.
    """

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a root verb opened an IMAP connection")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    (tmp_path / "mail" / ME).mkdir(parents=True)
    (tmp_path / "gmail" / ME).mkdir(parents=True)
    (tmp_path / "wicket" / "accounts" / ME).mkdir(parents=True)
    (tmp_path / "wicket" / "accounts" / ME / "imap.json").symlink_to(
        tmp_path / "gmail" / ME / "imap.json"
    )
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path / "mail")
    monkeypatch.setattr(api, "MAIL_ROOT", tmp_path / "mail")  # bound by name at import
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    monkeypatch.setattr(api, "load_credentials", lambda *_a, **_k: (ME, "pw"))
    monkeypatch.setattr(api, "sweep", _sweep)
    monkeypatch.setattr(api, "download_years", _download_years)


@pytest.mark.parametrize("verb", sorted(api.VERBS))
def test_root_verbs_return_the_type_they_declare(verb: str) -> None:
    fn = api.VERBS[verb]
    result = fn(**SAMPLES[verb])
    assert wicket.schema.conforms(result, get_type_hints(fn)["return"]) == []


def test_root_output_publishes_one_schema_per_verb() -> None:
    phases = sorted(str(params["phase"]) for params, _ in cli.output())
    assert phases == sorted(api.VERBS)
