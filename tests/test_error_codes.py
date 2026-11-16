"""``error-codes.jsonl``: wicket's closed list of refusals, held in step with the code.

The file is authored, never generated from the code, so these tests compare two
independent statements: every code the source names is a line of the file, every line
is named somewhere in the source, every placeholder in a fix is a fact each raise of
that code carries, and each code reaches the log with the file's exit and the terminal
with the file's fix.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest

import wicket
import wicket.halt.__main__ as halt_cli
from wicket import auth, env, errors
from wicket.provider import NoProvider
from wicket.root import api as root_api
from wicket.root.lib.ingest import AccountMismatch

SRC = Path(wicket.__file__).parent
CODES_FILE = SRC / errors.CODES_FILENAME
ME = "you@gmail.com"


def _lines() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in CODES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _facts_for(reason: str, **facts: object) -> dict[str, object]:
    """``facts``, plus a stand-in for each placeholder ``reason``'s fix names."""
    return {
        **{
            name: "x"
            for name in errors.PLACEHOLDER_RE.findall(errors.CODES[reason].fix)
        },
        **facts,
    }


def _named_in_source() -> set[str]:
    """Every code the source raises: a class's ``reason``, an ``ApiError`` or
    ``LinkRefused`` code (second argument or ``reason=``), and the default
    (`errors.BAD_REQUEST`). A ``reason=`` field of a log event (``event(...)``) names a
    code without raising it, so it is not counted."""
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                names = {t.id for t in node.targets if isinstance(t, ast.Name)}
                if names & {"reason", "BAD_REQUEST"}:
                    found.add(str(node.value.value))
            if not isinstance(node, ast.Call):
                continue
            callee = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if callee == "event":
                continue
            if callee in {"ApiError", "LinkRefused"} and len(node.args) > 1:
                code = node.args[1]
                if isinstance(code, ast.Constant):
                    found.add(str(code.value))
            for keyword in node.keywords:
                if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant):
                    found.add(str(keyword.value.value))
    return found


def _linked_credentials(tmp_path: Path) -> Path:
    """A Gmail account's ``imap.json`` link, as `provider.provider_of` reads it."""
    target = tmp_path / "gmail" / ME / "imap.json"
    target.parent.mkdir(parents=True)
    target.write_text(f'{{"email": "{ME}", "app_password": "pw"}}', encoding="utf-8")
    link = tmp_path / "wicket" / "accounts" / ME / "imap.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    return link


def test_every_line_has_a_reason_an_exit_a_when_and_a_fix() -> None:
    lines = _lines()
    assert len(lines) == len(errors.CODES) == len({line["reason"] for line in lines})
    for line in lines:
        assert set(line) == {"reason", "exit", "when", "fix"}, line
        assert line["exit"] in (1, 2), line
        assert str(line["when"]).strip() and str(line["fix"]).strip(), line


def test_every_code_the_source_names_is_a_line_of_the_file() -> None:
    assert _named_in_source() - set(errors.CODES) == set()


def test_every_line_of_the_file_is_named_somewhere_in_the_source() -> None:
    assert set(errors.CODES) - _named_in_source() == set()


def test_an_unlisted_reason_raises_when_the_refusal_is_built() -> None:
    with pytest.raises(LookupError, match="not-a-code"):
        _ = errors.ApiError("refused", "not-a-code")


def test_a_refusal_lacking_a_fact_its_fix_names_raises_when_built() -> None:
    """No ``<placeholder>`` ever reaches a terminal: the raise site fails instead."""
    with pytest.raises(LookupError, match="leader"):
        _ = errors.ApiError("mismatch", "account_mismatch")


def test_a_caller_cannot_state_an_exit_code() -> None:
    with pytest.raises(TypeError, match="error-codes.jsonl"):
        _ = errors.ApiError("refused", "imap", exit_code=1)


def test_the_exit_code_comes_from_the_file() -> None:
    for reason, code in errors.CODES.items():
        refusal = errors.ApiError("refused", reason, **_facts_for(reason))
        assert refusal.exit_code == code.exit


def test_the_text_is_the_message_then_the_fix_filled_from_the_facts() -> None:
    refusal = errors.ApiError("thread mismatch", "account_mismatch", leader="w@x.com")
    assert str(refusal) == (
        "thread mismatch\n"
        "fix: Run again with --mail-account w@x.com, or pass --force to file it "
        "under the named account anyway."
    )
    assert refusal.message == "thread mismatch"


def test_a_class_without_a_code_is_a_bad_request() -> None:
    assert errors.refused(ValueError("bad")).reason == errors.BAD_REQUEST
    # A stdlib `reason` attribute is no refusal code: only a class's own is read.
    decode = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    assert errors.refused(decode).reason == errors.BAD_REQUEST


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (env.NoMailRoot("x"), "no_mail_root"),
        (env.NoAccount("x"), "no_account"),
        (env.UnknownAccount("x"), "unknown_account"),
        (env.BadMap("x"), "bad_map"),
        (auth.NoCredentials("x"), "no_credentials"),
        (auth.Unsupported("x"), "unsupported"),
        (auth.Unreachable("x"), "unreachable"),
        (AccountMismatch("x", "w@x.com"), "account_mismatch"),
        (NoProvider("x"), "no_provider"),
    ],
)
def test_each_class_names_its_code_and_stays_what_callers_catch(
    exc: Exception, reason: str
) -> None:
    assert errors.refused(exc).reason == reason
    assert isinstance(exc, (ValueError, auth.AuthError))  # every `except` still holds


def _halted_fetch_stats() -> dict[str, object]:
    """What a fetch run that halted partway returns (`root.lib.fetch._result`)."""
    return {
        **dict.fromkeys(
            ("matched", "got", "candidates", "pending", "linked", "downloaded"), 0
        ),
        "failed": 0,
        "passes": [],
        "halted": True,
        "halt": "throttle",
        "halt_file": f"/state/wicket/halt/{ME}",
        "halt_facts": {"cause": "throttle"},
    }


def _halted_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[errors.ApiError]:
    """Every way the api says a HALT: one this run wrote, one already in force
    (`root.api._refused`), and a fetch run that halted partway (`root.api.fetch_root`).
    """
    creds = _linked_credentials(tmp_path)
    written = auth.write_halt(creds, "month 2026-03: TimeoutError")
    in_force = auth.halt_in_force(creds)
    assert in_force is not None
    monkeypatch.setattr(root_api, "fetch", lambda *_a, **_k: _halted_fetch_stats())
    with pytest.raises(errors.ApiError) as partway:
        root_api.fetch_root(query="x")
    return [root_api._refused(written), root_api._refused(in_force), partway.value]


def test_every_placeholder_in_a_fix_is_a_fact_each_raise_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    raises = {
        "account_mismatch": [
            errors.refused(AccountMismatch("mismatch", "w@x.com")),
        ],
        "halted": _halted_refusals(tmp_path, monkeypatch),
    }
    with_placeholders = {
        reason
        for reason, code in errors.CODES.items()
        if errors.PLACEHOLDER_RE.search(code.fix)
    }
    assert with_placeholders == set(raises)  # a new placeholder needs its raises here
    for reason, refusals in raises.items():
        wanted = set(errors.PLACEHOLDER_RE.findall(errors.CODES[reason].fix))
        for refusal in refusals:
            assert refusal.reason == reason
            assert wanted <= set(refusal.facts), (reason, refusal.facts)
            assert "<" not in refusal.fix(), refusal.fix()
    assert all(refusal.facts["account"] == ME for refusal in raises["halted"])


def test_a_halted_fetch_names_the_halt_files_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(root_api, "fetch", lambda *_a, **_k: _halted_fetch_stats())
    with pytest.raises(errors.ApiError) as refused:
        root_api.fetch_root(query="x")
    assert refused.value.reason == "halted"
    assert refused.value.facts["account"] == ME
    assert refused.value.fix().endswith(f"--mail-account {ME}.")


@pytest.mark.parametrize("reason", sorted(errors.CODES))
def test_each_reason_reaches_wicket_end_with_the_files_exit_code(
    reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    @errors.refusals
    def request() -> None:
        raise errors.ApiError(
            "refused", reason, **_facts_for(reason, account=ME, lock_pid="42")
        )

    caplog.set_level(logging.INFO, logger=env.LOGGER_NAME)
    with pytest.raises(errors.ApiError):
        request()
    (record,) = [r for r in caplog.records if getattr(r, "event", None)]
    line = env.LogfmtFormatter().format(record)
    exit_code = errors.CODES[reason].exit
    assert line.endswith(
        f": account={ME} level=error event=wicket.end outcome=refused "
        f"exit={exit_code} reason={reason} lock_pid=42"
    )
    assert record.getMessage() == ""  # event only: the face prints the text


def test_a_refusal_at_a_terminal_prints_its_fix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = halt_cli.main(["set", "--mail-account", "nobody"])
    assert (code, capsys.readouterr().err) == (
        2,
        "not an email address: 'nobody'\n"
        "fix: Check the command against its --help.\n",
    )


def test_each_request_starts_with_no_process_account() -> None:
    """One request's account never reaches the next: the wrapper clears it on entry. It
    is kept after a request ends, for the renderer's events that follow."""

    @errors.refusals
    def names(account: str) -> str | None:
        env.set_process_account(account)
        return env.process_account()

    @errors.refusals
    def names_none() -> str | None:
        return env.process_account()

    @errors.refusals
    def refused() -> None:
        env.set_process_account(ME)
        raise errors.ApiError("refused")

    assert names(ME) == ME
    assert env.process_account() == ME  # kept after the request returns
    assert names_none() is None  # a request that names no account carries none
    with pytest.raises(errors.ApiError):
        refused()
    assert names_none() is None
