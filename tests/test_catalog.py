"""Tests for the census record builder (ADR 0002 unified inventory row)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from wicket.catalog.lib import _fetch_rows_batch, _finalize_record, _merge_observed
from wicket.manifest import Row, read_shard, shard_path, write_shard
from wicket.message import local_msgid


def _meta(*, labels: bytes = b'X-GM-LABELS ("Receipts")') -> bytes:
    return (
        b"1 (UID 7 X-GM-MSGID 18446744073709551615 X-GM-THRID 1690000000000 "
        b'RFC822.SIZE 48211 INTERNALDATE "22-Mar-2024 14:53:21 +0000" ' + labels + b" "
    )


def test_finalize_record_unified_schema() -> None:
    header_block = (
        b"From: Acme Billing <Billing@Acme.com>\r\n"
        b"To: You <you@gmail.com>, ops@acme.com\r\n"
        b"Subject: Invoice 1023\r\n"
        b"Message-ID: <CAa+1X@mail.gmail.com>\r\n\r\n"
    )
    row = _finalize_record(_meta(), header_block)
    assert row is not None
    assert row["id"] == "caa+1x@mail.gmail.com"
    assert row["msgid"] == "ffffffffffffffff"
    assert row["thread_id"] == "1690000000000"
    assert row["date"] == "2024-03-22T14:53:21+00:00"
    assert row["size"] == 48211
    assert row["from"] == "billing@acme.com"
    assert row["to"] == ["ops@acme.com", "you@gmail.com"]
    assert row["subject"] == "Invoice 1023"
    assert row["labels"] == ["Receipts"]


def test_finalize_record_without_message_id_falls_back_to_provider_key() -> None:
    header_block = b"From: a@x.com\r\nSubject: Hi\r\n\r\n"
    row = _finalize_record(_meta(), header_block)
    assert row is not None
    assert row["id"] == "gmail:ffffffffffffffff"
    assert row["to"] == []


def test_finalize_record_skips_unparseable_metadata() -> None:
    # Missing X-GM-MSGID → no row.
    assert (
        _finalize_record(b"1 (UID 7 RFC822.SIZE 10)", b"From: a@x.com\r\n\r\n") is None
    )


def test_header_block_reads_a_colon_message_id_whole() -> None:
    # policy.default read this as "<urn", so every urn: message shared one row.
    from wicket.catalog.lib import _parse_header_block

    block = (
        b"From: Amazon <ship@amazon.com>\r\nTo: you@gmail.com\r\nSubject: order\r\n"
        b"Message-ID: <urn:correios:msg:1@amazon.com>\r\n\r\n"
    )
    assert _parse_header_block(block)[3] == "<urn:correios:msg:1@amazon.com>"


# --- keying observations against the whole store -------------------------

SENT = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)


def _obs(
    msgid: str, date: str, *, key: str = "r@ba", sent: datetime | None = SENT
) -> dict[str, object]:
    """One observation as a worker returns it (``id`` is the bare message_key)."""
    return {
        "id": key,
        "msgid": msgid,
        "thread_id": "t",
        "date": date,
        "size": 1,
        "from": "news@ba.com",
        "to": [],
        "subject": "s",
        "labels": [],
        "_sent": sent,
    }


def _merge(
    store: Path,
    observed: list[dict[str, object]],
    swept: set[int],
    archive: Path | None = None,
) -> None:
    _merge_observed(
        store_dir=store,
        archive_dir=archive,
        observed=observed,
        swept=swept,
        dry_run=False,
    )


def test_two_gmail_ids_one_message_id_get_two_rows(tmp_path: Path) -> None:
    _merge(
        tmp_path,
        [
            _obs("19b", "2024-03-02T00:00:00+00:00"),
            _obs("18a", "2024-03-01T00:00:00+00:00"),
        ],
        {2024},
    )
    shard = read_shard(shard_path(tmp_path, 2024))
    assert {i: r["msgid"] for i, r in shard.items()} == {
        "r@ba": "18a",
        "r@ba|gmail:19b": "19b",
    }
    assert all(
        "_sent" not in r for r in shard.values()
    )  # the send time is never stored


def test_one_message_id_in_two_years_keeps_one_bare_key(tmp_path: Path) -> None:
    _merge(
        tmp_path,
        [
            _obs("18a", "2018-12-31T00:00:00+00:00"),
            _obs("19b", "2019-01-01T00:00:00+00:00"),
        ],
        {2018, 2019},
    )
    assert set(read_shard(shard_path(tmp_path, 2018))) == {"r@ba"}
    assert set(read_shard(shard_path(tmp_path, 2019))) == {"r@ba|gmail:19b"}


def test_a_resweep_is_byte_identical(tmp_path: Path) -> None:
    def sweep_once() -> bytes:
        _merge(
            tmp_path,
            [
                _obs("19b", "2024-03-02T00:00:00+00:00"),
                _obs("18a", "2024-03-01T00:00:00+00:00"),
            ],
            {2024},
        )
        return shard_path(tmp_path, 2024).read_bytes()

    assert sweep_once() == sweep_once()


def test_an_ingest_row_in_an_unswept_year_is_linked_and_nothing_dropped(
    tmp_path: Path,
) -> None:
    store, archive = tmp_path / "manifest", tmp_path / "archive"
    path = "ba.com/2019-12/" + local_msgid("r@ba") + ".eml"
    (archive / path).parent.mkdir(parents=True)
    (archive / path).write_bytes(b"x")
    ingested = {
        "id": "r@ba",
        "msgid": local_msgid("r@ba"),
        "date": SENT.isoformat(),
        "downloaded": True,
        "path": path,
    }
    bystander: Row = {
        "id": "other@x",
        "msgid": "17f",
        "date": "2019-06-01T00:00:00+00:00",
    }
    write_shard(shard_path(store, 2019), {"r@ba": ingested, "other@x": bystander})
    # its Gmail copy arrived on 1 Jan 2020; only 2020 is swept
    _merge(store, [_obs("18a", "2020-01-01T00:00:00+00:00")], {2020}, archive)
    shard = read_shard(shard_path(store, 2019))
    assert shard["r@ba"]["msgid"] == "18a" and shard["r@ba"]["path"] == path
    assert shard["r@ba"]["deleted"] is False
    assert "other@x" in shard  # a year not swept loses nothing
    assert not read_shard(shard_path(store, 2020))  # no second row for the message


def test_a_resweep_keeps_excluded(tmp_path: Path) -> None:
    row: Row = {
        "id": "r@ba|gmail:19b",
        "msgid": "19b",
        "date": "2024-03-02T00:00:00+00:00",
        "excluded": "duplicate of r@ba",
    }
    write_shard(
        shard_path(tmp_path, 2024),
        {"r@ba": {"id": "r@ba", "msgid": "18a"}, "r@ba|gmail:19b": row},
    )
    _merge(
        tmp_path,
        [
            _obs("18a", "2024-03-01T00:00:00+00:00"),
            _obs("19b", "2024-03-02T00:00:00+00:00"),
        ],
        {2024},
    )
    assert (
        read_shard(shard_path(tmp_path, 2024))["r@ba|gmail:19b"]["excluded"]
        == "duplicate of r@ba"
    )


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    _merge_observed(
        store_dir=tmp_path,
        archive_dir=None,
        observed=[_obs("18a", "2024-03-01T00:00:00+00:00")],
        swept={2024},
        dry_run=True,
    )
    assert not list(tmp_path.iterdir())


class _Conn:
    """An IMAP connection that answers one header FETCH the way Gmail does."""

    def __init__(self) -> None:
        self.items = ""

    def uid(self, _cmd: str, _uids: str, items: str) -> tuple[str, list[object]]:
        """Answer the FETCH with the field list it asked for, as Gmail echoes it."""
        self.items = items
        fields = items[items.index("HEADER.FIELDS") : items.index("])") + 1]
        header = (
            b"From: news@ba.com\r\nMessage-ID: <r@ba>\r\n"
            b"Date: Fri, 01 Mar 2024 09:00:00 +0000\r\n\r\n"
        )
        meta = _meta() + b"BODY[" + fields.encode() + b" {%d}" % len(header)
        return "OK", [(meta, header), b")"]


def test_the_header_fetch_asks_for_date_and_parses_it() -> None:
    conn = _Conn()
    rows, skips = _fetch_rows_batch(conn, [b"7"])  # type: ignore[arg-type]
    assert "DATE" in conn.items and not skips
    assert rows[0]["id"] == "r@ba"  # the marker matched, so the header block was read
    assert rows[0]["_sent"] == SENT
