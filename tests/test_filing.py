"""What ingest files, and what it skips (plan rule 14).

These tests drive ingest's production path (`ingest_folder`). A message the store has
observed keeps the store's thread id; a row the owner marked ``excluded`` is skipped,
counted, and its source kept. A row records no folder and no label: a message's one
file is ``messages/YYYY/MM/<stem>.eml``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import wicket.root.lib.ingest as ingest_lib
from wicket.manifest import Row, load_store, message_files, shard_path, write_shard
from wicket.root.lib.ingest import Target, ingest_folder

ME = "you@gmail.com"
WORK = "work@corp.com"


def _account_for(address: str) -> str | None:
    return address if address in (ME, WORK) else None


def _target(root: Path, account: str = ME) -> Target:
    return Target(
        store_dir=root / account / "manifest",
        messages_dir=root / account / "messages",
        account=account,
    )


def _eml(folder: Path, name: str, *, frm: str, to: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.eml").write_text(
        f"Message-ID: <{name}@test>\nFrom: {frm}\nTo: {to}\n"
        f"Date: Mon, 06 Jul 2026 09:00:00 +0000\nSubject: {name}\n\nbody\n",
        encoding="utf-8",
    )


def _filed(root: Path, *, account: str = ME) -> dict[str, Row]:
    """Ingest ``root/src`` into ``account`` and return the store's rows by id.

    Every downloaded row's file is on disk at its stem under ``messages/``.
    """
    ingest_folder(
        src=root / "src",
        target=_target(root, account),
        account_for=_account_for,
        force=True,
    )
    rows = dict(load_store(root / account / "manifest"))
    files = message_files(root / account / "messages")
    for row in rows.values():
        if row.get("downloaded"):
            assert str(row["stem"]) in files
    return rows


def test_a_message_the_store_observed_joins_its_thread(tmp_path: Path) -> None:
    """A local export has no provider thread id: the store's own one is kept."""
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
    assert rows["reply@test"]["thread_id"] == "1765"


def test_an_excluded_row_is_skipped_counted_and_kept_at_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _target(tmp_path)
    row: Row = {
        "id": "dup@test",
        "date": "2026-01-01T00:00:00+00:00",
        "msgid": "1a",
        "excluded": "owner deleted 2026-10-01",
    }
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
    assert not target.messages_dir.exists()  # not archived
    assert trashed == [[]]  # its source is not trashed
    assert load_store(target.store_dir)["dup@test"] == row
