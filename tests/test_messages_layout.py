"""One file per message under ``messages/`` (ADR-MAIL and ADR 0002, 2026-11-14).

The stem and month come from the message; the row records ``stem``, ``cc``, ``bcc``
and ``attachments``; a second copy of a message in one store is not filed again.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import os
import stat
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path
from typing import cast

import pytest

from wicket import env
from wicket.manifest import Row, load_store, merge_catalog, message_files
from wicket.message import attachment_names, message_date, message_stem
from wicket.root import api as root_api
from wicket.root.lib import ingest as ingest_lib
from wicket.root.lib.catalog import _attachments, _fetch_rows_batch
from wicket.root.lib.ingest import Target, ingest_folder

ME = "you@gmail.com"
DATE = "Mon, 06 Jul 2026 09:00:00 +0000"
HAS_FLAGS = hasattr(os, "chflags") and hasattr(stat, "UF_IMMUTABLE")


def _raw(
    mid: str | None = "a@x",
    *,
    date: str | None = DATE,
    frm: str = "Them <Them@Acme.com>",
    subject: str = "s",
    extra: str = "",
) -> bytes:
    lines = [f"From: {frm}", f"To: {ME}", f"Subject: {subject}"]
    if mid is not None:
        lines.insert(0, f"Message-ID: <{mid}>")
    if date is not None:
        lines.append(f"Date: {date}")
    return ("\n".join(lines) + "\n" + extra + "\nbody\n").encode()


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


@pytest.fixture(autouse=True)
def _unlock(tmp_path: Path) -> Iterator[None]:
    """Clear every ``uchg`` under the test's dir afterwards, so it can be removed."""
    yield
    if not HAS_FLAGS:
        return
    for folder, _, names in os.walk(tmp_path):
        for name in names:
            path = os.path.join(folder, name)
            if not os.path.islink(path) and os.lstat(path).st_flags & stat.UF_IMMUTABLE:
                os.chflags(path, 0)


# --- the stem and the month ------------------------------------------------


def test_the_stem_hashes_message_id_utc_date_and_from() -> None:
    raw = _raw(date="Mon, 06 Jul 2026 14:30:00 +0530")
    assert message_stem(raw) == _sha("a@x\n2026-07-06T09:00:00Z\nthem@acme.com")


def test_a_missing_or_unreadable_header_contributes_nothing() -> None:
    blank = _sha("a@x\n\nthem@acme.com")
    assert message_stem(_raw(date=None)) == blank
    assert message_stem(_raw(date="+0530")) == blank  # unreadable, like a missing one
    assert message_stem(_raw("", frm="")) == _sha("\n2026-07-06T09:00:00Z\n")


def test_a_date_past_year_9999_in_utc_reads_as_unreadable() -> None:
    far = _raw(date="Fri, 31 Dec 9999 23:00:00 -0100")  # 00:00 on 1 Jan 10000 in UTC
    assert message_stem(far) == message_stem(_raw(date=None))


def test_two_copies_of_one_message_share_a_stem_whatever_else_differs() -> None:
    one = _raw(extra="X-Gmail-Labels: Inbox\n")
    other = _raw(subject="[CATCH-ALL] s")
    assert message_stem(one) == message_stem(other)


def test_the_month_is_the_date_header_then_received_then_the_fallback() -> None:
    from datetime import datetime, timezone

    fallback = datetime(2020, 1, 1, tzinfo=timezone.utc)
    received = "Received: from a by b; Tue, 30 Jun 2026 23:59:00 -0400\n"
    assert message_date(_raw(), fallback).month == 7
    assert message_date(_raw(date="+0530", extra=received), fallback).isoformat() == (
        "2026-07-01T03:59:00+00:00"
    )
    assert message_date(_raw(date=None), fallback) == fallback


# --- attachment names: one rule for ingest and catalog -------------------------


def _with_attachments() -> EmailMessage:
    msg = EmailMessage()
    msg["Message-ID"] = "<att@x>"
    msg.set_content("hi")
    msg.add_attachment(
        b"%PDF", maintype="application", subtype="pdf", filename="Invoice 7.pdf"
    )
    msg.add_attachment(b"png", maintype="image", subtype="png", filename="€.png")
    inner = EmailMessage()
    inner.set_content("inner")
    inner.add_attachment(
        b"zip", maintype="application", subtype="zip", filename="café.zip"
    )
    msg.add_attachment(inner)
    return msg


# The BODYSTRUCTURE an IMAP server sends for `_with_attachments`, written by hand.
_STRUCTURE = (
    b'1 (UID 7 BODYSTRUCTURE (("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 3 1 '
    b'NIL NIL NIL NIL)("APPLICATION" "PDF" NIL NIL NIL "BASE64" 8 NIL ("attachment" '
    b'("FILENAME" "Invoice 7.pdf")) NIL NIL)("IMAGE" "PNG" NIL NIL NIL "BASE64" 4 NIL '
    b'("attachment" ("FILENAME*" "utf-8\'\'%E2%82%AC.png")) NIL NIL)("MESSAGE" "RFC822" '
    b'NIL NIL NIL "7BIT" 90 ("x") (("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 6 '
    b'1 NIL NIL NIL NIL)("APPLICATION" "ZIP" NIL NIL NIL "BASE64" 4 NIL ("attachment" '
    b'("FILENAME" "=?utf-8?q?caf=C3=A9.zip?=")) NIL NIL) "MIXED" NIL NIL NIL) 5 NIL '
    b'("attachment" NIL) NIL NIL) "MIXED" ("BOUNDARY" "b") NIL NIL NIL))'
)


def test_ingest_and_catalog_read_the_same_attachment_names() -> None:
    parsed = email.message_from_bytes(
        _with_attachments().as_bytes(), policy=email.policy.default
    )
    expected = ["Invoice 7.pdf", "€.png", "café.zip"]
    assert attachment_names(parsed) == expected
    assert _attachments(_STRUCTURE) == expected


def test_a_file_name_sent_as_a_literal_is_read() -> None:
    class Conn:  # pylint: disable=too-few-public-methods
        """An IMAP connection whose reply sends a file name as a literal."""

        def uid(self, _cmd: str, _uids: str, items: str) -> tuple[str, list[object]]:
            """Answer the FETCH the way an IMAP server does."""
            fields = items[items.index("HEADER.FIELDS") : items.index("])") + 1]
            header = b"From: a@b.com\r\nCc: C <c@d.com>\r\nMessage-ID: <m@b>\r\n\r\n"
            meta = (
                b'1 (UID 7 X-GM-MSGID 1 X-GM-THRID 2 RFC822.SIZE 9 INTERNALDATE "06-Jul-2026'
                b' 09:00:00 +0000" X-GM-LABELS () BODYSTRUCTURE ("APPLICATION" "PDF" NIL NIL'
                b' NIL "BASE64" 4 NIL ("attachment" ("FILENAME" {9}'
            )
            return "OK", [
                (meta, b'a "q".pdf'),
                (
                    b")) NIL NIL) BODY[" + fields.encode() + b" {%d}" % len(header),
                    header,
                ),
                b")",
            ]

    rows, skips = _fetch_rows_batch(Conn(), [b"7"])  # type: ignore[arg-type]
    assert not skips
    assert rows[0]["attachments"] == ['a "q".pdf']
    assert rows[0]["cc"] == ["c@d.com"] and rows[0]["bcc"] == []


# --- ingest --src ------------------------------------------------------------


def _target(mail: Path) -> Target:
    return Target(
        store_dir=mail / ME / "manifest",
        messages_dir=mail / ME / "messages",
        account=ME,
    )


def _ingest(mail: Path, src: Path, **kw: object) -> dict[str, object]:
    return ingest_folder(
        src=src, target=_target(mail), account_for=lambda a: ME, **kw  # type: ignore[arg-type]
    )


def test_a_message_files_once_at_its_stem_and_its_row_names_the_stem(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    raw = _raw(extra=f"Cc: Bo <BO@corp.com>\nBcc: {ME}\n")
    (src / "a.eml").write_bytes(raw)
    stats = _ingest(tmp_path, src)
    stem = message_stem(raw)
    assert stats["added"] == 1
    assert (
        tmp_path / ME / "messages" / "2026" / "07" / f"{stem}.eml"
    ).read_bytes() == raw
    rows = list(load_store(tmp_path / ME / "manifest").values())
    assert len(rows) == 1
    row = rows[0]
    assert row["stem"] == stem and "path" not in row
    assert row["cc"] == ["bo@corp.com"] and row["bcc"] == [ME]
    assert row["attachments"] == []
    assert not (tmp_path / ME / "archive").exists()


def test_a_second_copy_is_not_filed_and_a_different_subject_is_reported(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    # No Message-ID: each is keyed by its bytes, so two keys share one stem.
    (src / "a.eml").write_bytes(_raw(None, extra="X-A: 1\n"))
    (src / "b.eml").write_bytes(_raw(None, subject="[CATCH-ALL] s"))
    stats = _ingest(tmp_path, src, to_trash=False)
    assert stats["added"] == 1 and stats["copies"] == 1
    (mismatch,) = cast(list[dict[str, object]], stats["subject_mismatches"])
    assert {mismatch["subject"], mismatch["holder_subject"]} == {"s", "[CATCH-ALL] s"}
    assert len(message_files(tmp_path / ME / "messages")) == 1


def test_a_copy_whose_subject_differs_keeps_its_source_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.eml").write_bytes(_raw(None, extra="X-A: 1\n"))
    (src / "b.eml").write_bytes(_raw(None, subject="other"))
    trash, original = tmp_path / "trash", ingest_lib.trash
    moved: list[Path] = []

    def fake_trash(paths: list[Path], _trash_dir: Path | None = None) -> int:
        moved.extend(paths)
        return original(paths, trash)

    monkeypatch.setattr(ingest_lib, "trash", fake_trash)
    _ingest(tmp_path, src, to_trash=True)
    names = {p.name for p in moved}
    assert "a.eml" in names and "b.eml" not in names  # possibly a different message


def test_a_file_no_row_names_is_taken_by_the_row_not_overwritten(
    tmp_path: Path,
) -> None:
    raw = _raw()
    stem = message_stem(raw)
    held = tmp_path / ME / "messages" / "2026" / "07" / f"{stem}.eml"
    held.parent.mkdir(parents=True)
    held.write_bytes(raw + b"tracked copy\n")  # filed by another tool
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.eml").write_bytes(raw)
    stats = _ingest(tmp_path, src)
    rows = list(load_store(tmp_path / ME / "manifest").values())
    assert len(rows) == 1
    row = rows[0]
    assert row["stem"] == stem and row["downloaded"] is True
    assert held.read_bytes() == raw + b"tracked copy\n"
    assert stats["copies"] == 0


# --- ingest --staged -----------------------------------------------------------


@pytest.fixture(name="mail")
def _mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    mail = tmp_path / "mail"
    (mail / ME).mkdir(parents=True)
    monkeypatch.setattr(env, "MAIL_ROOT", mail)
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    return mail


def _stage(mail: Path, msgid: str, raw: bytes) -> Path:
    path = mail / ME / "staging" / "1" / f"{msgid}@20260706T090004Z.eml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def test_a_staged_copy_catalog_never_saw_gets_a_whole_excluded_row(mail: Path) -> None:
    # Gmail holds one message twice: the first is filed, the second (another Gmail id,
    # no catalog row yet) has its stem and is a copy.
    _stage(mail, "18f2", _raw())
    first = root_api.ingest(ME, options=root_api.IngestOptions(staged=True))
    assert first["added"] == 1
    staged = _stage(mail, "18f3", _raw(extra="X-Gmail: 1\n"))
    stats = root_api.ingest(ME, options=root_api.IngestOptions(staged=True))
    assert stats["copies"] == 1 and stats["added"] == 0
    rows = load_store(mail / ME / "manifest")
    (copy,) = [r for r in rows.values() if r.get("msgid") == "18f3"]
    assert copy["excluded"] == "duplicate of a@x"
    assert copy["date"] and copy["thread_id"] == "1" and "stem" not in copy
    assert copy["downloaded"] is False
    assert not staged.exists()
    assert list((mail / ".duplicates" / ME).rglob("18f3.eml"))
    assert len(message_files(mail / ME / "messages")) == 1


def test_a_catalog_keeps_the_stem() -> None:
    prior: dict[str, Row] = {"a": {"id": "a", "downloaded": True, "stem": "s"}}
    observed: dict[str, Row] = {"a": {"id": "a", "from": "x@y.com", "to": []}}
    merged = merge_catalog(prior, observed, complete=False)
    assert merged["a"]["stem"] == "s" and merged["a"]["downloaded"] is True
