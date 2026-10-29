"""The HALT, homed in `wicket.auth` (plan rules 4 and 5).

Every IMAP connection goes through `auth.open_mailbox`, so that is where a HALT is
enforced, and `write_halt` / `clear_halt` are the one door that sets and clears one,
each logging one line. A HALT is an empty, touched file: its name is the account, its
timestamp is when it was set, present means halted, and it never expires. No test here
reaches the network: `imaplib.IMAP4_SSL` is replaced by a fake in every test, and the
halt and log dirs follow ``$XDG_STATE_HOME`` to a temp dir.
"""

from __future__ import annotations

import imaplib
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

from wicket import auth, env

ACCOUNT = "you@gmail.com"
PASSWORD = "abcdabcdabcdabcd"


class FakeConn:
    """Just enough of an IMAP4_SSL for `open_mailbox` and `first_year`."""

    def __init__(
        self,
        *,
        login_error: str | None = None,
        select_typ: str = "OK",
        uid: Callable[..., tuple[str, Sequence[object]]] | None = None,
    ) -> None:
        self.login_error = login_error
        self.select_typ = select_typ
        self._uid = uid
        self.readonly: bool | None = None
        self.logins = 0

    def login(self, _email: str, _password: str) -> tuple[str, list[bytes]]:
        self.logins += 1
        if self.login_error:
            raise imaplib.IMAP4.error(self.login_error)
        return "OK", [b""]

    def select(self, _mailbox: str, readonly: bool = True) -> tuple[str, list[bytes]]:
        self.readonly = readonly
        return self.select_typ, [b"1"]

    def uid(self, *args: object) -> tuple[str, list[object]]:
        assert self._uid is not None
        typ, data = self._uid(*args)
        return typ, list(data)

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b""]


def _imap(fake: FakeConn) -> imaplib.IMAP4_SSL:
    """The fake, typed as what the code under test takes (it duck-types the calls used)."""
    return cast(imaplib.IMAP4_SSL, fake)


@pytest.fixture(name="state")
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A credential file and a temp run dir."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    creds = tmp_path / "gmail" / ACCOUNT / "imap.json"
    creds.parent.mkdir(parents=True)
    creds.write_text(f'{{"email": "{ACCOUNT}", "app_password": "{PASSWORD}"}}')
    return creds


class Net(list[FakeConn]):
    """Every connection made, in order; ``next`` presets the one after."""

    def __init__(self) -> None:
        super().__init__()
        self.next: list[FakeConn] = []


@pytest.fixture(name="connects")
def _connects(monkeypatch: pytest.MonkeyPatch) -> Net:
    net = Net()

    def factory(*_args: object, **_kwargs: object) -> FakeConn:
        conn = net.next.pop(0) if net.next else FakeConn()
        net.append(conn)
        return conn

    monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
    return net


def _state_base() -> Path:
    return Path(os.environ["XDG_STATE_HOME"]) / "wicket"


def _halt_file() -> Path:
    return _state_base() / "halt" / ACCOUNT


def _log() -> str:
    path = _state_base() / "log" / f"{ACCOUNT}.log"
    return path.read_text() if path.exists() else ""


def _plant(text: str, *, hours_old: float = 0.0, account: str = ACCOUNT) -> Path:
    path = _halt_file().with_name(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    then = time.time() - hours_old * 3600
    os.utime(path, (then, then))
    return path


def test_open_mailbox_refuses_past_a_halt(state: Path, connects: Net) -> None:
    halt = _plant("", hours_old=3)
    with pytest.raises(auth.Halted) as refused:
        auth.open_mailbox(state, interactive=False)
    assert connects == []  # no connection was even attempted
    assert refused.value.line.startswith("halted since ")
    assert refused.value.path == halt


def test_a_halt_never_expires(state: Path, connects: Net) -> None:
    """However old, a HALT holds until `clear_halt` removes it; any text in it is ignored."""
    for text in ("", "throttled at 2019 chunk 3 2026-10-22 08:00\n"):
        halt = _plant(text, hours_old=500)
        with pytest.raises(auth.Halted):
            auth.open_mailbox(state, interactive=False)
        assert halt.exists() and connects == [] and _log() == ""


def test_write_halt_touches_an_empty_file_and_logs_its_cause(state: Path) -> None:
    before = time.time()
    halted = auth.write_halt(state, "month 2019-03: TimeoutError")
    assert _halt_file().read_text() == ""  # a touched file: the name and the time
    assert before - 1 <= _halt_file().stat().st_mtime <= time.time() + 1
    assert halted.line == "month 2019-03: TimeoutError"
    assert _log().splitlines()[-1].endswith("HALT set month 2019-03: TimeoutError")


def test_write_halt_keeps_the_first_halt_and_its_time(state: Path) -> None:
    halt = _plant("", hours_old=5)
    then = halt.stat().st_mtime
    second = auth.write_halt(state, "a later cause")
    assert halt.stat().st_mtime == then  # never touched again
    assert second.line.startswith("halted since ") and "later" not in _log()


def test_a_rejected_app_password_logs_the_page(state: Path, connects: Net) -> None:
    connects.next.append(
        FakeConn(login_error="[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
    )
    with pytest.raises(auth.Halted) as refused:
        auth.open_mailbox(state, interactive=False)
    assert _halt_file().read_text() == ""
    assert "https://myaccount.google.com/apppasswords" in _log()
    assert str(state) not in _log() and PASSWORD not in _log()
    assert PASSWORD not in str(refused.value)


@pytest.mark.parametrize(
    "conn",
    [FakeConn(login_error="[ALERT] Too many connections"), FakeConn(select_typ="NO")],
)
def test_a_rejected_login_or_select_writes_a_halt(
    state: Path, connects: Net, conn: FakeConn
) -> None:
    connects.next.append(conn)
    with pytest.raises(auth.Halted):
        auth.open_mailbox(state, interactive=False)
    assert _halt_file().is_file()


def test_an_errors_text_is_never_logged(state: Path) -> None:
    """A disk error's text names a path; the log may hold none."""
    error = OSError(28, "No space left on device", "/vault/you/staging/1/2.eml")
    halted = auth.throttle_halt(state, "month 2024-03", error)
    assert halted.line == "month 2024-03: OSError"
    assert "/vault" not in _log() and _log().rstrip().endswith("month 2024-03: OSError")


def test_a_connect_timeout_writes_a_halt(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def times_out(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", times_out)
    with pytest.raises(auth.Halted):
        auth.open_mailbox(state, interactive=False)
    assert _halt_file().is_file()


def test_an_unreachable_host_is_no_halt(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No network is not Gmail throttling: an error, exit 2, and no HALT."""

    def unreachable(*_args: object, **_kwargs: object) -> None:
        raise OSError("nodename nor servname provided")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", unreachable)
    with pytest.raises(auth.AuthError) as error:
        auth.open_mailbox(state, interactive=False)
    assert not isinstance(error.value, getattr(auth, "Halted", ()))
    assert not _halt_file().exists()


def test_first_year_is_the_lowest_uids_internaldate() -> None:
    """Moved from catalog so fetch and catalog start a whole-mailbox pass alike."""
    asked: list[tuple[object, ...]] = []

    def uid(*args: object) -> tuple[str, list[object]]:
        asked.append(args)
        if args[0] == "SEARCH":
            return "OK", [b"5 9 12"]
        return "OK", [b'5 (UID 5 INTERNALDATE "03-Feb-2007 23:10:00 -0800")']

    assert auth.first_year(_imap(FakeConn(uid=uid))) == 2007  # UTC: 04-Feb-2007 07:10
    assert asked[1][1] == "5"
    assert auth.first_year(_imap(FakeConn(uid=lambda *_a: ("OK", [b""])))) is None
    with pytest.raises(imaplib.IMAP4.error):
        auth.first_year(_imap(FakeConn(uid=lambda *_a: ("NO", [b""]))))


# --- the one door: every HALT set and cleared is logged (owner, 2026-10-27) ----


def test_halt_and_log_follow_xdg_state_home_in_two_dirs_and_resolving_creates_nothing(
    state: Path, tmp_path: Path
) -> None:
    del state
    halt = env.resolve_halt_path("You+tag@Gmail.com")
    log = env.resolve_log_path("You+tag@Gmail.com")
    assert halt == tmp_path / "xdg" / "wicket" / "halt" / ACCOUNT
    assert log == tmp_path / "xdg" / "wicket" / "log" / f"{ACCOUNT}.log"
    assert halt.parent != log.parent  # halt and log never share a folder
    assert not (tmp_path / "xdg").exists()
    made = env.resolve_halt_dir(create=True)
    assert made == halt.parent and made.stat().st_mode & 0o777 == 0o700
    env.append_log(ACCOUNT, "one\ntwo")
    assert log.parent.stat().st_mode & 0o777 == 0o700
    assert log.stat().st_mode & 0o777 == 0o600
    assert log.read_text().endswith(" one two\n")  # one call is one line


def test_a_halt_lives_in_halt_and_its_log_in_log_never_one_dir(state: Path) -> None:
    auth.write_halt(state, "slow")
    assert _halt_file().is_file()
    assert (_state_base() / "log" / f"{ACCOUNT}.log").is_file()
    assert sorted(p.name for p in _state_base().iterdir()) == ["halt", "log"]
    for folder in ("halt", "log"):
        assert (_state_base() / folder).stat().st_mode & 0o777 == 0o700
    assert _halt_file().stat().st_mode & 0o777 == 0o600


def test_a_cause_is_logged_on_one_line(state: Path) -> None:
    auth.write_halt(state, "two\nlines")
    assert _log().splitlines()[-1].endswith("HALT set two lines")


def test_clear_halt_logs_then_removes(state: Path) -> None:
    del state
    _plant("", hours_old=2)
    info = auth.clear_halt(ACCOUNT)
    assert info is not None and info.account == ACCOUNT
    assert not _halt_file().exists()
    assert list(_halt_file().parent.iterdir()) == []  # no HALT.cleared-* left behind
    assert _log().splitlines()[-1].endswith("HALT cleared")


def test_an_unlink_that_finds_the_halt_gone_counts_as_cleared(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another process cleared it first: this one still reports it cleared."""
    del state
    _plant("")

    def gone(self: Path, missing_ok: bool = False) -> None:
        del missing_ok
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "unlink", gone)
    assert auth.clear_halt(ACCOUNT) is not None


def test_clearing_an_absent_halt_writes_nothing(state: Path) -> None:
    del state
    assert auth.clear_halt(ACCOUNT) is None
    assert not _state_base().exists()  # no dir made, no log line written


def test_list_halts_reads_each_files_name_and_timestamp(state: Path) -> None:
    del state
    _plant("", hours_old=3)
    _plant("", hours_old=50, account="work@corp.com")
    found = auth.list_halts()
    assert [h.account for h in found] == ["work@corp.com", ACCOUNT]
    assert 3 * 3600 - 5 <= found[1].age_seconds <= 3 * 3600 + 5
    assert abs(found[1].halted.timestamp() - (time.time() - 3 * 3600)) < 5
    assert found[1].halted.tzinfo is not None
