"""Tests for fetch planning, links and staging (offline; the IMAP paths are excluded)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

import wicket.root.lib.fetch as fetch_lib
from wicket.filing import build_domain_query, compute_domain
from wicket.manifest import Row, read_shard, shard_path, write_shard
from wicket.provider import GMAIL
from wicket.root.lib.fetch import (
    ThreadContext,
    _held,
    _plan,
    _split_linked,
    _write_links,
    download,
)

ME = "you@gmail.com"


def test_compute_domain_inbound_uses_sender() -> None:
    assert compute_domain("billing@acme.com", "you@gmail.com", ME) == "acme.com"


def test_compute_domain_inbound_ignores_to() -> None:
    # Inbound mail files under the sender, regardless of a forwarding recipient.
    assert compute_domain("billing@acme.com", "fwd@elsewhere.net", ME) == "acme.com"


def test_compute_domain_outbound_single_recipient() -> None:
    assert compute_domain(ME, "billing@acme.com", ME) == "acme.com"


def test_compute_domain_outbound_multiple_domains_is_none() -> None:
    assert compute_domain(ME, "a@acme.com, b@globex.com", ME) is None


def test_compute_domain_outbound_self_cc_ignored() -> None:
    # cc'ing yourself does not make an outbound thread ambiguous.
    assert compute_domain(ME, "billing@acme.com, you@gmail.com", ME) == "acme.com"


def test_compute_domain_rejects_path_traversal() -> None:
    # A crafted From domain must never become a filesystem path segment.
    assert compute_domain("x@../../../../tmp/evil", "you@gmail.com", ME) is None
    assert compute_domain("x@/etc", "you@gmail.com", ME) is None
    assert compute_domain("x@a/b", "you@gmail.com", ME) is None


def test_compute_domain_alias_canonicalizes() -> None:
    aliases = {"emirates.email": "emirates.com"}
    assert (
        compute_domain("x@emirates.email", "you@gmail.com", ME, aliases)
        == "emirates.com"
    )


def test_build_domain_query_expands_and_orders() -> None:
    assert build_domain_query(["acme.com"]) == "{from:acme.com to:acme.com}"


# Eight parameters because a fetch record has eight fields; naming them here is
# the point of the helper, and collapsing them into a dict would only move the
# arity into every call site as untyped keys.
def _rec(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    uid: bytes,
    msgid: str,
    thr: str,
    dt: datetime,
    frm: str,
    to: str,
    mid: str,
    sent: datetime | None = None,
) -> tuple[bytes, str, str, datetime, str, str, str, datetime | None, str]:
    return (uid, msgid, thr, dt, frm, to, mid, sent, GMAIL.all_mail or "")


def test_plan_stages_every_message_and_computes_no_domain(tmp_path: Path) -> None:
    """Plan rule 6: fetch files nothing, so a thread no rule resolves is still got."""
    ctx = ThreadContext(
        dest=tmp_path, account=ME, domain_aliases={}, dry_run=False, store_dir=None
    )
    dt = datetime(2024, 3, 1, 9, 30, tzinfo=timezone.utc)
    records = [
        _rec(b"1", "18f2", "77", dt, "billing@acme.com", "you@gmail.com", "<a@acme>"),
        # outbound to two distinct domains: fetch used to drop this thread
        _rec(b"2", "19a0", "78", dt, ME, "a@acme.com, b@globex.com", "<b@x>"),
    ]
    pending = _plan(records, ctx)
    assert [e["path"] for e in pending] == [
        "77/18f2@20240301T093000Z.eml",
        "78/19a0@20240301T093000Z.eml",
    ]
    assert all("domain" not in e for e in pending)
    assert pending[0]["id"] == "a@acme"


def test_download_stages_the_body_and_writes_no_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real pass: the body lands in staging/, the archive and the manifest stay as they were."""
    dt = datetime(2024, 3, 1, 9, 30, tzinfo=timezone.utc)
    rec = _rec(b"1", "18f2", "77", dt, "billing@acme.com", ME, "<a@acme>", dt)

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
    ctx = ThreadContext(
        dest=tmp_path / "archive",
        account=ME,
        domain_aliases={},
        dry_run=False,
        store_dir=store,
        staging_dir=tmp_path / "staging",
    )
    stats = download(
        search=GMAIL.raw_search("q"),
        ctx=ctx,
        credentials_path=tmp_path / "c",
        limit=None,
    )
    assert stats["downloaded"] == 1
    assert (tmp_path / "staging" / "77" / "18f2@20240301T093000Z.eml").exists()
    assert not list((tmp_path / "archive").rglob("*.eml"))
    assert not list(store.glob("*.jsonl"))  # settles nothing


def test_got_is_staged_filed_excluded_and_duplicate(tmp_path: Path) -> None:
    """fetch's dedup reads staging/ and .duplicates/<store>/ as well as the rows."""
    store, staging, dups = tmp_path / "manifest", tmp_path / "staging", tmp_path / "dup"
    write_shard(
        shard_path(store, 2024),
        {
            "a": {"id": "a", "msgid": "a1", "downloaded": True},
            "b": {"id": "b", "msgid": "b2", "excluded": "duplicate of a"},
            "c": {"id": "c", "msgid": "c3", "downloaded": False},
        },
    )
    (staging / "77").mkdir(parents=True)
    (staging / "77" / "d4@20240301T093000Z.eml").write_bytes(b"x")
    (staging / "77" / "not-a-staged-name.eml").write_bytes(b"x")
    (dups / "acme.com" / "2024-03").mkdir(parents=True)
    (dups / "acme.com" / "2024-03" / "e5.eml").write_bytes(b"x")
    ctx = ThreadContext(
        dest=tmp_path / "archive",
        account=ME,
        domain_aliases={},
        dry_run=True,
        store_dir=store,
        staging_dir=staging,
        duplicates_dir=dups,
    )
    got = fetch_lib._got(ctx)[0]
    assert len(got) == 4 and all(m in got for m in ("a1", "b2", "d4", "e5"))
    assert fetch_lib._got_count(ctx) == 4


def test_a_real_run_needs_a_staging_dir(tmp_path: Path) -> None:
    ctx = ThreadContext(dest=tmp_path, account=ME, domain_aliases={}, dry_run=False)
    with pytest.raises(ValueError, match="staging dir"):
        download(
            search=GMAIL.raw_search("q"),
            ctx=ctx,
            credentials_path=tmp_path / "c",
            limit=None,
        )


# --- Linking a copy ingest already filed ---------------------------------

SENT = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)
INGESTED = "acme.com/2024-03/068d2a33a1af5856.eml"


def _ingested_store(tmp_path: Path, *, date: datetime = SENT) -> tuple[Path, Path]:
    """An archive + store holding one message as ingest files it (sha1-named)."""
    dest, store = tmp_path / "archive", tmp_path / "manifest"
    (dest / INGESTED).parent.mkdir(parents=True)
    (dest / INGESTED).write_bytes(b"Message-ID: <a@acme>\r\n\r\nbody")
    row: Row = {
        "id": "a@acme",
        "msgid": "068d2a33a1af5856",
        "thread_id": "local",
        "date": date.isoformat(),
        "from": "billing@acme.com",
        "downloaded": True,
        "domain": "acme.com",
        "path": INGESTED,
    }
    write_shard(shard_path(store, 2024), {"a@acme": row})
    return dest, store


def _gmail_copy(dest: Path, store: Path, sent: datetime | None = SENT) -> list[Row]:
    """The same message as fetch plans it from Gmail (INTERNALDATE a few s later)."""
    ctx = ThreadContext(
        dest=dest, account=ME, domain_aliases={}, dry_run=True, store_dir=store
    )
    internal = datetime(2024, 3, 1, 9, 0, 4, tzinfo=timezone.utc)
    rec = _rec(
        b"1",
        "1a0f296bf69d52b0",
        "t1",
        internal,
        "billing@acme.com",
        ME,
        "<a@acme>",
        sent,
    )
    return _plan([rec], ctx)


def test_held_maps_rows_by_id_with_shard_year(tmp_path: Path) -> None:
    _dest, store = _ingested_store(tmp_path)
    write_shard(
        store / "notes.jsonl", {"x@y": {"id": "x@y", "msgid": "ab", "downloaded": True}}
    )
    msgids, by_id = _held(store)
    assert msgids == {"068d2a33a1af5856", "ab"}
    assert set(by_id) == {"a@acme"}  # a non-year shard feeds the prefilter only
    assert by_id["a@acme"][1] == 2024


def test_split_links_the_ingested_copy(tmp_path: Path) -> None:
    dest, store = _ingested_store(tmp_path)
    to_download, linked = _split_linked(_gmail_copy(dest, store), _held(store)[1])
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
        (dest / INGESTED).unlink()
    pending = _gmail_copy(dest, store, sent)
    to_download, linked = _split_linked(pending, _held(store)[1])
    assert not linked and len(to_download) == 1
    assert to_download[0]["id"] == "a@acme|gmail:1a0f296bf69d52b0"  # its own row


def test_write_links_records_only_the_gmail_msgid(tmp_path: Path) -> None:
    dest, store = _ingested_store(tmp_path)
    _, linked = _split_linked(_gmail_copy(dest, store), _held(store)[1])
    _write_links(store, linked)
    row = read_shard(shard_path(store, 2024))["a@acme"]
    assert row["msgid"] == "1a0f296bf69d52b0"
    assert row["path"] == INGESTED and row["thread_id"] == "local"
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
        "billing@acme.com",
        ME,
        "<a@acme>",
        SENT,
    )

    def discover(*_args: object, **_kw: object) -> tuple[list[object], int, int]:
        return [rec], 1, 1

    def no_retrieve(**_kwargs: object) -> None:
        raise AssertionError("a linked message must not be downloaded")

    monkeypatch.setattr(fetch_lib, "_discover_records", discover)
    monkeypatch.setattr(fetch_lib, "_retrieve_months", no_retrieve)
    ctx = ThreadContext(
        dest=dest,
        account=ME,
        domain_aliases={},
        dry_run=False,
        store_dir=store,
        staging_dir=tmp_path / "staging",
    )
    stats = download(
        search=GMAIL.raw_search("q"),
        ctx=ctx,
        credentials_path=tmp_path / "c",
        limit=None,
    )
    assert stats["linked"] == 1 and stats["downloaded"] == 0
    assert sorted(p.name for p in dest.rglob("*.eml")) == ["068d2a33a1af5856.eml"]
    row = read_shard(shard_path(store, 2024))["a@acme"]
    assert row["msgid"] == "1a0f296bf69d52b0" and row["path"] == INGESTED
    assert _held(store)[0] >= {"1a0f296bf69d52b0"}  # next run's prefilter skips it


def test_got_counts_every_got_match_when_max_caps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """10 matches, 7 not got, --max 2: got is 3, not 10 - 2."""

    def discover(*_args: object, **_kw: object) -> tuple[list[object], int, int]:
        return [], 10, 7

    monkeypatch.setattr(fetch_lib, "_discover_records", discover)
    ctx = ThreadContext(dest=tmp_path, account=ME, domain_aliases={}, dry_run=True)
    caplog.set_level(logging.INFO)
    stats = download(
        search=GMAIL.raw_search("q"), ctx=ctx, credentials_path=tmp_path / "c", limit=2
    )
    assert (stats["got"], stats["candidates"]) == (3, 2)
    assert "3 got, 2 to fetch (5 beyond --max)" in caplog.text


# --- one row per Gmail message ---------------------------------------------

MAR = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)


def _ctx(dest: Path, store: Path | None) -> ThreadContext:
    return ThreadContext(
        dest=dest,
        account=ME,
        domain_aliases={},
        dry_run=False,
        store_dir=store,
        staging_dir=dest / "staging",
    )


def _two_reusing_one_message_id() -> (
    list[tuple[bytes, str, str, datetime, str, str, str, datetime | None, str]]
):
    """Two distinct Gmail messages a sender gave one Message-ID (as BA did in 2011)."""
    later = datetime(2024, 3, 2, 9, 0, tzinfo=timezone.utc)
    return [
        _rec(b"2", "19b", "t2", later, "news@ba.com", ME, "<r@ba>", later),
        _rec(b"1", "18a", "t1", MAR, "news@ba.com", ME, "<r@ba>", MAR),
    ]


def test_two_messages_one_message_id_get_two_rows(tmp_path: Path) -> None:
    store = tmp_path / "manifest"
    pending = _plan(_two_reusing_one_message_id(), _ctx(tmp_path, store))
    keys = {e["msgid"]: e["id"] for e in pending}
    assert keys == {"18a": "r@ba", "19b": "r@ba|gmail:19b"}  # first arrival keeps it


def test_a_filed_pair_keeps_its_keys_next_run(tmp_path: Path) -> None:
    """Once ingest has filed both (rows downloaded), the next run keys them alike."""
    store = tmp_path / "manifest"
    pending = _plan(_two_reusing_one_message_id(), _ctx(tmp_path, store))
    rows: dict[str, Row] = {}
    for e in pending:
        path = f"ba.com/2024-03/{e['msgid']}.eml"
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(b"x")
        rows[str(e["id"])] = {
            "id": e["id"],
            "msgid": e["msgid"],
            "downloaded": True,
            "path": path,
        }
    write_shard(shard_path(store, 2024), rows)
    again = _plan(_two_reusing_one_message_id(), _ctx(tmp_path, store))
    assert {e["msgid"]: e["id"] for e in again} == {
        "18a": "r@ba",
        "19b": "r@ba|gmail:19b",
    }
    assert _held(store)[0] == {"18a", "19b"}  # the prefilter holds both: nothing flips


def test_a_gmail_row_never_links_another_gmail_message(tmp_path: Path) -> None:
    store = tmp_path / "manifest"
    (tmp_path / "ba.com" / "2024-03").mkdir(parents=True)
    (tmp_path / "ba.com" / "2024-03" / "18a.eml").write_bytes(b"x")
    row: Row = {
        "id": "r@ba",
        "msgid": "18a",
        "date": MAR.isoformat(),  # an INTERNALDATE equal to the copy's Date
        "downloaded": True,
        "path": "ba.com/2024-03/18a.eml",
    }
    write_shard(shard_path(store, 2024), {"r@ba": row})
    copy = _rec(b"3", "1c0", "t3", MAR, "news@ba.com", ME, "<r@ba>", MAR)
    pending = _plan([copy], _ctx(tmp_path, store))
    to_download, linked = _split_linked(pending, _held(store)[1])
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
    pending = _plan(_two_reusing_one_message_id(), _ctx(tmp_path, store))
    assert [e["msgid"] for e in pending] == ["18a"]  # and _plan drops it too


def test_a_known_row_carries_its_shard_year(tmp_path: Path) -> None:
    store = tmp_path / "manifest"
    # catalog filed the row in 2023 (a boundary INTERNALDATE); fetch sees 2024
    write_shard(shard_path(store, 2023), {"r@ba": {"id": "r@ba", "msgid": "18a"}})
    rec = _rec(b"1", "18a", "t1", MAR, "news@ba.com", ME, "<r@ba>", MAR)
    pending = _plan([rec], _ctx(tmp_path, store))
    assert pending[0]["id"] == "r@ba" and pending[0]["shard_year"] == 2023
