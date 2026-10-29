"""Tests for the provider-neutral key and the observation/settlement join."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from wicket.message import (
    GmailRow,
    gmail_key,
    local_msgid,
    message_id_of,
    message_key,
    normalize_message_id,
    sent_date,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<CAa+1X@mail.gmail.com>", "caa+1x@mail.gmail.com"),
        ("  <abc@x.com>  ", "abc@x.com"),
        ("Message body (comment) <Def@Y.COM>", "def@y.com"),
        ("", None),
        (None, None),
        ("no-brackets-token", "no-brackets-token"),
    ],
)
def test_normalize_message_id(raw: str | None, expected: str | None) -> None:
    assert normalize_message_id(raw) == expected


def test_message_key_prefers_message_id() -> None:
    assert (
        message_key(message_id="<ABC@x.com>", provider="gmail", native_id="18f2")
        == "abc@x.com"
    )


def test_message_key_falls_back_to_provider_tagged_native_id() -> None:
    assert (
        message_key(message_id=None, provider="gmail", native_id="18f2a3")
        == "gmail:18f2a3"
    )
    # Malformed/empty header is treated the same as missing.
    assert (
        message_key(message_id="   ", provider="fastmail", native_id="42")
        == "fastmail:42"
    )


@pytest.mark.parametrize(
    "header,expected",
    [
        (
            "Sat, 25 Oct 2026 07:23:10 +0530",
            datetime(2026, 10, 25, 1, 53, 10, tzinfo=timezone.utc),
        ),
        # A naive date is taken as UTC.
        (
            "Sat, 25 Oct 2026 07:23:10 -0000",
            datetime(2026, 10, 25, 7, 23, 10, tzinfo=timezone.utc),
        ),
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_sent_date(header: str | None, expected: datetime | None) -> None:
    assert sent_date(header) == expected


@pytest.mark.parametrize(
    "header,expected",
    [
        # policy.default cuts these short; every urn: id read as <urn and collided.
        (
            b"Message-ID: <urn:correios:msg:2011052220@1306095.amazon.com>",
            "<urn:correios:msg:2011052220@1306095.amazon.com>",
        ),
        (
            b"Message-ID: <share-43548ea1,43548ea1@x.com>",
            "<share-43548ea1,43548ea1@x.com>",
        ),
        # Exchange's bracketed local part, which policy.default raises on.
        (b"Message-ID: <[a1b2=@microsoft.com]>", "<[a1b2=@microsoft.com]>"),
        # Folded onto a continuation line.
        (b"Message-ID:\r\n <abc@x.com>", "<abc@x.com>"),
        # Non-ASCII reads as UTF-8, as policy.default read it; a stray byte is U+FFFD.
        ("Message-ID: <café@x.com>".encode(), "<café@x.com>"),
        (b"Message-ID: <caf\xe9@x.com>", "<caf\ufffd@x.com>"),
        (b"Subject: none", ""),
    ],
)
def test_message_id_of_reads_the_header_whole(header: bytes, expected: str) -> None:
    assert message_id_of(header + b"\r\n\r\nbody") == expected


def test_a_colon_id_keys_whole() -> None:
    raw = message_id_of(b"Message-ID: <urn:a:b@amazon.com>\r\n")
    assert message_key(message_id=raw, provider="gmail", native_id="x") == (
        "urn:a:b@amazon.com"
    )


# --- one row per Gmail message (gmail_key) --------------------------------

SENT = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)


def _always_held(_row: GmailRow) -> bool:
    return True


def _key(rows: dict[str, GmailRow], gmail_id: str, sent: datetime | None = SENT) -> str:
    return gmail_key(
        key="a@x", gmail_id=gmail_id, sent=sent, rows=rows, held=_always_held
    )


def _ingest_row(date: datetime = SENT) -> GmailRow:
    return {"id": "a@x", "msgid": local_msgid("a@x"), "date": date.isoformat()}


def test_local_msgid_is_ingests_filename() -> None:
    assert local_msgid("a@x") == hashlib.sha1(b"a@x").hexdigest()[:16]


def test_gmail_key_keeps_the_row_carrying_its_gmail_id() -> None:
    rows: dict[str, GmailRow] = {"a@x": {"id": "a@x", "msgid": "18a"}}
    assert _key(rows, "18a") == "a@x"


def test_gmail_key_finds_its_split_row() -> None:
    rows: dict[str, GmailRow] = {
        "a@x": {"id": "a@x", "msgid": "18a"},
        "a@x|gmail:19b": {"id": "a@x|gmail:19b", "msgid": "19b"},
    }
    assert _key(rows, "19b") == "a@x|gmail:19b"


def test_gmail_key_takes_a_free_key_and_records_it() -> None:
    rows: dict[str, GmailRow] = {}
    assert _key(rows, "18a") == "a@x"
    assert rows["a@x"]["msgid"] == "18a"


def test_gmail_key_splits_a_second_gmail_message() -> None:
    rows: dict[str, GmailRow] = {"a@x": {"id": "a@x", "msgid": "18a"}}
    assert _key(rows, "19b") == "a@x|gmail:19b"
    assert rows["a@x|gmail:19b"]["msgid"] == "19b"
    assert rows["a@x"]["msgid"] == "18a"  # the first message keeps its row


def test_gmail_key_in_one_run_is_stable_across_runs() -> None:
    rows: dict[str, GmailRow] = {}
    first = [_key(rows, g) for g in ("18a", "19b")]
    again = [_key(rows, g) for g in ("18a", "19b")]
    assert first == again == ["a@x", "a@x|gmail:19b"]


def test_gmail_key_links_the_copy_ingest_filed() -> None:
    row = _ingest_row()
    rows: dict[str, GmailRow] = {"a@x": row}
    assert _key(rows, "18a") == "a@x"
    assert rows["a@x"]["msgid"] == "18a"
    assert row["msgid"] == local_msgid(
        "a@x"
    )  # the caller's row is replaced, not mutated


@pytest.mark.parametrize(
    "case", ["different send time", "no parseable Date", "file not held", "a Gmail row"]
)
def test_gmail_key_never_links_anything_else(case: str) -> None:
    row = _ingest_row()
    sent: datetime | None = SENT
    held = _always_held
    if case == "different send time":
        sent = datetime(2024, 3, 1, 9, 7, tzinfo=timezone.utc)
    elif case == "no parseable Date":
        sent = None
    elif case == "file not held":

        def held(_row: GmailRow) -> bool:  # pylint: disable=function-redefined
            return False

    else:
        row["msgid"] = "17f"
    rows: dict[str, GmailRow] = {"a@x": row}
    key = gmail_key(key="a@x", gmail_id="18a", sent=sent, rows=rows, held=held)
    assert key == "a@x|gmail:18a"
