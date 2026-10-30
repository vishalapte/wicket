"""What the four root verbs print: stdout, stderr and the exit code, pinned whole.

One test per render and refusal branch of ``catalog``, ``fetch``, ``ingest`` and
``report``, run through ``wicket.__main__.main`` with ``sys.argv`` set, as the console
script runs it. The worker under each api is replaced by a function returning the stats a
real run returns, so the pin is the face and the api, never the mailbox. The mail root,
``~/.config/wicket/accounts``, the state home and the credentials are temp dirs, and
`imaplib.IMAP4_SSL` refuses in every test.
"""

from __future__ import annotations

import imaplib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import wicket.__main__ as root
from wicket import auth, env
from wicket.root import api as root_api
from wicket.root.lib import report as report_lib

ME = "you@gmail.com"
WORK = "work@corp.com"

Run = Callable[..., tuple[int, str, str]]
Stats = dict[str, object]

HALT_LINE = "throttle 2026-10-27T08:00:00Z month 2026-03: abort"


@pytest.fixture(name="mail")
def _mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A mail root with one store, its account folder and imap.json link, and credentials."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a pinned run opened an IMAP connection")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse)
    mail = tmp_path / "mail"
    (mail / ME).mkdir(parents=True)
    (tmp_path / "gmail" / ME).mkdir(parents=True)
    (tmp_path / "gmail" / ME / "imap.json").write_text(
        f'{{"email": "{ME}", "app_password": "pw"}}', encoding="utf-8"
    )
    (tmp_path / "wicket" / "accounts" / ME).mkdir(parents=True)
    (tmp_path / "wicket" / "accounts" / ME / "imap.json").symlink_to(
        tmp_path / "gmail" / ME / "imap.json"
    )
    monkeypatch.setattr(env, "MAIL_ROOT", mail)
    # Each api binds the mail root by name at import: point every binding at the temp one.
    monkeypatch.setattr(root_api, "MAIL_ROOT", mail)
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    return mail


def _creds_link(tmp_path: Path) -> Path:
    """The account's imap.json: a link in its folder, as a refusal names it."""
    return tmp_path / "wicket" / "accounts" / ME / "imap.json"


@pytest.fixture(name="run")
def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Run:
    """(exit code, stdout, stderr) of ``wicket <argv>``, run as the console script runs."""

    def run(*argv: str) -> tuple[int, str, str]:
        monkeypatch.setattr(sys, "argv", ["wicket", *argv])
        try:
            code = root.main()
        except SystemExit as exc:
            code = int(exc.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def _halt_text(path: Path) -> str:
    return (
        f"halted: {HALT_LINE}\n"
        f"HALT at {path}; clear it with `python -m wicket.halt clear --mail-account "
        f"{path.name}` (if the provider rejected the app password, re-seed it first)\n"
    )


def _busy_text(mail: Path, account: str = ME) -> str:
    return f"{env.StoreBusy(mail / account / 'manifest', str(os.getpid()))}\n"


def _raises(exc: BaseException) -> Callable[..., object]:
    def worker(*_args: object, **_kwargs: object) -> object:
        raise exc

    return worker


def _returns(stats: Stats) -> Callable[..., Stats]:
    return lambda *_args, **_kwargs: stats


# --- catalog ----------------------------------------------------------------------


def _sweep(**over: int) -> Stats:
    stats: Stats = {
        "messages": 12,
        "shards": 2,
        "removed": 0,
        "parse_skips": 0,
        "label_misses": 0,
        "boundary_drops": 0,
    }
    return {**stats, **over}


CATALOG = ["catalog", "--mail-account", ME, "--years", "2026", "--non-interactive"]


def _catalog(monkeypatch: pytest.MonkeyPatch, worker: Callable[..., object]) -> None:
    monkeypatch.setattr(root_api, "sweep", worker)


def test_catalog_done(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    _catalog(monkeypatch, _returns(_sweep()))
    assert run(*CATALOG) == (
        0,
        f"done: observed 12 message(s); wrote 2 year shard(s) under {mail / ME}"
        "/manifest\n",
        "",
    )


def test_catalog_dry_run(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    _catalog(monkeypatch, _returns(_sweep()))
    assert run(*CATALOG, "--dry-run") == (
        0,
        f"done: observed 12 message(s); would write 2 year shard(s) under {mail / ME}"
        "/manifest\n",
        "",
    )


def test_catalog_removed_and_boundary(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _catalog(monkeypatch, _returns(_sweep(removed=1, boundary_drops=3)))
    assert run(*CATALOG) == (
        0,
        f"done: observed 12 message(s); wrote 2 year shard(s) under {mail / ME}"
        "/manifest; removed 1 stale shard(s); 3 boundary overlap(s) deduped\n",
        "",
    )


def test_catalog_warnings(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _catalog(monkeypatch, _returns(_sweep(parse_skips=2, label_misses=1)))
    assert run(*CATALOG) == (
        0,
        f"done: observed 12 message(s); wrote 2 year shard(s) under {mail / ME}"
        "/manifest\n",
        "warning: 2 unparseable FETCH response(s)\n"
        "warning: 1 row(s) with labels unrecoverable (labels: null)\n",
    )


def test_catalog_not_linked(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    shutil.rmtree(tmp_path / "wicket" / "accounts" / ME)  # no folder: no account
    _catalog(monkeypatch, _raises(AssertionError("a no-op swept")))
    assert run(*CATALOG) == (0, "", "")


def test_catalog_halt(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    path = tmp_path / "xdg" / "wicket" / "halt" / ME
    _catalog(monkeypatch, _raises(auth.Halted(path, HALT_LINE)))
    assert run(*CATALOG) == (2, "", _halt_text(path))


def test_catalog_busy(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    _catalog(monkeypatch, _returns(_sweep()))
    with env.store_lock(mail / ME / "manifest"):
        assert run(*CATALOG) == (2, "", _busy_text(mail))


def test_catalog_auth(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    (tmp_path / "gmail" / ME / "imap.json").unlink()
    _catalog(monkeypatch, _raises(AssertionError("swept without credentials")))
    assert run(*CATALOG) == (
        2,
        "",
        f"auth error: No credentials at {_creds_link(tmp_path)} and "
        "running non-interactively. Run once in a terminal (without "
        "--non-interactive) to seed it.\n",
    )


def test_catalog_imap_error(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    _catalog(monkeypatch, _raises(imaplib.IMAP4.error("NO [ALERT] refused")))
    assert run(*CATALOG) == (1, "", "IMAP error: NO [ALERT] refused\n")


def test_catalog_bad_years(mail: Path, run: Run) -> None:
    del mail
    assert run("catalog", "--mail-account", ME, "--years", "x") == (
        2,
        "",
        "bad --years: 'x' is not a year (four digits, 1970 or later)\n",
    )


def test_catalog_refuses_a_year_that_is_not_four_digits(mail: Path, run: Run) -> None:
    del mail
    assert run("catalog", "--mail-account", ME, "--years", "202") == (
        2,
        "",
        "bad --years: '202' is not a year (four digits, 1970 or later)\n",
    )


# The driver a subprocess runs: the console script's entry, with the credentials and the
# sweep replaced, the sweep logging one progress line as the real one does.
DRIVER = """
import sys
import wicket.__main__ as root
from wicket.root import api
from wicket.root.lib import catalog as lib

def sweep(**_kwargs):
    lib.logger.info("[plan] 1 year(s) 2026..2026, 1 thread(s)")
    return {"messages": 1, "shards": 1, "removed": 0, "parse_skips": 0,
            "label_misses": 0, "boundary_drops": 0}

api.load_credentials = lambda *_a, **_k: ("you@gmail.com", "pw")
api.sweep = sweep
sys.argv = ["wicket", "catalog", "--mail-account", "you@gmail.com", "--years", "2026",
            "--non-interactive"]
raise SystemExit(root.main())
"""


def test_catalog_progress_goes_to_stderr(tmp_path: Path) -> None:
    """In a process of its own, as a user runs it: progress on stderr, the summary on stdout.

    Under pytest the root logger already has handlers, so `main()`'s logging setup is a
    no-op in-process; only a subprocess shows the stream a user sees.
    """
    home, mail = tmp_path / "home", tmp_path / "mail"
    (mail / ME).mkdir(parents=True)
    (tmp_path / "gmail" / ME).mkdir(parents=True)
    (home / ".config" / "wicket" / "accounts" / ME).mkdir(parents=True)
    (home / ".config" / "wicket" / "accounts" / ME / "imap.json").symlink_to(
        tmp_path / "gmail" / ME / "imap.json"
    )
    environ = {k: v for k, v in os.environ.items() if not k.startswith("WICKET_")}
    environ.update(
        HOME=str(home),
        WICKET_MAIL_ROOT=str(mail),
        XDG_STATE_HOME=str(tmp_path / "xdg"),
    )
    done = subprocess.run(
        [sys.executable, "-c", DRIVER],
        capture_output=True,
        text=True,
        env=environ,
        check=False,
        timeout=60,
    )
    assert (done.returncode, done.stdout, done.stderr) == (
        0,
        f"done: observed 1 message(s); wrote 1 year shard(s) under {mail / ME}"
        "/manifest\n",
        "[plan] 1 year(s) 2026..2026, 1 thread(s)\n",
    )


# --- fetch ------------------------------------------------------------------------


def _counts(**over: int) -> dict[str, int]:
    counts = {
        "matched": 5,
        "got": 2,
        "candidates": 3,
        "pending": 3,
        "linked": 0,
        "downloaded": 3,
        "failed": 0,
    }
    return {**counts, **over}


def _fetched(
    passes: list[Stats], counts: dict[str, int], *, halted: bool = False
) -> Stats:
    return {
        **counts,
        "passes": passes,
        "halted": halted,
        "halt": HALT_LINE if halted else None,
        "halt_file": str(Path("/state/wicket/halt") / ME) if halted else None,
    }


def _one_pass(**over: int) -> Stats:
    """What `download` returns: one filtered pass."""
    counts = _counts(**over)
    return _fetched([{"year": None, "pass": 1, **counts}], counts)


FETCH = ["fetch", "--mail-account", ME, "--query", "from:x", "--non-interactive"]
HINT = f"staged, not yet filed: wicket ingest --staged --mail-account {ME}\n"


def _fetch(
    monkeypatch: pytest.MonkeyPatch,
    worker: Callable[..., object],
    years: Callable[..., object] | None = None,
) -> None:
    monkeypatch.setattr(root_api, "download", worker)
    monkeypatch.setattr(
        root_api, "download_years", years or _raises(AssertionError("unfiltered"))
    )


def test_fetch_filtered(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    del mail
    _fetch(monkeypatch, _returns(_one_pass()))
    assert run(*FETCH) == (
        0,
        "done: 3 downloaded, 0 failed, 2 already got.\n" + HINT,
        "",
    )


def test_fetch_nothing_downloaded_has_no_hint(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    _fetch(monkeypatch, _returns(_one_pass(downloaded=0, failed=1)))
    assert run(*FETCH) == (0, "done: 0 downloaded, 1 failed, 2 already got.\n", "")


def test_fetch_unfiltered(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    passes: list[Stats] = [
        {"year": 2026, "pass": 1, **_counts(), "got_total": 4},
        {"year": 2026, "pass": 2, **_counts(downloaded=0), "got_total": 4},
        {"year": 2025, "pass": 1, **_counts(downloaded=0), "got_total": 4},
    ]
    _fetch(
        monkeypatch,
        _raises(AssertionError("filtered")),
        _returns(_fetched(passes, _counts())),
    )
    assert run("fetch", "--mail-account", ME, "--non-interactive") == (
        0,
        "done: 3 downloaded, 0 failed, over 3 pass(es) in 2 year(s).\n" + HINT,
        "",
    )


def test_fetch_dry_run(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    del mail
    _fetch(monkeypatch, _returns(_one_pass(downloaded=0)))
    assert run(*FETCH, "--dry-run") == (
        0,
        "done (dry-run): 3 to download, 2 already got. Nothing written.\n",
        "",
    )


def test_fetch_linked(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    del mail
    _fetch(monkeypatch, _returns(_one_pass(linked=2)))
    assert run(*FETCH) == (
        0,
        "done: 3 downloaded, 0 failed, 2 already got. 2 linked to an archived copy.\n"
        + HINT,
        "",
    )


def test_fetch_hint_without_an_account_named(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    _fetch(monkeypatch, _returns(_one_pass()))
    assert run("fetch", "--query", "from:x", "--non-interactive") == (
        0,
        "done: 3 downloaded, 0 failed, 2 already got.\n"
        "staged, not yet filed: wicket ingest --staged --mail-account <account>\n",
        "",
    )


def test_fetch_halted_after_work(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    halted = _fetched([{"year": None, "pass": 1, **_counts()}], _counts(), halted=True)
    _fetch(monkeypatch, _returns(halted))
    assert run(*FETCH) == (
        2,
        "",
        "done: 3 downloaded, 0 failed, 2 already got.\n"
        + HINT
        + _halt_text(Path("/state/wicket/halt") / ME),
    )


def test_fetch_halted_before_work(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    path = tmp_path / "xdg" / "wicket" / "halt" / ME
    _fetch(monkeypatch, _raises(auth.Halted(path, HALT_LINE)))
    assert run(*FETCH) == (2, "", _halt_text(path))


def test_fetch_busy(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    _fetch(monkeypatch, _returns(_one_pass()))
    with env.store_lock(mail / ME / "manifest"):
        assert run(*FETCH) == (2, "", _busy_text(mail))


def test_fetch_auth(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    (tmp_path / "gmail" / ME / "imap.json").unlink()
    _fetch(monkeypatch, _raises(AssertionError("fetched without credentials")))
    assert run(*FETCH) == (
        2,
        "",
        f"auth error: No credentials at {_creds_link(tmp_path)} and "
        "running non-interactively. Run once in a terminal (without "
        "--non-interactive) to seed it.\n",
    )


def test_fetch_imap_error(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    _fetch(monkeypatch, _raises(imaplib.IMAP4.error("NO [ALERT] refused")))
    assert run(*FETCH) == (1, "", "IMAP error: NO [ALERT] refused\n")


def test_fetch_target_is_refused(mail: Path, run: Run) -> None:
    del mail
    assert run("fetch", "--target", "out") == (2, "", f"{root_api.DEST_REFUSED}\n")


def test_fetch_empty_domains(mail: Path, run: Run) -> None:
    del mail
    assert run("fetch", "--domains", ",", "--non-interactive") == (
        2,
        "",
        "domains was empty\n",
    )


def test_fetch_not_linked(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    shutil.rmtree(tmp_path / "wicket" / "accounts" / ME)  # no folder: no account
    _fetch(monkeypatch, _raises(AssertionError("a no-op fetched")))
    assert run(*FETCH) == (0, "", "")


# --- ingest -----------------------------------------------------------------------


def _filed(account: str = ME, **over: object) -> Stats:
    """What one store's filing reports (`ingest.lib._filed_stats` and the counts after)."""
    stats: Stats = {
        "account": account,
        "added": 2,
        "unfiled": 0,
        "added_by_year": {2026: 2},
        "top_domains": [("acme.com", 2)],
        "by_rule": {"rule": 2},
        "filed": {"acme.com/2026-07": ["a1.eml", "a2.eml"]},
        "skipped": 0,
        "excluded": 0,
        "excluded_lists": 0,
    }
    return {**stats, **over}


def _routed(**over: object) -> Stats:
    """What `ingest_routed` returns."""
    stats: Stats = {
        "files": 3,
        "unique": 3,
        "dup": 0,
        "accounts": [_filed()],
        "added": 2,
        "skipped": 0,
        "excluded": 0,
        "excluded_lists": 0,
        "filed": {"acme.com/2026-07": ["a1.eml", "a2.eml"]},
        "unrouted": 0,
        "unrouted_threads": 0,
        "busy": [],
        "trashable": 2,
        "trashed": 2,
    }
    return {**stats, **over}


def _single(**over: object) -> Stats:
    """What `ingest_folder` returns."""
    stats: Stats = {
        **_filed(),
        "warning": None,
        "files": 3,
        "unique": 3,
        "dup": 0,
        "unrouted": 0,
        "trashable": 2,
        "trashed": 2,
    }
    return {**stats, **over}


def _staged(**over: object) -> Stats:
    """What `ingest_staged` returns."""
    stats: Stats = {
        **_filed(),
        "staged": 2,
        "archived": 0,
        "duplicates": 0,
        "linked": 0,
        "refused": [],
        "unrecognized": [],
    }
    del stats["skipped"]
    return {**stats, **over}


FILED = "    acme.com/2026-07/\n      a1.eml\n      a2.eml\n"


def _ingest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    routed: Callable[..., object] | None = None,
    single: Callable[..., object] | None = None,
    staged: Callable[..., object] | None = None,
) -> None:
    never = _raises(AssertionError("the wrong ingest mode ran"))
    monkeypatch.setattr(root_api, "ingest_routed", routed or never)
    monkeypatch.setattr(root_api, "ingest_folder", single or never)
    monkeypatch.setattr(root_api, "ingest_staged", staged or never)


def test_ingest_routed(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    _ingest(monkeypatch, routed=_returns(_routed()))
    assert run("ingest", "--src", str(tmp_path)) == (
        0,
        "read: 3 file(s), 3 unique, 0 in-folder dup(s)\n"
        f"  added 2 to {ME} (2026:2)\n" + FILED + "  trashed: 2 source file(s) -> "
        "~/.Trash\n",
        "",
    )


def test_ingest_routed_dry_run_left_alone_and_counts(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    stats = _routed(
        dup=1,
        accounts=[_filed(skipped=3, excluded=1, unfiled=1)],
        unrouted=2,
        unrouted_threads=1,
        trashable=4,
        trashed=0,
    )
    _ingest(monkeypatch, routed=_returns(stats))
    assert run("ingest", "--src", str(tmp_path), "--dry-run") == (
        0,
        "read: 3 file(s), 3 unique, 1 in-folder dup(s)\n"
        f"  would add 2 to {ME} (2026:2); 3 already archived; 1 excluded, skipped; "
        "1 unfiled\n" + FILED + "  left alone: 2 message(s) addressed to no known "
        "account (add an alias, or create the store, to claim them)\n"
        "  would trash: 4 source file(s) -> ~/.Trash\n",
        "",
    )


def test_ingest_routed_no_delete(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    _ingest(monkeypatch, routed=_returns(_routed(trashable=0, trashed=0)))
    assert run("ingest", "--src", str(tmp_path), "--no-delete") == (
        0,
        "read: 3 file(s), 3 unique, 0 in-folder dup(s)\n"
        f"  added 2 to {ME} (2026:2)\n" + FILED,
        "",
    )


def test_ingest_routed_busy(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    (mail / WORK).mkdir()
    busy = [{"account": WORK, "pid": "4242", "messages": 3}]
    _ingest(monkeypatch, routed=_returns(_routed(busy=busy)))
    assert run("ingest", "--src", str(tmp_path)) == (
        2,
        "",
        "read: 3 file(s), 3 unique, 0 in-folder dup(s)\n"
        f"  added 2 to {ME} (2026:2)\n" + FILED + "  trashed: 2 source file(s) -> "
        "~/.Trash\n"
        f"busy: {WORK} is locked by pid 4242; its 3 message(s) were left for the "
        "next run\n",
    )


def test_ingest_single_with_a_warning(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    warning = f"this folder is addressed to {WORK}, not {ME}"
    _ingest(monkeypatch, single=_returns(_single(warning=warning)))
    assert run("ingest", "--src", str(tmp_path), "--mail-account", ME, "--force") == (
        0,
        "read: 3 file(s), 3 unique, 0 in-folder dup(s)\n"
        f"  added 2 to {ME} (2026:2)\n" + FILED + "  trashed: 2 source file(s) -> "
        "~/.Trash\n",
        f"warning: {warning}\n",
    )


def test_ingest_single_busy(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    busy = env.StoreBusy(mail / ME / "manifest", "4242")
    _ingest(monkeypatch, single=_raises(busy))
    assert run("ingest", "--src", str(tmp_path), "--mail-account", ME) == (
        2,
        "",
        f"{busy}\n",
    )


def test_ingest_staged(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    del mail
    stats = _staged(
        archived=1, duplicates=1, excluded=1, unfiled=1, by_rule={"rule": 1, "self": 1}
    )
    _ingest(monkeypatch, staged=_returns(stats))
    assert run("ingest", "--staged", "--mail-account", ME) == (
        0,
        f"staged: 2 file(s); filed 2 to {ME} (2026:2)\n"
        "  by rule: rule 1, self 1\n"
        "  1 already archived (staged copy removed)\n"
        "  1 set aside as duplicates\n"
        "  1 excluded, skipped and left staged\n"
        "  1 unfiled\n" + FILED,
        "",
    )


def test_ingest_staged_refused_and_unrecognized(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    stats = _staged(
        added=0,
        added_by_year={},
        by_rule={},
        filed={},
        refused=["t1/m1@20260706T090004Z.eml"],
        unrecognized=["notes.txt", "t2/odd.eml"],
    )
    _ingest(monkeypatch, staged=_returns(stats))
    assert run("ingest", "--staged", "--mail-account", ME, "--dry-run") == (
        0,
        f"staged: 2 file(s); would file 0 to {ME}\n",
        "  1 refused (a different file is in the way; left staged):\n"
        "    t1/m1@20260706T090004Z.eml\n"
        "  2 not a staged name (left alone):\n"
        "    notes.txt\n"
        "    t2/odd.eml\n",
    )


def test_ingest_bad_domain(mail: Path, run: Run) -> None:
    del mail
    assert run("ingest", "--src", "docs", "--domain", "../etc") == (
        2,
        "",
        "invalid --domain '../etc': expected a bare domain like acme.com\n",
    )


def test_ingest_no_eml(
    mail: Path, tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    _ingest(monkeypatch, routed=_raises(FileNotFoundError(f"no .eml in {tmp_path}")))
    assert run("ingest", "--src", str(tmp_path)) == (2, "", f"no .eml in {tmp_path}\n")


# --- report -----------------------------------------------------------------------


def _report(monkeypatch: pytest.MonkeyPatch, name: str, value: object) -> None:
    monkeypatch.setattr(report_lib, name, lambda *_args, **_kwargs: value)


def test_report_summary(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    counts = {
        "messages": 3241,
        "downloaded": 2987,
        "downloaded_gone": 41,
        "downloaded_present": 2946,
        "observed_only": 254,
        "excluded": 7,
        "senders": 318,
        "addresses": 512,
    }
    _report(monkeypatch, "summary", counts)
    assert run("report", "--mail-account", ME) == (
        0,
        f"manifest: {mail / ME / 'manifest'}\n"
        "  messages               3241\n"
        "  downloaded             2987  (held locally)\n"
        "    gone from mailbox      41  (only copy)\n"
        "    still in mailbox     2946\n"
        "  observed only           254  (not held)\n"
        "  excluded                  7  (duplicates, excluded lists)\n"
        "  distinct senders        318\n"
        "  distinct addresses      512\n",
        "",
    )


def test_report_senders(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    del mail
    _report(monkeypatch, "sender_counts", [("a@acme.com", 3), ("b@globex.com", 1)])
    assert run("report", "--senders") == (
        0,
        "3\ta@acme.com\n1\tb@globex.com\n",
        "",
    )


def test_report_addresses(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    _report(monkeypatch, "all_addresses", ["a@acme.com", f"{ME}"])
    assert run("report", "--addresses", "--mail-account", ME) == (
        0,
        f"a@acme.com\n{ME}\n",
        "",
    )


def test_report_bucket(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    (mail / WORK).mkdir()
    rows = {
        ME: [
            ("air.example", {"downloaded": True}),
            ("air.example", {}),
            ("rail.example", {"downloaded": True}),
        ],
        WORK: [("air.example", {"downloaded": True})],
    }

    def bucket_rows(store_dir: Path, *_args: object) -> object:
        return rows[store_dir.parent.name]

    monkeypatch.setattr(report_lib, "bucket_rows", bucket_rows)
    assert run("report", "--bucket", "travel") == (
        0,
        f"{WORK}: 1 message(s), 1 held locally\n"
        "       1  air.example\n"
        f"{ME}: 3 message(s), 2 held locally\n"
        "       2  air.example\n"
        "       1  rail.example\n"
        "travel: 4 message(s) across 2 store(s)\n",
        "",
    )


def test_report_empty_bucket(
    mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    del mail
    _report(monkeypatch, "bucket_rows", [])
    assert run("report", "--bucket", "travel") == (
        0,
        "travel: no mail in any store\n",
        "",
    )


def test_report_by_year(mail: Path, run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    del mail
    _report(monkeypatch, "by_year", {2025: (12, 3), 2026: (100, 0)})
    assert run("report", "--by-year") == (
        0,
        "local/not local, by account and year\n"
        "account        2025   2026  total\n"
        f"{ME}  12/3  100/0  112/3\n"
        "total          12/3  100/0  112/3\n",
        "",
    )


def test_report_unknown_account(mail: Path, run: Run) -> None:
    assert run("report", "--mail-account", "nobody@example.com") == (
        2,
        "",
        f"unknown account 'nobody@example.com': it has no store under {mail} and is "
        "not listed in account-aliases.json. If it is an address you receive at, add "
        "it as an alias of the account that owns it; if it is genuinely a new "
        "mailbox, create its store directory first.\n",
    )
