"""Each provider's IMAP dialect (`wicket.provider`): what a verb asks it, and what it answers."""

from __future__ import annotations

from datetime import date

from wicket.provider import GMAIL, imap_quote
from wicket.root.lib.catalog import _fetch_items

# --- Gmail: the dialect every verb spoke before the provider existed --------------


def test_gmail_reads_its_decimal_id_as_hex_and_its_thread_as_is() -> None:
    meta = b"1 (UID 7 X-GM-MSGID 1795338466127216210 X-GM-THRID 1795338466127216211 "
    assert GMAIL.parse_id(meta) == f"{1795338466127216210:x}"
    assert GMAIL.parse_thread(meta) == "1795338466127216211"


def test_gmail_finds_no_id_where_the_response_carries_none() -> None:
    assert GMAIL.parse_id(b"1 (UID 7 RFC822.SIZE 10)") is None
    assert GMAIL.parse_thread(b"1 (UID 7 RFC822.SIZE 10)") is None


def test_gmail_orders_by_id_value_and_names_the_file_by_the_id() -> None:
    ids = ["18f", "190", "1a"]
    assert sorted(ids, key=lambda m: GMAIL.order(m, "")) == ["1a", "18f", "190"]
    assert GMAIL.file_stem("18f2a3") == "18f2a3"


def test_gmail_searches_through_x_gm_raw() -> None:
    assert GMAIL.raw_search('from:a "b"') == (
        "CHARSET",
        "UTF-8",
        "X-GM-RAW",
        '"from:a \\"b\\""',
    )
    assert GMAIL.year_search(2024) == GMAIL.raw_search(
        "after:2024/01/01 before:2025/01/01"
    )
    assert GMAIL.since_search("2d", date(2026, 10, 29)) == GMAIL.raw_search(
        "newer_than:2d"
    )


def test_gmail_catalog_fetches_its_items_and_the_list_id_header() -> None:
    assert _fetch_items(GMAIL) == (
        "(X-GM-MSGID X-GM-THRID RFC822.SIZE INTERNALDATE X-GM-LABELS "
        "BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID DATE LIST-ID)])"
    )


def test_imap_quote_escapes_backslash_and_quote() -> None:
    assert imap_quote('a\\b"c') == '"a\\\\b\\"c"'
