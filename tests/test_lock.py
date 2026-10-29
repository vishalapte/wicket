"""One writer per store (plan rule 9).

`env.store_lock` holds ``<manifest dir>/.lock`` with a non-blocking ``flock``; a held
lock is `env.StoreBusy` naming the holder's pid, which a face reports as a refusal, exit 2. No test
here reaches the network: `imaplib.IMAP4_SSL` refuses in every test.
"""

from __future__ import annotations

import imaplib
import os
from pathlib import Path

import pytest

import wicket.__main__ as root
from wicket import env
from wicket.root import api as root_api
from wicket.root.lib.ingest import Target, ingest_folder, ingest_routed

ME = "you@gmail.com"
WORK = "work@corp.com"


@pytest.fixture(autouse=True)
def _offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("no network in tests")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse)


@pytest.fixture(name="mail")
def _mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A mail root with two stores, and credentials for the first."""
    mail = tmp_path / "mail"
    for account in (ME, WORK):
        (mail / account / "manifest").mkdir(parents=True)
    monkeypatch.setattr(env, "MAIL_ROOT", mail)
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    creds = tmp_path / "gmail" / ME / "imap.json"
    creds.parent.mkdir(parents=True)
    creds.write_text(f'{{"email": "{ME}", "app_password": "pw"}}')
    (tmp_path / "wicket" / "accounts" / ME).mkdir(parents=True)
    (tmp_path / "wicket" / "accounts" / ME / "imap.json").symlink_to(creds)
    return mail


def test_a_second_take_in_one_process_is_busy_and_names_the_pid(tmp_path: Path) -> None:
    manifest = tmp_path / ME / "manifest"
    with env.store_lock(manifest):
        assert (manifest / ".lock").read_text().strip() == str(os.getpid())
        with pytest.raises(env.StoreBusy) as busy:
            with env.store_lock(manifest):
                pass  # pragma: no cover - never entered
        assert busy.value.pid == str(os.getpid())
        assert str(os.getpid()) in str(busy.value)
    with env.store_lock(manifest):  # released on exit: the next take succeeds
        pass


def _fetch(argv: list[str]) -> int:
    return root.main(["fetch", *argv])


def test_fetch_on_a_locked_store_exits_2(
    mail: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with env.store_lock(mail / ME / "manifest"):
        code = _fetch(["--mail-account", ME, "--query", "x", "--non-interactive"])
    assert code == 2
    assert f"pid {os.getpid()}" in capsys.readouterr().err


def test_a_dry_run_fetch_takes_no_lock(
    mail: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """It writes nothing, so a busy store does not stop it (it fails offline here)."""
    with env.store_lock(mail / ME / "manifest"):
        code = _fetch(
            ["--mail-account", ME, "--query", "x", "--dry-run", "--non-interactive"]
        )
    assert code == 2 and "could not reach" in capsys.readouterr().err


def test_catalog_on_a_locked_store_is_busy(mail: Path) -> None:
    with env.store_lock(mail / ME / "manifest"):
        with pytest.raises(env.StoreBusy):
            root_api.catalog(ME, options=root_api.CatalogOptions(years=[2026]))


def test_catalog_locks_the_store_dir_it_writes(mail: Path, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere" / "manifest"
    with env.store_lock(other):
        with pytest.raises(env.StoreBusy):
            root_api.catalog(
                ME,
                options=root_api.CatalogOptions(store_dir=other, years=[2026]),
            )


def _eml(folder: Path, name: str, *, frm: str, to: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.eml").write_text(
        f"Message-ID: <{name}@test>\nFrom: {frm}\nTo: {to}\n"
        f"Date: Mon, 06 Jul 2026 09:00:00 +0000\nSubject: {name}\n\nbody\n",
        encoding="utf-8",
    )


def _target(mail: Path, account: str) -> Target:
    return Target(
        store_dir=mail / account / "manifest",
        archive_dir=mail / account / "archive",
        account=account,
        identities=env.Identities(frozenset({ME, WORK}), frozenset()),
        domain_aliases={},
    )


def _owner(address: str) -> str | None:
    return address if address in (ME, WORK) else None


def test_single_target_ingest_refuses_a_locked_store(
    mail: Path, tmp_path: Path
) -> None:
    _eml(tmp_path / "src", "a", frm="them@acme.com", to=ME)
    with env.store_lock(mail / ME / "manifest"):
        with pytest.raises(env.StoreBusy):
            ingest_folder(
                src=tmp_path / "src", target=_target(mail, ME), account_for=_owner
            )
    assert not (mail / ME / "archive").exists()  # nothing was filed


def test_routed_ingest_skips_and_names_a_busy_store_and_files_the_rest(
    mail: Path, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)
    _eml(src, "b", frm="them@acme.com", to=WORK)
    with env.store_lock(mail / WORK / "manifest"):
        stats = ingest_routed(
            src=src,
            targets={a: _target(mail, a) for a in (ME, WORK)},
            account_for=_owner,
        )
    assert [a["account"] for a in stats["accounts"]] == [ME]  # type: ignore[attr-defined]
    assert stats["busy"] == [{"account": WORK, "pid": str(os.getpid()), "messages": 1}]
    assert len(list((mail / ME / "archive").rglob("*.eml"))) == 1
    assert not (mail / WORK / "archive").exists()


def test_the_ingest_face_names_a_busy_store_and_exits_2(
    mail: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _eml(tmp_path / "src", "b", frm="them@acme.com", to=WORK)
    argv = ["ingest", "--src", str(tmp_path / "src"), "--no-delete"]
    with env.store_lock(mail / WORK / "manifest"):
        code = root.main(argv)
    assert code == 2
    err = capsys.readouterr().err
    assert f"busy: {WORK} is locked by pid {os.getpid()}" in err
