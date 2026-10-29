"""fetch only stages; ``ingest --staged`` files (plan rules 6, 7 and 14, and the
duplicates home).

A staged body is ``<store>/staging/<thread_id>/<msgid>@<INTERNALDATE>.eml``. These
tests stage bodies by hand and drive ``ingest --staged`` through the worker, the
api and the face. No test opens IMAP.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import wicket.__main__ as root
from wicket import env, manifest
from wicket.manifest import Row, load_store, read_shard, shard_path, write_shard
from wicket.message import local_msgid
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


def _ingest(domain: str | None = None, dry_run: bool = False) -> dict[str, object]:
    options = root_api.IngestOptions(staged=True, domain=domain, dry_run=dry_run)
    return root_api.ingest(ME, options=options)


def _rows(mail: Path) -> dict[str, dict[str, object]]:
    return {k: dict(v) for k, v in load_store(mail / ME / "manifest").items()}


def _archived(mail: Path) -> list[str]:
    archive = mail / ME / "archive"
    return sorted(p.relative_to(archive).as_posix() for p in archive.rglob("*.eml"))


# --- rule 6: fetch stages, ingest --staged files -----------------------------


def test_staged_mail_is_filed_by_the_rank_and_leaves_staging(mail: Path) -> None:
    _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    stats = _ingest()
    assert stats["added"] == 1 and stats["by_rule"] == {"rule": 1}
    assert _archived(mail) == ["acme.com/2026-07/18f2.eml"]
    row = _rows(mail)["a@acme"]
    assert (row["msgid"], row["thread_id"], row["filed"]) == ("18f2", "1765", "rule")
    assert row["date"] == "2026-07-06T09:00:04+00:00"  # the INTERNALDATE
    assert row["downloaded"] is True and row["path"] == "acme.com/2026-07/18f2.eml"
    assert not list((mail / ME / "staging").rglob("*"))  # body and thread dir gone


def test_a_staged_note_to_self_and_a_blast_file_under_self_and_sent(mail: Path) -> None:
    _stage(mail, "1", "a1", frm=ME, to=ME, mid="note@x")
    _stage(mail, "2", "a2", frm=ME, to="a@acme.com, b@globex.com", mid="blast@x")
    stats = _ingest()
    assert stats["by_rule"] == {"self": 1, "sent": 1}
    assert _archived(mail) == ["self/2026-07/a1.eml", "sent/2026-07/a2.eml"]


def test_an_alias_address_is_you_for_the_self_rule(
    mail: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aliases = mail / "account-aliases.json"
    aliases.write_text('{"you@gmail.com": ["me@burner.example"]}')
    monkeypatch.setenv(env.ACCOUNT_ALIASES_FILE_ENV_VAR, str(aliases))
    _stage(mail, "1", "a1", frm="me@burner.example", to=ME, mid="note@x")
    _ingest()
    assert _archived(mail) == ["self/2026-07/a1.eml"]


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
    assert row["downloaded"] is True and row["filed"] == "rule"
    assert row["labels"] == ["inbox"] and row["date"] == observed["date"]
    assert not shard_path(mail / ME / "manifest", 2026).exists()


def test_a_staged_reply_joins_its_threads_archived_folder(mail: Path) -> None:
    archive = mail / ME / "archive"
    (archive / "sent" / "2026-07").mkdir(parents=True)
    (archive / "sent" / "2026-07" / "10.eml").write_bytes(b"x")
    opener = {
        "id": "open@x",
        "msgid": "10",
        "thread_id": "1765",
        "date": "2026-07-01T09:00:00+00:00",
        "from": ME,
        "to": ["a@acme.com", "b@globex.com"],
        "downloaded": True,
        "path": "sent/2026-07/10.eml",
        "filed": "sent",
    }
    write_shard(shard_path(mail / ME / "manifest", 2026), {"open@x": opener})
    _stage(mail, "1765", "18f2", frm="a@acme.com", to=ME, mid="reply@acme")
    _ingest(domain="vendor.com")  # an anchor outranks --domain
    assert _rows(mail)["reply@acme"]["path"] == "sent/2026-07/18f2.eml"
    assert _rows(mail)["reply@acme"]["filed"] == "sent"  # copied from the anchor


# --- rule 7: --domain files every unanchored staged message ------------------


def test_staged_domain_files_every_unanchored_message(mail: Path) -> None:
    _stage(mail, "1", "a1", frm="billing@acme.com", to=ME, mid="a@x")
    _stage(mail, "2", "a2", frm=ME, to=ME, mid="b@x")
    stats = _ingest(domain="vendor.com")
    assert stats["by_rule"] == {"forced": 2}
    assert _archived(mail) == ["vendor.com/2026-07/a1.eml", "vendor.com/2026-07/a2.eml"]


# --- rule 14: an excluded row is skipped and counted --------------------------


def test_an_excluded_staged_message_is_skipped_counted_and_left(mail: Path) -> None:
    row: Row = {"id": "a@x", "msgid": "a1", "excluded": "owner deleted 2026-10-01"}
    write_shard(shard_path(mail / ME / "manifest", 2026), {"a@x": row})
    staged = _stage(mail, "1", "a1", frm="billing@acme.com", to=ME, mid="a@x")
    stats = _ingest()
    assert (stats["added"], stats["excluded"]) == (0, 1)
    assert staged.exists() and _archived(mail) == []
    kept = _rows(mail)["a@x"]  # reconcile ran first; the owner's record stands
    assert kept["excluded"] == row["excluded"] and not kept["downloaded"]
    assert "path" not in kept and "filed" not in kept


# --- duplicates: one home, <mail root>/.duplicates/<store>/<path> -------------


def test_a_copy_archived_under_another_name_goes_to_duplicates(mail: Path) -> None:
    """The Gmail copy of a message ingest filed from a local export (gmail_key's link)."""
    archive = mail / ME / "archive"
    local = f"acme.com/2026-07/{local_msgid('a@acme')}.eml"
    (archive / local).parent.mkdir(parents=True)
    (archive / local).write_bytes(b"the local copy")
    filed = {
        "id": "a@acme",
        "msgid": local_msgid("a@acme"),
        "thread_id": "a@acme",
        "date": "2026-07-06T09:00:00+00:00",  # the Date header: ingest stores it
        "from": "billing@acme.com",
        "downloaded": True,
        "path": local,
    }
    write_shard(shard_path(mail / ME / "manifest", 2026), {"a@acme": filed})
    staged = _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    body = staged.read_bytes()
    stats = _ingest()
    assert (stats["duplicates"], stats["linked"], stats["added"]) == (1, 1, 0)
    dup = mail / ".duplicates" / ME / "acme.com" / "2026-07" / "18f2.eml"
    assert dup.read_bytes() == body and not staged.exists()
    assert _archived(mail) == [local]  # the archive is untouched
    assert _rows(mail)["a@acme"]["msgid"] == "18f2"  # linked: fetch now holds it
    store = env.resolve_duplicates_dir(ME)
    assert store == mail / ".duplicates" / ME


def test_the_same_message_already_archived_drops_only_an_identical_copy(
    mail: Path,
) -> None:
    archive = mail / ME / "archive"
    staged = _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    (archive / "acme.com" / "2026-07").mkdir(parents=True)
    (archive / "acme.com" / "2026-07" / "18f2.eml").write_bytes(staged.read_bytes())
    row = {
        "id": "a@acme",
        "msgid": "18f2",
        "thread_id": "1765",
        "date": "2026-07-06T09:00:04+00:00",
        "downloaded": True,
        "path": "acme.com/2026-07/18f2.eml",
    }
    write_shard(shard_path(mail / ME / "manifest", 2026), {"a@acme": row})
    other = _stage(mail, "1766", "18f3", frm="b@acme.com", to=ME, mid="b@acme")
    (archive / "acme.com" / "2026-07" / "18f3.eml").write_bytes(b"different bytes")
    stats = _ingest()
    assert stats["archived"] == 1 and not staged.exists()
    assert stats["refused"] == [other.relative_to(mail / ME / "staging").as_posix()]
    assert other.exists()  # a differing file in the way: refused and kept
    assert "b@acme" not in _rows(mail)  # nothing settled onto the other file


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
        "path": "acme.com/2026-07/18f2.eml",
        "filed": "rule",
    }
    write_shard(shard_path(mail / ME / "manifest", 2026), {"a@acme": row})
    _stage(mail, "1765", "18f2", frm="billing@acme.com", to=ME, mid="a@acme")
    stats = _ingest()
    assert stats["added"] == 1
    assert _archived(mail) == ["acme.com/2026-07/18f2.eml"]
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
    assert not (mail / ME / "archive").exists()
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
    assert code == 0 and "filed 1 to you@gmail.com" in out and "rule 1" in out
    code, _out, err = run_cli(_main, ["ingest", "--staged", "--src", "x"])
    assert code == 2 and "not allowed with" in err


# --- fetch takes no destination ----------------------------------------------


@pytest.mark.usefixtures("mail")
def test_fetch_refuses_a_destination_and_names_the_split(run_cli: Run) -> None:
    with pytest.raises(ValueError, match="ingest --staged"):
        root_api.FetchOptions(dest=Path("/somewhere"))
    code, _out, err = run_cli(_main, ["fetch", "--target", "/x", "--mail-account", ME])
    assert code == 2 and "ingest --staged" in err


def test_staged_paths_are_got_by_the_writer_and_read_by_the_reader() -> None:
    """One home for the staged name: what fetch writes, ingest parses back."""
    staged = manifest.parse_staged("1765/18f2@20260706T090004Z.eml")
    assert staged is not None and staged[:2] == ("1765", "18f2")
    assert staged[2].isoformat() == "2026-07-06T09:00:04+00:00"
