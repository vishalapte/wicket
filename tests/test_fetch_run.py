"""fetch until throttled (plan rules 1-3 and 5 as fetch applies them).

The year loop is tested with the pass stubbed (`fetch.lib._pass`) and the store's
downloaded count simulated; the month workers and body checks with a fake connection.
No test reaches the network: `imaplib.IMAP4_SSL` refuses in every test.
"""

from __future__ import annotations

import imaplib
import os
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import cast

import pytest

import wicket.__main__ as root
import wicket.fetch.lib as fetch_lib
from wicket import auth, env
from wicket.fetch import api as fetch_api
from wicket.fetch import cli as fetch_cli
from wicket.manifest import Row

ME = "you@gmail.com"


@pytest.fixture(autouse=True)
def _offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("no network in tests")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))


def _halt_file() -> Path:
    return Path(os.environ["XDG_STATE_HOME"]) / "wicket" / "halt" / ME


def _log() -> str:
    """The account's log: where a HALT's cause is kept (the HALT file itself is empty)."""
    path = Path(os.environ["XDG_STATE_HOME"]) / "wicket" / "log" / f"{ME}.log"
    return path.read_text() if path.exists() else ""


class FakeConn:
    """A discovery or worker connection: a scripted ``uid``."""

    def __init__(
        self, uid: Callable[..., tuple[str, Sequence[object]]] | None = None
    ) -> None:
        self._uid = uid

    def uid(self, *args: object) -> tuple[str, list[object]]:
        assert self._uid is not None
        typ, data = self._uid(*args)
        return typ, list(data)

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b""]


def _imap(fake: FakeConn) -> imaplib.IMAP4_SSL:
    """The fake, typed as what the code under test takes (it duck-types the calls used)."""
    return cast(imaplib.IMAP4_SSL, fake)


def _creds(tmp_path: Path) -> Path:
    path = tmp_path / "gmail" / ME / "imap.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{{"email": "{ME}", "app_password": "pw"}}')
    return path


def _ctx(tmp_path: Path, *, dry_run: bool = False) -> fetch_lib.ThreadContext:
    return fetch_lib.ThreadContext(
        dest=tmp_path / "archive",
        imap_email=ME,
        domain_aliases={},
        dry_run=dry_run,
        store_dir=tmp_path / "manifest",
        staging_dir=tmp_path / "staging",
        duplicates_dir=tmp_path / ".duplicates",
    )


class Years:
    """Stub the pass: each year's queue of downloads per pass grows the store's count."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, growth: dict[int, list[int]]):
        self.growth = {y: list(n) for y, n in growth.items()}
        self.rows = 0
        self.calls: list[tuple[str, int | None]] = []
        self.halt_on: int | None = None
        self.conn = FakeConn()
        monkeypatch.setattr(fetch_lib, "_pass", self._pass)
        monkeypatch.setattr(fetch_lib, "_got_count", lambda _ctx: self.rows)
        monkeypatch.setattr(fetch_lib, "open_mailbox", lambda *_a, **_k: self.conn)
        monkeypatch.setattr(fetch_lib, "first_year", lambda _conn: 2022)
        monkeypatch.setattr(fetch_lib, "reconcile", lambda *_a: {})

    def _pass(self, **kw: object) -> tuple[dict[str, int], auth.Halted | None]:
        query, limit = str(kw["query"]), kw["limit"]
        self.calls.append((query, limit))  # type: ignore[arg-type]
        year = int(query[6:10])
        got = self.growth.get(year, [])
        n = got.pop(0) if got else 0
        if limit is not None:
            n = min(n, int(limit))  # type: ignore[call-overload]
        self.rows += n
        counts = fetch_lib._counts()
        counts.update(candidates=n, downloaded=n)
        halt = None
        if year == self.halt_on:
            creds = cast(Path, kw["credentials_path"])
            halt = auth.write_halt(creds, "test")
        return counts, halt

    def years(self) -> list[int]:
        return [int(q[6:10]) for q, _ in self.calls]


def _hooks(**kw: object) -> fetch_lib.RunHooks:
    base: dict[str, object] = {
        "this_year": lambda: 2026,
    }
    base.update(kw)
    return fetch_lib.RunHooks(**base)  # type: ignore[arg-type]


def _run(tmp_path: Path, limit: int | None = None, **kw: object) -> dict[str, object]:
    return fetch_lib.download_years(
        ctx=_ctx(tmp_path),
        credentials_path=_creds(tmp_path),
        limit=limit,
        hooks=_hooks(**kw),
    )


def test_no_filter_passes_this_year_last_year_then_the_rest_oldest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    years = Years(monkeypatch, {})
    stats = _run(tmp_path)
    assert years.years() == [
        2026,
        2025,
        2022,
        2023,
        2024,
    ]  # first year 2022, none skipped
    assert years.calls[0] == ("after:2026/01/01 before:2027/01/01", None)  # no cap
    assert stats["halted"] is False and len(stats["passes"]) == 5  # type: ignore[arg-type]


def test_a_year_is_one_pass_with_no_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    years = Years(monkeypatch, {2026: [1703, 3]})
    stats = _run(tmp_path)
    assert years.years() == [2026, 2025, 2022, 2023, 2024]  # 2026 once, not again
    assert stats["downloaded"] == 1703


def test_max_bounds_the_whole_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    years = Years(monkeypatch, {2026: [500], 2025: [500]})
    _run(tmp_path, limit=700)
    assert years.calls == [
        ("after:2026/01/01 before:2027/01/01", 700),
        ("after:2025/01/01 before:2026/01/01", 200),
    ]


def test_a_halt_in_a_pass_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    years = Years(monkeypatch, {2026: [10]})
    years.halt_on = 2026
    stats = _run(tmp_path)
    assert years.years() == [2026]
    assert stats["halted"] is True and str(stats["halt"]) and _halt_file().is_file()


def test_a_refused_discovery_connection_returns_halted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds = _creds(tmp_path)
    auth.write_halt(creds, "Gmail rejected the app password")
    monkeypatch.setattr(fetch_lib, "reconcile", lambda *_a: {})
    stats = fetch_lib.download_years(
        ctx=_ctx(tmp_path), credentials_path=creds, limit=None, hooks=_hooks()
    )
    assert stats["halted"] is True and stats["passes"] == []
    assert str(stats["halt"]).startswith("halted since ")


# --- the months: short bodies, failed months, the stop -----------------------


def _entry(uid: bytes, month: str = "2024-03") -> Row:
    return {"uid": uid, "month": month, "path": f"acme.com/{month}/{uid.decode()}.eml"}


def _bodies(*rows: tuple[bytes, int, bytes]) -> Callable[..., tuple[str, list[object]]]:
    def uid(*args: object) -> tuple[str, list[object]]:
        assert args[2] == "(RFC822.SIZE BODY.PEEK[])"
        data: list[object] = []
        for n, (u, size, body) in enumerate(rows, 1):
            head = f"{n} (UID {u.decode()} RFC822.SIZE {size} BODY[] {{{len(body)}}}"
            data += [(head.encode(), body), b")"]
        return "OK", data

    return uid


def test_a_short_body_is_never_written_and_halts(tmp_path: Path) -> None:
    creds = _creds(tmp_path)
    conn = FakeConn(
        _bodies((b"7", 100, b"x" * 50), (b"8", 10, b"y" * 10), (b"9", 5, b""))
    )
    pending = [_entry(b"7"), _entry(b"8"), _entry(b"9")]
    result = fetch_lib._write_month(
        _imap(conn), "2024-03", pending, _ctx(tmp_path), creds
    )
    assert [e["uid"] for e in result.written] == [b"8"]
    staged = sorted(p.name for p in (tmp_path / "staging").rglob("*.eml"))
    assert staged == ["8.eml"]
    assert not (tmp_path / "archive").exists()  # fetch files nothing
    assert result.halt is not None
    assert _halt_file().read_text() == ""
    assert "HALT set short body: 2 message(s)" in _log()


def test_a_size_after_the_literal_is_read() -> None:
    def uid(*_args: object) -> tuple[str, list[object]]:
        return "OK", [(b"1 (UID 7 BODY[] {3}", b"abc"), b" RFC822.SIZE 9)"]

    assert fetch_lib._fetch_bodies_batch(_imap(FakeConn(uid)), [b"7"]) == {
        b"7": (9, b"abc")
    }


def test_a_failed_month_halts_and_the_rest_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One worker: the first month's FETCH is rejected, the next two never start."""
    creds = _creds(tmp_path)
    conn = FakeConn(lambda *_a: ("NO", [b"[THROTTLED]"]))
    monkeypatch.setattr(fetch_lib, "open_mailbox", lambda *_a, **_k: conn)
    by_month = {m: [_entry(b"1", m)] for m in ("2024-01", "2024-02", "2024-03")}
    written, failed, halt = fetch_lib._retrieve_months(
        credentials_path=creds, by_month=by_month, ctx=_ctx(tmp_path), threads=1
    )
    assert (written, failed) == ([], 1)
    assert halt is not None and halt.line.startswith("month 2024-01")
    assert "HALT set month 2024-01" in _log()


def test_a_refused_worker_skips_its_months_and_writes_no_second_halt(
    tmp_path: Path,
) -> None:
    creds = _creds(tmp_path)
    auth.write_halt(creds, "the first cause")
    by_month = {m: [_entry(b"1", m)] for m in ("2024-01", "2024-02")}
    written, failed, halt = fetch_lib._retrieve_months(
        credentials_path=creds, by_month=by_month, ctx=_ctx(tmp_path), threads=2
    )
    assert (written, failed) == ([], 0)  # skipped, not failed
    assert halt is not None and halt.line.startswith("halted since ")
    assert _log().count("HALT set") == 1 and "HALT set the first cause" in _log()


def test_a_worker_checks_the_stop_before_each_month(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds = _creds(tmp_path)
    conn = FakeConn(_bodies((b"1", 4, b"body")))
    work: Queue[tuple[str, list[Row]] | None] = Queue()
    results: Queue[object] = Queue()
    work.put(("2024-01", [_entry(b"1", "2024-01")]))
    work.put(("2024-02", [_entry(b"2", "2024-02")]))
    work.put(None)
    real_write = fetch_lib._write_month

    def write_then_halt(*args: object) -> object:
        out = real_write(*args)  # type: ignore[arg-type]
        auth.write_halt(creds, "elsewhere")  # another worker halted
        return out

    monkeypatch.setattr(fetch_lib, "_write_month", write_then_halt)
    monkeypatch.setattr(fetch_lib, "open_mailbox", lambda *_a, **_k: conn)
    fetch_lib._month_worker(creds, _ctx(tmp_path), work, results)  # type: ignore[arg-type]
    first, second = results.get(), results.get()
    assert first.written and not first.skipped  # type: ignore[attr-defined]
    assert second.skipped and not second.written  # type: ignore[attr-defined]


def test_a_rejected_search_halts_the_pass(tmp_path: Path) -> None:
    creds = _creds(tmp_path)
    conn = FakeConn(lambda *_a: ("NO", [b""]))
    counts, halt = fetch_lib._pass(
        conn=_imap(conn),
        query="after:2024/01/01 before:2025/01/01",
        ctx=_ctx(tmp_path),
        credentials_path=creds,
        limit=500,
        threads=1,
    )
    assert halt is not None and counts["downloaded"] == 0
    assert "HALT set discovery" in _log()


# --- the api and the face -------------------------------------------------------


@pytest.fixture(name="account")
def _account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    mail = tmp_path / "mail"
    (mail / ME).mkdir(parents=True)
    monkeypatch.setattr(env, "MAIL_ROOT", mail)
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    (tmp_path / "wicket" / "accounts" / ME).mkdir(parents=True)
    (tmp_path / "wicket" / "accounts" / ME / "imap.json").symlink_to(_creds(tmp_path))
    return mail


def test_the_api_runs_years_with_no_filter_and_logs_counts_only(
    account: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    this = datetime.now().year  # the api runs on the real clock
    years = Years(monkeypatch, {this: [3]})
    stats = fetch_api.fetch(ME, options=fetch_api.FetchOptions())
    assert years.years() == [this, this - 1, *range(2022, this - 1)]  # one pass a year
    state = Path(os.environ["XDG_STATE_HOME"]) / "wicket"
    log = (state / "log" / f"{ME}.log").read_text()
    assert not (state / ME).exists()  # no per-account run dir any more
    assert f"{this} pass 1:" in log and "3 downloaded" in log and "run end:" in log
    assert "@" not in log and "/" not in log  # no address, no path
    assert stats["halted"] is False


def test_the_options_take_no_filter_but_not_two() -> None:
    assert fetch_api.FetchOptions().domains is None
    with pytest.raises(ValueError, match="at most one"):
        fetch_api.FetchOptions(domains="a.com", query="x")


def test_the_face_accepts_no_filter() -> None:
    args = root.parser().parse_args(["fetch", "--mail-account", ME])
    assert args.domains is None and args.query is None


def _fetched(monkeypatch: pytest.MonkeyPatch) -> list[fetch_api.FetchOptions]:
    """Every FetchOptions the face runs with, the run itself replaced by empty counts."""
    seen: list[fetch_api.FetchOptions] = []

    def fake(*_a: object, options: fetch_api.FetchOptions, **_k: object) -> object:
        seen.append(options)
        return {**fetch_lib._counts(), "passes": [], "halted": False}

    monkeypatch.setattr(fetch_cli, "fetch", fake)
    return seen


@pytest.mark.parametrize("last", ["2d", "3m", "10y"])
def test_last_runs_gmail_newer_than_as_one_filtered_pass(
    monkeypatch: pytest.MonkeyPatch, last: str
) -> None:
    seen = _fetched(monkeypatch)
    args = root.parser().parse_args(["fetch", "--mail-account", ME, "--last", last])
    assert fetch_cli.dispatch(args) == 0
    assert [(o.query, o.domains) for o in seen] == [(f"newer_than:{last}", None)]


@pytest.mark.parametrize(
    "other",
    [["--domains", "a.com"], ["--query", "x"], ["--domains", "a.com", "--query", "x"]],
)
def test_last_refuses_another_filter(
    capsys: pytest.CaptureFixture[str], other: list[str]
) -> None:
    with pytest.raises(SystemExit) as refused:
        root.parser().parse_args(["fetch", "--last", "2d", *other])
    assert refused.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["2h", "2w", "0d", "2x", "02d", "2D", "2dd", "d", "2"])
def test_last_refuses_anything_but_n_days_months_or_years(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    seen = _fetched(monkeypatch)
    args = root.parser().parse_args(["fetch", "--mail-account", ME, "--last", bad])
    assert fetch_cli.dispatch(args) == 2
    assert "--last takes N then d, m or y" in capsys.readouterr().err
    assert seen == []


def test_the_face_exits_3_on_a_halt_and_names_the_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    halted = {
        **{k: 0 for k in ("matched", "got", "pending", "linked")},
        "downloaded": 4,
        "failed": 1,
        "passes": [{"year": 2026}],
        "halted": True,
        "halt": "throttle 2026-10-27T08:00:00Z month 2026-03: abort",
        "halt_file": "/state/wicket/halt/you@gmail.com",
    }
    monkeypatch.setattr(fetch_cli, "fetch", lambda *_a, **_k: halted)
    code = fetch_cli.dispatch(root.parser().parse_args(["fetch", "--query", "x"]))
    out, err = capsys.readouterr()
    assert code == 3
    assert "done: 4 downloaded, 1 failed" in out
    assert "halted: throttle 2026-10-27T08:00:00Z" in err
    assert "HALT at /state/wicket/halt/you@gmail.com" in err
    assert "python -m wicket.halt clear --mail-account you@gmail.com" in err
    assert "aside" not in err


def test_the_face_exits_3_when_the_api_raises_halted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(*_a: object, **_k: object) -> None:
        raise fetch_api.Halted(
            Path("/s/halt/you@gmail.com"), "auth 2026-10-27T08:00:00Z rejected"
        )

    monkeypatch.setattr(fetch_cli, "fetch", refused)
    code = fetch_cli.dispatch(root.parser().parse_args(["fetch", "--query", "x"]))
    err = capsys.readouterr().err
    assert code == 3 and "HALT at /s/halt/you@gmail.com" in err
    assert "python -m wicket.halt clear --mail-account you@gmail.com" in err


def test_catalog_maps_a_timeout_to_a_throttle_halt_and_exits_3(
    account: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import wicket.catalog.lib as catalog_lib
    from wicket.catalog import cli as catalog_cli

    def times_out(*_args: object) -> tuple[str, list[object]]:
        raise TimeoutError("timed out")

    monkeypatch.setattr(
        catalog_lib, "open_mailbox", lambda *_a, **_k: FakeConn(times_out)
    )
    code = catalog_cli.dispatch(
        root.parser().parse_args(
            ["catalog", "--mail-account", ME, "--years", "2024", "--non-interactive"]
        )
    )
    assert code == 3
    assert _halt_file().read_text() == ""
    assert "HALT set catalog 2024: TimeoutError" in _log()
    err = capsys.readouterr().err
    assert "halted: catalog 2024: TimeoutError" in err
    assert f"python -m wicket.halt clear --mail-account {ME}" in err


def test_catalog_opens_no_year_past_the_stop(account: Path, tmp_path: Path) -> None:
    from wicket.catalog import api as catalog_api

    auth.write_halt(_creds(tmp_path), "an earlier year")
    # Refused, not "could not reach": the autouse fake would raise OSError on connect.
    with pytest.raises(auth.Halted):
        catalog_api.catalog(ME, options=catalog_api.CatalogOptions(years=[2023, 2024]))
