"""Tests for the census record builder (ADR 0002 unified inventory row)."""

from __future__ import annotations

from wicket.catalog.observe import _finalize_record


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
