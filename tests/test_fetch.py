"""Tests for fetch planning, links and staging (offline; the IMAP paths are excluded)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

import wicket.root.lib.fetch as fetch_lib
from wicket.filing import build_domain_query
from wicket.manifest import Row, message_file, read_shard, shard_path, write_shard
from wicket.message import message_stem
from wicket.provider import GMAIL
from wicket.root.lib.fetch import (
    Record,
    ThreadContext,
    _held,
    _plan,
    _write_links,
    download,
)

ME = "you@gmail.com"


def test_build_domain_query_expands_and_orders() -> None:
    assert build_domain_query(["acme.com"]) == "{from:acme.com to:acme.com}"


def _rec(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    uid: bytes,
    msgid: str,
    thr: str,
    dt: datetime,
    mid: str,
    sent: datetime | None = None,
) -> Record:
    """A Gmail discovery record, in All Mail."""
    return Record(uid, msgid, thr, dt, mid, sent, GMAIL.all_mail or "")


def _context(dest: Path, root: Path, *, dry_run: bool) -> ThreadContext:
    """A run whose manifest, staging and duplicates dirs sit under ``root``."""
    return ThreadContext(
        dest=dest,
        dry_run=dry_run,
        store_dir=root / "manifest",
        staging_dir=root / "staging",
        duplicates_dir=root / ".duplicates",
    )


def test_plan_stages_every_message_and_computes_no_domain(tmp_path: Path) -> None:
    """Plan rule 6: fetch files nothing, so a thread no rule resolves is still got."""
    ctx = _context(tmp_path, tmp_path, dry_run=False)
    dt = datetime(2024, 3, 1, 9, 30, tzinfo=timezone.utc)
    records = [
        _rec(b"1", "18f2", "77", dt, "<a@acme>"),
        # fetch used to drop a thread sent to two domains; it now reads no address
        _rec(b"2", "19a0", "78", dt, "<b@x>"),
    ]
    pending, _linked = _plan(records, ctx, _held(ctx.store_dir)[1])
    assert [e["path"] for e in pending] == [
        "77/18f2@20240301T093000Z.eml",
        "78/19a0@20240301T093000Z.eml",
    ]
    assert all("domain" not in e for e in pending)
    assert pending[0]["id"] == "a@acme"


def test_download_stages_the_body_and_writes_no_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real pass: the body lands in staging/, messages/ and the manifest stay as they were."""
    dt = datetime(2024, 3, 1, 9, 30, tzinfo=timezone.utc)
    rec = _rec(b"1", "18f2", "77", dt, "<a@acme>", dt)

    def discover(*_args: object, **_kw: object) -> tuple[list[object], int, int]:
        return [rec], 1, 1

    def retrieve(**kw: object) -> tuple[list[Row], int, None]:
        months: dict[str, list[Row]] = kw["by_month"]  # type: ignore[assignment]
        entries = [e for batch in months.values() for e in batch]
        for e in entries:
            target = tmp_path / "staging" / str(e["path"])
            target.parent.mkdir(parents=True)
            target.write_bytes(b"Message-ID: <a@acme>\r\n\r\nbody")
        return entries, 0, None

    monkeypatch.setattr(fetch_lib, "_discover_records", discover)
    monkeypatch.setattr(fetch_lib, "_retrieve_months", retrieve)
    store = tmp_path / "manifest"
    ctx = _context(tmp_path / "messages", tmp_path, dry_run=False)
    stats = download(
        search=GMAIL.raw_search("q"),
        ctx=ctx,
        credentials_path=tmp_path / "c",
        limit=None,
    )
    assert stats["downloaded"] == 1
    assert (tmp_path / "staging" / "77" / "18f2@20240301T093000Z.eml").exists()
    assert not list((tmp_path / "messages").rglob("*.eml"))
    assert not list(store.glob("*.jsonl"))  # settles nothing


def test_got_is_staged_filed_excluded_and_duplicate(tmp_path: Path) -> None:
    """fetch's dedup reads staging/ and .duplicates/<store>/ as well as the rows."""
    store, staging, dups = tmp_path / "manifest", tmp_path / "staging", tmp_path / "dup"
    write_shard(
        shard_path(store, 2024),
        {
            "a": {
                "id": "a",
                "date": "2024-01-01T00:00:00+00:00",
                "msgid": "a1",
                "downloaded": True,
            },
            "b": {
                "id": "b",
                "date": "2024-01-01T00:00:00+00:00",
                "msgid": "b2",
                "excluded": "duplicate of a",
            },
            "c": {
                "id": "c",
                "date": "2024-01-01T00:00:00+00:00",
                "msgid": "c3",
                "downloaded": False,
            },
        },
    )
    (staging / "77").mkdir(parents=True)
    (staging / "77" / "d4@20240301T093000Z.eml").write_bytes(b"x")
    (staging / "77" / "not-a-staged-name.eml").write_bytes(b"x")
    (dups / "acme.com" / "2024-03").mkdir(parents=True)
    (dups / "acme.com" / "2024-03" / "e5.eml").write_bytes(b"x")
    ctx = ThreadContext(
        dest=tmp_path / "messages",
        dry_run=True,
        store_dir=store,
        staging_dir=staging,
        duplicates_dir=dups,
    )
    got = fetch_lib._got(ctx)[0]
    assert len(got) == 4 and all(m in got for m in ("a1", "b2", "d4", "e5"))
    assert fetch_lib._got_count(ctx) == 4


def test_a_real_run_needs_a_staging_dir(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="staging_dir"):
        ThreadContext(  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter
            dest=tmp_path,
            dry_run=False,
            store_dir=tmp_path / "manifest",
            duplicates_dir=tmp_path / ".duplicates",
        )


# --- Linking a copy ingest already filed ---------------------------------

SENT = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)
INGESTED_RAW = b"Message-ID: <a@acme>\r\n\r\nbody"
INGESTED = message_stem(INGESTED_RAW)


def _file_at(messages: Path, stem: str, when: datetime, raw: bytes = b"x") -> Path:
    """Write a held message's one file at ``messages/YYYY/MM/<stem>.eml``."""
    path = message_file(messages, stem, when)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def _ingested_store(tmp_path: Path, *, date: datetime = SENT) -> tuple[Path, Path]:
    """A messages/ + store holding one message as ingest files it (by its stem)."""
    dest, store = tmp_path / "messages", tmp_path / "manifest"
    _file_at(dest, INGESTED, date, INGESTED_RAW)
    row: Row = {
        "id": "a@acme",
        "msgid": "068d2a33a1af5856",
        "thread_id": "local",
        "date": date.isoformat(),
        "from": "billing@acme.com",
        "downloaded": True,
        "stem": INGESTED,
    }
    write_shard(shard_path(store, 2024), {"a@acme": row})
    return dest, store


def _gmail_copy(
    dest: Path, store: Path, sent: datetime | None = SENT
) -> tuple[list[Row], list[Row]]:
    """The same message as fetch plans it from Gmail (INTERNALDATE a few s later):
    ``(to_download, linked)``."""
    ctx = _context(dest, store.parent, dry_run=True)
    internal = datetime(2024, 3, 1, 9, 0, 4, tzinfo=timezone.utc)
    rec = _rec(
        b"1",
        "1a0f296bf69d52b0",
        "t1",
        internal,
        "<a@acme>",
        sent,
    )
    return _plan([rec], ctx, _held(store)[1])


def test_held_maps_rows_by_id_with_shard_year(tmp_path: Path) -> None:
    _dest, store = _ingested_store(tmp_path)
    msgids, by_id = _held(store)
    assert msgids == {"068d2a33a1af5856"}
    assert set(by_id) == {"a@acme"}
    assert by_id["a@acme"][1] == 2024


def test_split_links_the_ingested_copy(tmp_path: Path) -> None:
    dest, store = _ingested_store(tmp_path)
    to_download, linked = _gmail_copy(dest, store)
    assert not to_download
    assert [e["msgid"] for e in linked] == ["1a0f296bf69d52b0"]
    assert linked[0]["id"] == "a@acme" and linked[0]["shard_year"] == 2024


@pytest.mark.parametrize(
    "case",
    ["reused message-id (different send date)", "no parseable Date", "file gone"],
)
def test_split_downloads_when_not_the_same_send(tmp_path: Path, case: str) -> None:
    other = datetime(2024, 3, 1, 9, 7, tzinfo=timezone.utc)
    dest, store = _ingested_store(tmp_path)
    sent: datetime | None = SENT
    if case.startswith("reused"):
        sent = other
    elif case.startswith("no parseable"):
        sent = None
    else:
        message_file(dest, INGESTED, SENT).unlink()
    to_download, linked = _gmail_copy(dest, store, sent)
    assert not linked and len(to_download) == 1
    assert to_download[0]["id"] == "a@acme|gmail:1a0f296bf69d52b0"  # its own row


def test_write_links_records_only_the_gmail_msgid(tmp_path: Path) -> None:
    dest, store = _ingested_store(tmp_path)
    _, linked = _gmail_copy(dest, store)
    _write_links(store, linked)
    row = read_shard(shard_path(store, 2024))["a@acme"]
    assert row["msgid"] == "1a0f296bf69d52b0"
    assert row["stem"] == INGESTED and row["thread_id"] == "local"
    assert row["date"] == SENT.isoformat() and row["downloaded"] is True
    assert "shard_year" not in row and "sent" not in row


def test_download_links_instead_of_duplicating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported duplicate, end to end with only IMAP discovery stubbed."""
    dest, store = _ingested_store(tmp_path)
    internal = datetime(2024, 3, 1, 9, 0, 4, tzinfo=timezone.utc)
    rec = _rec(
        b"1",
        "1a0f296bf69d52b0",
        "t1",
        internal,
        "<a@acme>",
        SENT,
    )

    def discover(*_args: object, **_kw: object) -> tuple[list[object], int, int]:
        return [rec], 1, 1

    def no_retrieve(**_kwargs: object) -> None:
        raise AssertionError("a linked message must not be downloaded")

    monkeypatch.setattr(fetch_lib, "_discover_records", discover)
    monkeypatch.setattr(fetch_lib, "_retrieve_months", no_retrieve)
    ctx = _context(dest, tmp_path, dry_run=False)
    stats = download(
        search=GMAIL.raw_search("q"),
        ctx=ctx,
        credentials_path=tmp_path / "c",
        limit=None,
    )
    assert stats["linked"] == 1 and stats["downloaded"] == 0
    assert sorted(p.name for p in dest.rglob("*.eml")) == [f"{INGESTED}.eml"]
    row = read_shard(shard_path(store, 2024))["a@acme"]
    assert row["msgid"] == "1a0f296bf69d52b0" and row["stem"] == INGESTED
    assert _held(store)[0] >= {"1a0f296bf69d52b0"}  # next run's prefilter skips it


def test_got_counts_every_got_match_when_max_caps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """10 matches, 7 not got, --max 2: got is 3, not 10 - 2."""

    def discover(*_args: object, **_kw: object) -> tuple[list[object], int, int]:
        return [], 10, 7

    monkeypatch.setattr(fetch_lib, "_discover_records", discover)
    ctx = _context(tmp_path, tmp_path, dry_run=True)
    caplog.set_level(logging.INFO)
    stats = download(
        search=GMAIL.raw_search("q"), ctx=ctx, credentials_path=tmp_path / "c", limit=2
    )
    assert (stats["got"], stats["candidates"]) == (3, 2)
    assert "3 got, 2 to fetch (5 beyond --max)" in caplog.text


# --- one row per Gmail message ---------------------------------------------

MAR = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)


def _ctx(dest: Path, store: Path) -> ThreadContext:
    return ThreadContext(
        dest=dest,
        dry_run=False,
        store_dir=store,
        staging_dir=dest / "staging",
        duplicates_dir=dest / ".duplicates",
    )


def _two_reusing_one_message_id() -> list[Record]:
    """Two distinct Gmail messages a sender gave one Message-ID (as BA did in 2011)."""
    later = datetime(2024, 3, 2, 9, 0, tzinfo=timezone.utc)
    return [
        _rec(b"2", "19b", "t2", later, "<r@ba>", later),
        _rec(b"1", "18a", "t1", MAR, "<r@ba>", MAR),
    ]


def test_two_messages_one_message_id_get_two_rows(tmp_path: Path) -> None:
    store = tmp_path / "manifest"
    pending, _linked = _plan(
        _two_reusing_one_message_id(), _ctx(tmp_path, store), _held(store)[1]
    )
    keys = {e["msgid"]: e["id"] for e in pending}
    assert keys == {"18a": "r@ba", "19b": "r@ba|gmail:19b"}  # first arrival keeps it


def test_a_filed_pair_keeps_its_keys_next_run(tmp_path: Path) -> None:
    """Once ingest has filed both (rows downloaded), the next run keys them alike."""
    store = tmp_path / "manifest"
    pending, _linked = _plan(
        _two_reusing_one_message_id(), _ctx(tmp_path, store), _held(store)[1]
    )
    rows: dict[str, Row] = {}
    for e in pending:
        stem = f"{e['msgid']:0>16}"  # two sends, two Date headers: two stems
        _file_at(tmp_path, stem, MAR)
        rows[str(e["id"])] = {
            "id": e["id"],
            "date": e["date"],
            "msgid": e["msgid"],
            "downloaded": True,
            "stem": stem,
        }
    write_shard(shard_path(store, 2024), rows)
    again, _linked = _plan(
        _two_reusing_one_message_id(), _ctx(tmp_path, store), _held(store)[1]
    )
    assert {e["msgid"]: e["id"] for e in again} == {
        "18a": "r@ba",
        "19b": "r@ba|gmail:19b",
    }
    assert _held(store)[0] == {"18a", "19b"}  # the prefilter holds both: nothing flips


def test_a_gmail_row_never_links_another_gmail_message(tmp_path: Path) -> None:
    store = tmp_path / "manifest"
    _file_at(tmp_path, "000000000000018a", MAR)
    row: Row = {
        "id": "r@ba",
        "msgid": "18a",
        "date": MAR.isoformat(),  # an INTERNALDATE equal to the copy's Date
        "downloaded": True,
        "stem": "000000000000018a",
    }
    write_shard(shard_path(store, 2024), {"r@ba": row})
    copy = _rec(b"3", "1c0", "t3", MAR, "<r@ba>", MAR)
    to_download, linked = _plan([copy], _ctx(tmp_path, store), _held(store)[1])
    assert not linked and [e["id"] for e in to_download] == ["r@ba|gmail:1c0"]


def test_an_excluded_row_is_never_downloaded(tmp_path: Path) -> None:
    store = tmp_path / "manifest"
    excluded: Row = {
        "id": "r@ba|gmail:19b",
        "msgid": "19b",
        "date": MAR.isoformat(),
        "excluded": "duplicate of r@ba",
        "downloaded": False,
    }
    write_shard(shard_path(store, 2024), {"r@ba|gmail:19b": excluded})
    assert "19b" in _held(store)[0]  # the prefilter skips it
    pending, _linked = _plan(
        _two_reusing_one_message_id(), _ctx(tmp_path, store), _held(store)[1]
    )
    assert [e["msgid"] for e in pending] == ["18a"]  # and _plan drops it too


def test_a_known_row_carries_its_shard_year(tmp_path: Path) -> None:
    store = tmp_path / "manifest"
    # catalog filed the row in 2023 (a boundary INTERNALDATE); fetch sees 2024
    write_shard(
        shard_path(store, 2023),
        {"r@ba": {"id": "r@ba", "date": "2023-01-01T00:00:00+00:00", "msgid": "18a"}},
    )
    rec = _rec(b"1", "18a", "t1", MAR, "<r@ba>", MAR)
    pending, _linked = _plan([rec], _ctx(tmp_path, store), _held(store)[1])
    assert pending[0]["id"] == "r@ba" and pending[0]["shard_year"] == 2023
