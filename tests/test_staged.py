"""fetch only stages; ``ingest --staged`` files (plan rules 6 and 14, and the
duplicates home).

A staged body is ``<store>/staging/<thread_id>/<msgid>@<INTERNALDATE>.eml``. These
tests stage bodies by hand and drive ``ingest --staged`` through the worker, the
api and the face. No test opens IMAP.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

import wicket.__main__ as root
from wicket import env
from wicket.manifest import (
    Row,
    load_store,
    message_file,
    message_files,
    read_shard,
    shard_path,
    write_shard,
)
from wicket.message import local_msgid, message_date, message_stem
from wicket.provider import GMAIL, parse_staged
from wicket.root import api as root_api

ME = "you@gmail.com"
JUL = "20260706T090004Z"  # INTERNALDATE of every staged body below
Run = Callable[..., tuple[int, str, str]]


def _main(argv: list[str] | None) -> int:
    """The root CLI on ``argv``."""
    return root.main(argv)


@pytest.fixture(name="mail")
def _mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A mail root holding one store, ``you@gmail.com``; nothing else exists."""
    mail = tmp_path / "mail"
    (mail / ME).mkdir(parents=True)
    monkeypatch.setattr(env, "MAIL_ROOT", mail)
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    return mail


def _stage(  # pylint: disable=too-many-arguments
    mail: Path, thread: str, msgid: str, *, frm: str, to: str, mid: str
) -> Path:
    """Stage one body as fetch writes it: ``staging/<thread>/<msgid>@<INTERNALDATE>.eml``."""
    path = mail / ME / "staging" / thread / f"{msgid}@{JUL}.eml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"Message-ID: <{mid}>\nFrom: {frm}\nTo: {to}\n"
        "Date: Mon, 06 Jul 2026 09:00:00 +0000\nSubject: s\n\nbody\n",
        encoding="utf-8",
    )
    return path


def _ingest(dry_run: bool = False) -> dict[str, object]:
    options = root_api.IngestOptions(staged=True, dry_run=dry_run)
    return root_api.ingest(ME, options=options)


def _rows(mail: Path) -> dict[str, dict[str, object]]:
    return {k: dict(v) for k, v in load_store(mail / ME / "manifest").items()}


def _archived(mail: Path) -> list[str]:
    """Each file under ``messages/``, as its path there, sorted."""
    messages = mail / ME / "messages"
    return sorted(
        path.relative_to(messages).as_posix()
        for path in message_files(messages).values()
    )


def _at(raw: bytes) -> str:
    """Where a message dated July 2026 is filed under ``messages/``."""
    return f"2026/07/{message_stem(raw)}.eml"


def _held(mail: Path, raw: bytes, **row: object) -> str:
    """Seed a message the store holds: its file at ``messages/YYYY/MM/<stem>.eml``
    and its row (``stem``, ``downloaded``) in its year's shard. Returns the stem."""
    stem = message_stem(raw)
    when = datetime.fromisoformat(str(row["date"]))
    path = message_file(mail / ME / "messages", stem, message_date(raw, when))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    shard = shard_path(mail / ME / "manifest", when.year)
    rows = read_shard(shard) if shard.exists() else {}
    rows[str(row["id"])] = {**row, "downloaded": True, "stem": stem}
    write_shard(shard, rows)
    return stem


# --- rule 6: fetch stages, ingest --staged files -----------------------------


def test_staged_mail_is_filed_and_leaves_staging(mail: Path) -> None:
    raw = _stage(
        mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme"
    ).read_bytes()
    stats = _ingest()
    assert stats["added"] == 1
    assert _archived(mail) == [_at(raw)]
    row = _rows(mail)["a@acme"]
    assert (row["msgid"], row["thread_id"]) == ("18f2", "1765")
    assert row["date"] == "2026-07-06T09:00:04+00:00"  # the INTERNALDATE
    assert row["downloaded"] is True and row["stem"] == message_stem(raw)
    assert not list((mail / ME / "staging").rglob("*"))  # body and thread dir gone


def test_a_staged_message_settles_into_its_catalog_rows_shard(mail: Path) -> None:
    observed = {
        "id": "a@acme",
        "msgid": "18f2",
        "thread_id": "1765",
        "date": "2025-12-31T23:59:59+00:00",  # catalog's shard, a boundary
        "from": "billing@acme.com",
        "to": [ME],
        "labels": ["inbox"],
        "downloaded": False,
    }
    write_shard(shard_path(mail / ME / "manifest", 2025), {"a@acme": observed})
    _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    _ingest()
    row = read_shard(shard_path(mail / ME / "manifest", 2025))["a@acme"]
    assert row["downloaded"] is True
    assert row["labels"] == ["inbox"] and row["date"] == observed["date"]
    assert not shard_path(mail / ME / "manifest", 2026).exists()


# --- rule 14: an excluded row is skipped and counted --------------------------


def test_an_excluded_staged_message_is_skipped_counted_and_left(mail: Path) -> None:
    row: Row = {
        "id": "a@x",
        "date": "2026-01-01T00:00:00+00:00",
        "msgid": "a1",
        "excluded": "owner deleted 2026-10-01",
    }
    write_shard(shard_path(mail / ME / "manifest", 2026), {"a@x": row})
    staged = _stage(mail, "1", "a1", frm="billing@acme.com", to=ME, mid="a@x")
    stats = _ingest()
    assert (stats["added"], stats["excluded"]) == (0, 1)
    assert staged.exists() and _archived(mail) == []
    kept = _rows(mail)["a@x"]  # reconcile ran first; the owner's record stands
    assert kept["excluded"] == row["excluded"] and not kept["downloaded"]
    assert "stem" not in kept


# --- duplicates: one home, <mail root>/.duplicates/<store>/<path> -------------


def test_a_copy_archived_under_another_name_goes_to_duplicates(mail: Path) -> None:
    """The Gmail copy of a message ingest filed from a local export (gmail_key's link)."""
    staged = _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    body = staged.read_bytes()
    local = body + b"the local copy\n"  # the export's bytes: the same message
    stem = _held(
        mail,
        local,
        id="a@acme",
        msgid=local_msgid("a@acme"),
        thread_id="a@acme",
        date="2026-07-06T09:00:00+00:00",  # the Date header: ingest stores it
        **{"from": "billing@acme.com"},
    )
    stats = _ingest()
    assert (stats["duplicates"], stats["linked"], stats["added"]) == (1, 1, 0)
    dup = mail / ".duplicates" / ME / "2026-07" / "18f2.eml"
    assert dup.read_bytes() == body and not staged.exists()
    assert _archived(mail) == [f"2026/07/{stem}.eml"]  # untouched
    assert message_files(mail / ME / "messages")[stem].read_bytes() == local
    assert _rows(mail)["a@acme"]["msgid"] == "18f2"  # linked: fetch now holds it
    store = env.resolve_duplicates_dir(ME)
    assert store == mail / ".duplicates" / ME


def test_the_same_message_already_archived_drops_only_an_identical_copy(
    mail: Path,
) -> None:
    staged = _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    _held(
        mail,
        staged.read_bytes(),
        id="a@acme",
        msgid="18f2",
        thread_id="1765",
        date="2026-07-06T09:00:04+00:00",
    )
    other = _stage(mail, "1766", "18f3", frm="b@acme.com", to=ME, mid="b@acme")
    body = other.read_bytes()
    in_the_way = mail / ME / "messages" / _at(body)
    in_the_way.write_bytes(b"different bytes")  # a file no row names, at its stem
    stats = _ingest()
    assert stats["archived"] == 1 and not staged.exists()
    # A differing file in the way is never overwritten: the staged body is set
    # aside (not filed, not lost) and named as a copy whose Subject differs.
    assert in_the_way.read_bytes() == b"different bytes"
    aside = mail / ".duplicates" / ME / "2026-07" / "18f3.eml"
    assert aside.read_bytes() == body and not other.exists()
    assert stats["copies"] == 1 and len(stats["subject_mismatches"]) == 1  # type: ignore[arg-type]
    row = _rows(mail)["b@acme"]
    assert "stem" not in row and row["downloaded"] is False  # nothing settled onto it


# --- the split plan's write order, names and no-op ----------------------------


def test_a_row_written_before_its_body_is_repaired_offline(mail: Path) -> None:
    """A crash between the row and the body: the next `ingest --staged` files it."""
    row = {
        "id": "a@acme",
        "msgid": "18f2",
        "thread_id": "1765",
        "date": "2026-07-06T09:00:04+00:00",
        "from": "billing@acme.com",
        "to": [ME],
        "downloaded": True,  # written first; the body never moved
    }
    staged = _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    raw = staged.read_bytes()
    row["stem"] = message_stem(raw)
    write_shard(shard_path(mail / ME / "manifest", 2026), {"a@acme": row})
    stats = _ingest()
    assert stats["added"] == 1
    assert _archived(mail) == [_at(raw)]
    assert _rows(mail)["a@acme"]["downloaded"] is True


def test_a_file_that_is_not_a_staged_name_is_reported_and_left(mail: Path) -> None:
    staging = mail / ME / "staging"
    (staging / "notdigits").mkdir(parents=True)
    (staging / "notdigits" / "18f2@20260706T090004Z.eml").write_text("x")
    (staging / "1765").mkdir()
    (staging / "1765" / "evil.eml").write_text("x")
    stats = _ingest()
    assert stats["unrecognized"] == [
        "1765/evil.eml",
        "notdigits/18f2@20260706T090004Z.eml",
    ]
    assert stats["added"] == 0 and _archived(mail) == []


def test_an_empty_or_missing_staging_dir_is_a_no_op(mail: Path) -> None:
    stats = _ingest()
    assert (stats["staged"], stats["added"]) == (0, 0)
    assert not (mail / ME / "manifest").exists()  # no lock taken, nothing written


def test_a_dry_run_writes_nothing(mail: Path) -> None:
    staged = _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    stats = _ingest(dry_run=True)
    assert stats["added"] == 1 and staged.exists()
    assert not (mail / ME / "messages").exists()
    assert not (mail / ME / "manifest").exists()


def test_the_options_take_exactly_one_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        root_api.IngestOptions()
    with pytest.raises(ValueError, match="exactly one"):
        root_api.IngestOptions(src=Path("x"), staged=True)


def test_the_face_files_staged_mail_and_refuses_both_sources(
    mail: Path, run_cli: Run
) -> None:
    _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    code, out, _err = run_cli(_main, ["ingest", "--staged", "--mail-account", ME])
    assert code == 0 and "filed 1 to you@gmail.com" in out
    code, _out, err = run_cli(_main, ["ingest", "--staged", "--src", "x"])
    assert code == 2 and "not allowed with" in err


# --- fetch takes no destination ----------------------------------------------


@pytest.mark.usefixtures("mail")
def test_staged_paths_are_got_by_the_writer_and_read_by_the_reader() -> None:
    """One home for the staged name: what fetch writes, ingest parses back."""
    staged = parse_staged("1765/18f2@20260706T090004Z.eml")
    assert staged is not None and staged.provider is GMAIL
    assert (staged.thread_id, staged.msgid) == ("1765", "18f2")
    assert staged.internal_dt.isoformat() == "2026-07-06T09:00:04+00:00"
