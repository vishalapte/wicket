"""Where a message is filed, and the record of why (plan rules 8 and 14).

The rank lives in `wicket.filing`; these tests drive it through ingest's production
path (`ingest_folder`), so each asserts the folder a message lands in and the
``filed`` its row records: anchor, ``--domain`` (forced), self, sent, the computed
domain (rule), the fallback, ``_unfiled``. The opener is the thread's earliest row
across the store, not the earliest message in hand, and only an archived row
anchors. A row the owner marked ``excluded`` is skipped and counted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import wicket.root.lib.ingest as ingest_lib
from wicket.env import Identities
from wicket.manifest import Row, load_store, merge_catalog, shard_path, write_shard
from wicket.root.lib.ingest import Target, ingest_folder

ME = "you@gmail.com"
WORK = "work@corp.com"
BURNER = "burner@duck.com"


def _account_for(address: str) -> str | None:
    return address if address in (ME, WORK) else None


def _target(root: Path, account: str = ME, *, forced: str | None = None) -> Target:
    return Target(
        store_dir=root / account / "manifest",
        archive_dir=root / account / "archive",
        account=account,
        identities=Identities(frozenset({ME, WORK, BURNER}), frozenset()),
        domain_aliases={},
        filing_domain=forced,
    )


def _eml(  # pylint: disable=too-many-arguments
    folder: Path,
    name: str,
    *,
    frm: str,
    to: str,
    date: str = "Mon, 06 Jul 2026 09:00:00 +0000",
    reply_to: str = "",
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    refs = f"In-Reply-To: <{reply_to}@test>\n" if reply_to else ""
    (folder / f"{name}.eml").write_text(
        f"Message-ID: <{name}@test>\n{refs}From: {frm}\nTo: {to}\n"
        f"Date: {date}\nSubject: {name}\n\nbody\n",
        encoding="utf-8",
    )


def _filed(
    root: Path, *, account: str = ME, forced: str | None = None
) -> dict[str, object]:
    """Ingest ``root/src`` into ``account`` and return the store's rows by id."""
    ingest_folder(
        src=root / "src",
        target=_target(root, account, forced=forced),
        account_for=_account_for,
        force=True,
    )
    return dict(load_store(root / account / "manifest"))


def _folder_and_filed(rows: dict[str, object], mid: str) -> tuple[str, object]:
    row = rows[mid]
    assert isinstance(row, dict)
    return str(row["path"]).split("/", 1)[0], row.get("filed")


def test_a_note_to_self_files_under_self(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "note", frm=ME, to=f"{ME}, {BURNER}")
    assert _folder_and_filed(_filed(tmp_path), "note@test") == ("self", "self")


def test_a_message_with_no_recipient_from_you_files_under_self(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "draft", frm=ME, to="")
    assert _folder_and_filed(_filed(tmp_path), "draft@test") == ("self", "self")


def test_mail_to_two_counterparty_domains_files_under_sent(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "blast", frm=ME, to=f"a@acme.com, b@globex.com, {BURNER}")
    assert _folder_and_filed(_filed(tmp_path), "blast@test") == ("sent", "sent")


def test_the_sent_rule_holds_in_every_account(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "blast", frm=WORK, to="a@acme.com, b@globex.com")
    rows = _filed(tmp_path, account=WORK)
    assert _folder_and_filed(rows, "blast@test") == ("sent", "sent")


def test_your_own_addresses_do_not_count_as_a_second_domain(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "one", frm=ME, to=f"a@acme.com, {BURNER}, {WORK}")
    assert _folder_and_filed(_filed(tmp_path), "one@test") == ("acme.com", "rule")


def test_an_inbound_message_records_rule(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "bill", frm="billing@acme.com", to=ME)
    assert _folder_and_filed(_filed(tmp_path), "bill@test") == ("acme.com", "rule")


def test_a_forced_domain_records_forced_and_outranks_self(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "note", frm=ME, to=ME)
    rows = _filed(tmp_path, forced="vendor.com")
    assert _folder_and_filed(rows, "note@test") == ("vendor.com", "forced")


def test_no_sender_but_one_outside_recipient_records_fallback(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "odd", frm="", to=f"{ME}, x@acme.com")
    assert _folder_and_filed(_filed(tmp_path), "odd@test") == ("acme.com", "fallback")


def test_nothing_to_go_on_records_unfiled(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "odd", frm="", to=ME)
    assert _folder_and_filed(_filed(tmp_path), "odd@test") == ("_unfiled", "unfiled")


def test_an_anchored_reply_copies_its_anchors_filed(tmp_path: Path) -> None:
    _eml(tmp_path / "src", "blast", frm=ME, to="a@acme.com, b@globex.com")
    _filed(tmp_path)
    _eml(
        tmp_path / "src",
        "reply",
        frm="a@acme.com",
        to=ME,
        date="Tue, 07 Jul 2026 09:00:00 +0000",
        reply_to="blast",
    )
    rows = _filed(tmp_path)
    assert _folder_and_filed(rows, "reply@test") == ("sent", "sent")


def test_an_anchor_with_no_filed_gives_none(tmp_path: Path) -> None:
    """A row archived before `filed` existed anchors its thread and records nothing."""
    target = _target(tmp_path)
    (target.archive_dir / "acme.com" / "2026-07").mkdir(parents=True)
    (target.archive_dir / "acme.com" / "2026-07" / "aa.eml").write_bytes(b"x")
    old = {
        "id": "root@test",
        "msgid": "aa",
        "thread_id": "root@test",
        "date": "2026-07-05T09:00:00+00:00",
        "from": "billing@acme.com",
        "to": [ME],
        "downloaded": True,
        "path": "acme.com/2026-07/aa.eml",
    }
    write_shard(shard_path(target.store_dir, 2026), {"root@test": old})
    _eml(tmp_path / "src", "root", frm="billing@acme.com", to=ME)
    _eml(tmp_path / "src", "reply", frm=ME, to="x@other.com", reply_to="root")
    rows = _filed(tmp_path)
    assert _folder_and_filed(rows, "reply@test") == ("acme.com", None)


def test_the_opener_is_the_earliest_row_the_store_holds(tmp_path: Path) -> None:
    """A thread you opened to two domains is sent mail, whichever reply is filed first."""
    target = _target(tmp_path)
    observed = {
        "open@test": {
            "id": "open@test",
            "msgid": "a1",
            "thread_id": "1765",
            "date": "2026-07-01T09:00:00+00:00",
            "from": ME,
            "to": ["a@acme.com", "b@globex.com"],
            "downloaded": False,
        },
        "reply@test": {
            "id": "reply@test",
            "msgid": "a2",
            "thread_id": "1765",
            "date": "2026-07-06T09:00:00+00:00",
            "from": "a@acme.com",
            "to": [ME],
            "downloaded": False,
        },
    }
    write_shard(shard_path(target.store_dir, 2026), observed)
    _eml(tmp_path / "src", "reply", frm="a@acme.com", to=ME)
    rows = _filed(tmp_path)
    assert _folder_and_filed(rows, "reply@test") == ("sent", "sent")
    assert rows["reply@test"]["thread_id"] == "1765"  # type: ignore[index]


def test_an_observed_rows_derived_domain_does_not_steer_filing(tmp_path: Path) -> None:
    """Only an archived row anchors (split plan rule 3)."""
    target = _target(tmp_path)
    row = {
        "id": "bill@test",
        "msgid": "a1",
        "thread_id": "1765",
        "date": "2026-07-06T09:00:04+00:00",
        "from": "billing@acme.com",
        "to": [ME],
        "downloaded": False,
        "domain": "wrong.com",
    }
    write_shard(shard_path(target.store_dir, 2026), {"bill@test": row})
    _eml(tmp_path / "src", "bill", frm="billing@acme.com", to=ME)
    assert _folder_and_filed(_filed(tmp_path), "bill@test") == ("acme.com", "rule")


def test_filed_survives_a_complete_catalog(tmp_path: Path) -> None:
    del tmp_path
    prior = {
        "x": {
            "id": "x",
            "downloaded": True,
            "path": "sent/2026-07/1.eml",
            "filed": "sent",
        }
    }
    merged = merge_catalog(prior, {"x": {"id": "x", "from": ME}}, complete=True)
    assert merged["x"]["filed"] == "sent"


def test_an_excluded_row_is_skipped_counted_and_kept_at_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target(tmp_path)
    row: Row = {"id": "dup@test", "msgid": "1a", "excluded": "owner deleted 2026-10-01"}
    write_shard(shard_path(target.store_dir, 2026), {"dup@test": row})
    _eml(tmp_path / "src", "dup", frm="news@spam.com", to=ME)
    trashed: list[list[Path]] = []

    def trash(paths: list[Path]) -> int:
        trashed.append(paths)
        return 0

    monkeypatch.setattr(ingest_lib, "trash", trash)
    stats = ingest_lib.ingest_folder(
        src=tmp_path / "src", target=target, account_for=_account_for, to_trash=True
    )
    assert (stats["added"], stats["excluded"]) == (0, 1)
    assert not target.archive_dir.exists()  # not archived
    assert trashed == [[]]  # its source is not trashed
    assert load_store(target.store_dir)["dup@test"] == row
