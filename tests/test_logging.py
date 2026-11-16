"""The log line and the two logs (`env.LogfmtFormatter`, `env.configure_logging`).

One formatter writes every line: ``TIME HOST wicket[PID]:`` then logfmt, ``account=``,
``level=`` and ``event=`` first. ``wicket.log`` holds every event of every run, however
it was started; ``<account>.log`` holds the account's durable subset, written by
`env.append_log`, and the same record reaches both. The terminal keeps its text: a record
with no human message prints nothing on stderr. No test here touches a mailbox; the
state home is a temp dir (conftest).
"""

from __future__ import annotations

import logging
import os
import re
import socket
import sys
import threading
from pathlib import Path
from queue import Queue
from types import TracebackType

import pytest

import wicket.halt.__main__ as halt_cli
from wicket import auth, env
from wicket.errors import ApiError
from wicket.provider import GMAIL
from wicket.root import api as root_api
from wicket.root.lib import catalog as catalog_lib
from wicket.root.lib import fetch as fetch_lib
from wicket.root.lib.fetch import ThreadContext, download

ME = "you@gmail.com"
WORK = "work@corp.com"

HEADER = re.compile(
    r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d (?P<host>\S+) wicket\[(?P<pid>\d+)\]: "
)


def _record(
    extra: dict[str, object] | None = None, message: str = "", level: int = logging.INFO
) -> logging.LogRecord:
    return logging.getLogger(env.LOGGER_NAME).makeRecord(
        env.LOGGER_NAME, level, __file__, 1, message, (), None, extra=extra
    )


def _body(record: logging.LogRecord) -> str:
    line = env.LogfmtFormatter().format(record)
    match = HEADER.match(line)
    assert match is not None, line
    return line[match.end() :]


def _state() -> Path:
    return Path(os.environ[env.STATE_HOME_ENV_VAR]) / "wicket"


def _wicket_log() -> list[str]:
    path = _state() / "log" / "wicket.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _account_log(account: str = ME) -> list[str]:
    path = _state() / "log" / f"{account}.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _bodies() -> list[str]:
    """wicket.log's lines without their header: the logfmt part."""
    return [line.split(": ", 1)[1] for line in _wicket_log()]


def _account_of(line: str) -> str:
    match = re.search(r" account=(\S+) ", line)
    assert match is not None, line
    return match[1]


# --- the line -------------------------------------------------------------------


def test_the_header_is_time_to_the_second_with_offset_host_and_program_pid() -> None:
    line = env.LogfmtFormatter().format(_record(env.event("halt.clear")))
    match = HEADER.match(line)
    assert match is not None, line
    assert match["host"] == socket.gethostname().split(".", 1)[0]
    assert match["pid"] == str(os.getpid())


def test_account_level_and_event_come_first_then_the_fields_in_order() -> None:
    record = _record(env.event("fetch.search", account=ME, matched=40, got=23))
    assert _body(record) == (
        f"account={ME} level=info event=fetch.search matched=40 got=23"
    )


def test_a_field_with_no_value_is_left_out_and_zero_is_a_value() -> None:
    record = _record(
        env.event("x.y", none=None, empty="", false=False, nothing=[], zero=0)
    )
    assert _body(record) == "level=info event=x.y zero=0"


def test_a_boolean_is_written_only_when_true() -> None:
    halted = _body(_record(env.event("fetch.end", halted=True)))
    assert halted.endswith(" halted=true")
    assert "halted" not in _body(_record(env.event("fetch.end", halted=False)))


@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("plain", "plain"),
        ("a@b.com", "a@b.com"),
        ("wicket/env.py:12", "wicket/env.py:12"),
        ("two words", '"two words"'),
        ("k=v", '"k=v"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash here", '"back\\\\slash here"'),
        ("one\ntwo", '"one\\ntwo"'),
        ("tab\there", '"tab\\there"'),
        ("bell\x07", '"bell\\u0007"'),
        ("del\x7f", '"del\\u007f"'),
        ("café", "café"),  # printable unicode stays bare
        ("next\x85line", '"next\\u0085line"'),  # C1: splitlines breaks on it
        ("para\u2029graph", '"para\\u2029graph"'),
        ("line\u2028sep", '"line\\u2028sep"'),
        ("file\x1csep", '"file\\u001csep"'),
        ("no\u00a0break", '"no\\u00a0break"'),  # not printable to Python
        ("tag\U000e0001", '"tag\\udb40\\udc01"'),  # above U+FFFF: a surrogate pair
    ],
)
def test_a_value_is_quoted_when_it_holds_a_space_an_equals_a_quote_or_a_control(
    value: str, written: str
) -> None:
    body = _body(_record(env.event("x.y", v=value)))
    assert body == f"level=info event=x.y v={written}"
    assert len(body.splitlines()) == 1  # one record, one line, however it is split


def test_the_account_is_the_records_own_else_the_process_account_else_none() -> None:
    assert _body(_record(env.event("x.y"))) == "level=info event=x.y"
    env.set_process_account(ME)
    assert _body(_record(env.event("x.y"))) == f"account={ME} level=info event=x.y"
    own = _record(env.event("x.y", account=WORK))
    assert _body(own) == f"account={WORK} level=info event=x.y"


def test_one_record_gives_the_same_line_in_the_account_log_and_wicket_log() -> None:
    env.configure_logging()
    env.append_log(ME, "halt.set", cause="month 2026-03: TimeoutError")
    assert _account_log()[-1] == _wicket_log()[-1]
    assert _account_log()[-1].endswith(
        f': account={ME} level=info event=halt.set cause="month 2026-03: TimeoutError"'
    )


# --- wicket.log -------------------------------------------------------------------


@pytest.mark.parametrize("tty", [True, False])
def test_wicket_log_is_written_by_a_terminal_run_and_a_cron_run_alike(
    tty: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        monkeypatch.setattr(stream, "isatty", lambda: tty)
    assert halt_cli.main(["set", "--mail-account", ME]) == 0
    assert capsys.readouterr().err == ""  # stderr shows no logfmt line
    assert _bodies() == [
        f'account={ME} level=info event=halt.set cause="by hand '
        '(python -m wicket.halt set)"'
    ]


def test_wicket_log_is_0600_in_a_0700_dir() -> None:
    env.configure_logging()
    logging.getLogger("wicket.test").info("", extra=env.event("x.y"))
    log = _state() / "log" / "wicket.log"
    assert log.stat().st_mode & 0o777 == 0o600
    assert log.parent.stat().st_mode & 0o777 == 0o700


def test_a_failed_wicket_log_write_does_not_stop_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(logging, "raiseExceptions", False)  # handleError stays quiet
    env.configure_logging()
    (_state() / "log" / "wicket.log").mkdir(parents=True)  # a dir: every open fails
    logger = logging.getLogger("wicket.test")
    logger.info("first", extra=env.event("x.y"))  # raises nothing
    logger.info("second", extra=env.event("x.y"))
    assert capsys.readouterr().err == "first\nsecond\n"  # the run and stderr go on


def test_stderr_shows_the_message_and_never_the_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    env.configure_logging()
    logger = logging.getLogger("wicket.test")
    logger.info("[plan] 1 year(s)", extra=env.event("catalog.plan", years=1))
    logger.info("", extra=env.event("fetch.start", **{"pass": "all"}))
    logger.warning("a message only")
    assert capsys.readouterr().err == "[plan] 1 year(s)\na message only\n"
    assert _bodies() == [
        "level=info event=catalog.plan years=1",
        "level=info event=fetch.start pass=all",
    ]


def test_configure_logging_is_idempotent() -> None:
    env.configure_logging()
    env.configure_logging()
    logger = logging.getLogger(env.LOGGER_NAME)
    ours = [
        h
        for h in logger.handlers
        if isinstance(h, (env.StderrHandler, env.WicketLogHandler))
    ]
    assert len(ours) == 2 and logger.level == logging.INFO


def test_each_test_starts_with_no_handler_no_hook_and_no_account() -> None:
    """The conftest fixture undoes what a face's `main` set up in the test before."""
    logger = logging.getLogger(env.LOGGER_NAME)
    assert not any(
        isinstance(h, (env.StderrHandler, env.WicketLogHandler))
        for h in logger.handlers
    )
    assert env.process_account() is None


# --- crashes ------------------------------------------------------------------------


def _crash() -> tuple[type[BaseException], BaseException, TracebackType | None]:
    """An exception raised inside the package: `normalize_account` on None."""
    try:
        env.normalize_account(None)  # type: ignore[arg-type]
    except AttributeError as exc:
        return type(exc), exc, exc.__traceback__
    raise AssertionError("no crash")


def test_a_crash_on_the_main_thread_logs_one_line_then_prints_its_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[type[BaseException]] = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, _e, _t: printed.append(kind))
    env.configure_logging()
    kind, exc, tb = _crash()
    sys.excepthook(kind, exc, tb)
    assert printed == [AttributeError]  # the hook in place prints today's traceback
    (line,) = _wicket_log()
    assert re.search(
        r' level=critical event=wicket.end outcome=crashed error="AttributeError: '
        r"[^\"]*\" at=wicket/env.py:\d+$",
        line,
    ), line


def test_a_keyboard_interrupt_goes_to_the_hook_unlogged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[type[BaseException]] = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, _e, _t: printed.append(kind))
    env.configure_logging()
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert printed == [KeyboardInterrupt] and _wicket_log() == []


def test_a_crash_in_a_worker_thread_logs_one_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[type[BaseException]] = []
    monkeypatch.setattr(
        threading, "excepthook", lambda args: seen.append(args.exc_type)
    )
    env.configure_logging()
    worker = threading.Thread(target=env.normalize_account, args=(None,))
    worker.start()
    worker.join()
    assert seen == [AttributeError]
    (line,) = _wicket_log()
    assert " event=wicket.end outcome=crashed " in line and " at=wicket/env.py:" in line


# --- cases --------------------------------------------------------------------------


def test_a_worker_threads_halt_names_its_own_account(tmp_path: Path) -> None:
    env.configure_logging()
    env.set_process_account(WORK)  # what the api set for the run
    creds = tmp_path / ME / "imap.json"
    worker = threading.Thread(target=auth.write_halt, args=(creds, "month 2026-03: x"))
    worker.start()
    worker.join()
    (line,) = [line for line in _wicket_log() if "event=halt.set" in line]
    assert f" account={ME} " in line and WORK not in line


def test_halt_clear_all_names_each_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accounts = tmp_path / "config" / "accounts"
    for account in (ME, WORK):
        (accounts / account).mkdir(parents=True)
        (accounts / account / "imap.json").write_text("{}")
        auth.write_halt(Path(account) / "imap.json", "set")
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "config")
    assert halt_cli.main(["clear", "--all"]) == 0
    cleared = [line for line in _wicket_log() if "event=halt.clear" in line]
    assert [_account_of(line) for line in cleared] == [WORK, ME]  # name order


@pytest.fixture(name="mail")
def _mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A mail root with this account's store and its imap.json link to Gmail."""
    mail = tmp_path / "mail"
    (mail / ME).mkdir(parents=True)
    (mail / WORK).mkdir(parents=True)
    target = tmp_path / "gmail" / ME / "imap.json"
    target.parent.mkdir(parents=True)
    target.write_text(f'{{"email": "{ME}", "app_password": "pw"}}', encoding="utf-8")
    link = tmp_path / "wicket" / "accounts" / ME / "imap.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    monkeypatch.setattr(env, "MAIL_ROOT", mail)
    monkeypatch.setattr(root_api, "MAIL_ROOT", mail)
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    return mail


def _staged_stats() -> dict[str, object]:
    return {
        "account": ME,
        "added": 2,
        "added_by_year": {2026: 2},
        "filed": {f"{ME}/messages/2026/07": ["a1.eml", "a2.eml"]},
        "copies": 0,
        "subject_mismatches": [],
        "staged": 3,
        "archived": 1,
        "duplicates": 0,
        "linked": 0,
        "excluded": 0,
        "excluded_lists": 0,
        "refused": ["t1/m1@20260706T090004Z.eml"],
        "unrecognized": [],
    }


def test_ingest_staged_appends_ingest_end_with_counts_only(
    mail: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    monkeypatch.setattr(root_api, "ingest_staged", lambda **_k: _staged_stats())
    root_api.ingest_root(mail_account=ME, staged=True)
    (line,) = _account_log()
    assert line.endswith(
        f": account={ME} level=info event=ingest.end added=2 copies=0 excluded=0 "
        "excluded_lists=0 refused=1 unrecognized=0"
    )
    assert "/" not in line.split(": ", 1)[1]  # never filed's paths


def test_a_dry_run_writes_no_account_log(
    mail: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    monkeypatch.setattr(root_api, "ingest_staged", lambda **_k: _staged_stats())
    env.configure_logging()
    root_api.ingest_root(mail_account=ME, staged=True, dry_run=True)
    assert _account_log() == []
    assert _bodies() == [
        f"account={ME} level=info event=ingest.end added=2 copies=0 excluded=0 "
        "excluded_lists=0 refused=1 unrecognized=0 dry_run=true"
    ]


def test_a_routed_ingest_appends_ingest_end_to_each_store_it_filed_to(
    mail: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    stats: dict[str, object] = {
        "files": 3,
        "unique": 3,
        "dup": 0,
        "accounts": [
            {"account": ME, "added": 2, "skipped": 1},
            {"account": WORK, "added": 1, "skipped": 0},
        ],
        "busy": [],
    }
    monkeypatch.setattr(root_api, "ingest_routed", lambda **_k: stats)
    root_api.ingest(None, options=root_api.IngestOptions(src=tmp_path))
    assert _account_log()[-1].endswith(
        f"account={ME} level=info event=ingest.end added=2 skipped=1"
    )
    assert _account_log(WORK)[-1].endswith(
        f"account={WORK} level=info event=ingest.end added=1 skipped=0"
    )


def test_a_dry_routed_ingest_logs_ingest_end_to_wicket_log_alone(
    mail: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    env.configure_logging()
    stats: dict[str, object] = {
        "files": 3,
        "unique": 3,
        "dup": 0,
        "accounts": [
            {"account": ME, "added": 2, "skipped": 1},
            {"account": WORK, "added": 1, "skipped": 0},
        ],
        "busy": [],
    }
    monkeypatch.setattr(root_api, "ingest_routed", lambda **_k: stats)
    root_api.ingest(None, options=root_api.IngestOptions(src=tmp_path, dry_run=True))
    assert _account_log() == [] and _account_log(WORK) == []
    assert _bodies() == [
        f"account={ME} level=info event=ingest.end added=2 skipped=1 dry_run=true",
        f"account={WORK} level=info event=ingest.end added=1 skipped=0 dry_run=true",
    ]


def test_a_dry_single_ingest_logs_ingest_end_to_wicket_log_alone(
    mail: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    env.configure_logging()
    stats: dict[str, object] = {"account": ME, "added": 2, "skipped": 0}
    monkeypatch.setattr(root_api, "ingest_folder", lambda **_k: stats)
    root_api.ingest(ME, options=root_api.IngestOptions(src=tmp_path, dry_run=True))
    assert _account_log() == []
    assert _bodies() == [
        f"account={ME} level=info event=ingest.end added=2 skipped=0 dry_run=true"
    ]


def test_a_busy_routed_ingest_logs_ingest_busy_per_store_then_the_refusal(
    mail: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    env.configure_logging()
    stats: dict[str, object] = {
        "files": 2,
        "unique": 2,
        "dup": 0,
        "accounts": [],
        "added": 0,
        "skipped": 0,
        "copies": 0,
        "subject_mismatches": [],
        "excluded": 0,
        "excluded_lists": 0,
        "filed": {},
        "unrouted": 0,
        "unrouted_threads": 0,
        "unrouted_held": 0,
        "busy": [
            {"account": ME, "pid": "4242", "messages": 1},
            {"account": WORK, "pid": "", "messages": 1},
        ],
        "trashable": 0,
        "trashed": 0,
    }
    monkeypatch.setattr(root_api, "ingest_routed", lambda **_k: stats)
    with pytest.raises(ApiError) as refused:
        root_api.ingest_root(src=tmp_path, no_delete=True)
    assert refused.value.reason == "busy"
    assert _bodies() == [
        f"account={ME} level=warning event=ingest.busy lock_pid=4242 left=1",
        f"account={WORK} level=warning event=ingest.busy left=1",
        "level=error event=wicket.end outcome=refused exit=2 reason=busy",
    ]


def test_fetch_events_carry_their_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``fetch.search`` from the worker: today's words, ``beyond_max`` only past --max."""
    env.configure_logging()

    def discover(*_args: object, **_kw: object) -> tuple[list[object], int, int]:
        return [], 10, 7

    monkeypatch.setattr(fetch_lib, "_discover_records", discover)
    ctx = ThreadContext(
        dest=tmp_path / "messages",
        dry_run=True,
        store_dir=tmp_path / "manifest",
        staging_dir=tmp_path / "staging",
        duplicates_dir=tmp_path / ".duplicates",
        provider=GMAIL,
    )
    for limit in (2, None):
        download(
            search=GMAIL.raw_search("q"),
            ctx=ctx,
            credentials_path=tmp_path / "c",
            limit=limit,
        )
    assert _bodies() == [
        "level=info event=fetch.search matched=10 got=3 candidates=2 beyond_max=5 "
        "dry_run=true",
        "level=info event=fetch.search matched=10 got=3 candidates=7 dry_run=true",
    ]


def test_fetch_plan_and_month_carry_their_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real pass: three rows over two months, the second month halted."""
    env.configure_logging()
    pending: list[dict[str, object]] = [
        {"month": "2024-01"},
        {"month": "2024-01"},
        {"month": "2024-02"},
    ]
    halt = auth.Halted(Path("/s/halt") / ME, "month 2024-02: x", cause="x")

    def worker(
        _creds: Path,
        _ctx: object,
        work_q: "Queue[tuple[str, list[dict[str, object]]] | None]",
        results_q: "Queue[object]",
    ) -> None:
        while (item := work_q.get()) is not None:
            month, rows = item
            if month == "2024-02":
                results_q.put(fetch_lib._Month(month, [], halt=halt))
            else:
                results_q.put(fetch_lib._Month(month, rows))

    monkeypatch.setattr(fetch_lib, "_got", lambda _ctx: (set(), {}))
    monkeypatch.setattr(fetch_lib, "_discover_records", lambda *_a, **_k: ([], 3, 3))
    monkeypatch.setattr(fetch_lib, "_plan", lambda *_a: (pending, []))
    monkeypatch.setattr(fetch_lib, "_month_worker", worker)
    ctx = ThreadContext(
        dest=tmp_path / "messages",
        dry_run=False,
        store_dir=tmp_path / "manifest",
        staging_dir=tmp_path / "staging",
        duplicates_dir=tmp_path / ".duplicates",
    )
    fetch_lib._pass(
        conn=None,
        search=GMAIL.raw_search("q"),
        ctx=ctx,
        credentials_path=tmp_path / "c",
        limit=None,
        threads=1,
    )
    assert _bodies() == [
        "level=info event=fetch.search matched=3 got=0 candidates=3",
        "level=info event=fetch.plan pending=3 months=2",
        "level=info event=fetch.month done=1 total=2 saved=2",
        "level=info event=fetch.month done=2 total=2 saved=2 failed=1",
    ]


def test_catalog_plan_year_and_discover_carry_their_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One year swept, dry and real, then a whole-mailbox sweep of an empty mailbox."""
    env.configure_logging()
    stats = dict.fromkeys(catalog_lib.STAT_KEYS, 0)
    monkeypatch.setattr(catalog_lib, "_sweep_year", lambda **_k: ([], dict(stats)))
    for dry_run in (True, False):
        catalog_lib.sweep(
            store_dir=tmp_path / "manifest",
            credentials_path=tmp_path / "c",
            mailbox=None,
            years=[2024],
            dry_run=dry_run,
        )
    monkeypatch.setattr(catalog_lib, "open_mailbox", lambda *_a, **_k: object())
    monkeypatch.setattr(catalog_lib, "first_year", lambda *_a: None)
    monkeypatch.setattr(catalog_lib, "logout_quietly", lambda _conn: None)
    catalog_lib.sweep(
        store_dir=tmp_path / "manifest",
        credentials_path=tmp_path / "c",
        mailbox=None,
        years=None,
        dry_run=False,
    )
    assert _bodies() == [
        "level=info event=catalog.plan years=1 first=2024 last=2024 threads=1 "
        "dry_run=true",
        "level=info event=catalog.year year=2024 observed=0 dry_run=true",
        "level=info event=catalog.plan years=1 first=2024 last=2024 threads=1",
        "level=info event=catalog.year year=2024 observed=0 in_shard=0",
        "level=info event=catalog.discover empty=true",
    ]


def test_the_fetch_summary_is_counts_and_a_halt_flag() -> None:
    assert root_api._summary({"downloaded": 6, "failed": 0, "linked": 1}) == {
        "downloaded": 6,
        "failed": 0,
        "linked": 1,
        "halted": False,
    }
    assert root_api._summary({"halted": True})["halted"] is True
