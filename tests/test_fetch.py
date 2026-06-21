"""Tests for fetch planning + settlement (offline; the IMAP paths are excluded)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from wicket.fetch.lib import ThreadContext, _plan, _write_settlement
from wicket.manifest import read_shard, shard_path
from wicket.reconcile import build_domain_query, compute_domain

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


def _rec(
    uid: bytes, msgid: str, thr: str, dt: datetime, frm: str, to: str, mid: str
) -> tuple[bytes, str, str, datetime, str, str, str]:
    return (uid, msgid, thr, dt, frm, to, mid)


def test_plan_files_by_domain_and_skips_unresolved(tmp_path: Path) -> None:
    ctx = ThreadContext(
        dest=tmp_path,
        imap_email=ME,
        domain_aliases={},
        dry_run=False,
        store_dir=None,
    )
    dt = datetime(2024, 3, 1, tzinfo=timezone.utc)
    records = [
        _rec(b"1", "18f2", "t1", dt, "billing@acme.com", "you@gmail.com", "<a@acme>"),
        # outbound to two distinct domains -> unresolved -> skipped
        _rec(b"2", "19a0", "t2", dt, ME, "a@acme.com, b@globex.com", "<b@x>"),
    ]
    pending, on_disk = _plan(records, ctx)
    assert not on_disk
    assert len(pending) == 1  # the unresolved (outbound multi-domain) thread skipped
    assert pending[0]["domain"] == "acme.com"
    assert pending[0]["path"] == "acme.com/2024-03/18f2.eml"
    assert pending[0]["id"] == "a@acme"


def test_plan_routes_on_disk_separately(tmp_path: Path) -> None:
    (tmp_path / "acme.com" / "2024-03").mkdir(parents=True)
    (tmp_path / "acme.com" / "2024-03" / "18f2.eml").write_bytes(b"x")
    ctx = ThreadContext(
        dest=tmp_path,
        imap_email=ME,
        domain_aliases={},
        dry_run=False,
        store_dir=None,
    )
    dt = datetime(2024, 3, 1, tzinfo=timezone.utc)
    records = [
        _rec(b"1", "18f2", "t1", dt, "billing@acme.com", "you@gmail.com", "<a@x>")
    ]
    pending, on_disk = _plan(records, ctx)
    assert not pending and len(on_disk) == 1


def test_write_settlement_into_shard_drops_plan_keys(tmp_path: Path) -> None:
    entry = {
        "uid": b"1",
        "month": "2024-03",
        "year": 2024,
        "id": "a@acme.com",
        "msgid": "18f2",
        "thread_id": "t1",
        "date": "2024-03-01T00:00:00+00:00",
        "domain": "acme.com",
        "path": "acme.com/2024-03/18f2.eml",
    }
    _write_settlement(tmp_path, [entry])
    row = read_shard(shard_path(tmp_path, 2024))["a@acme.com"]
    assert row["downloaded"] is True
    assert row["path"] == "acme.com/2024-03/18f2.eml"
    assert row["domain"] == "acme.com"
    assert "uid" not in row and "month" not in row  # planning-only keys not stored
