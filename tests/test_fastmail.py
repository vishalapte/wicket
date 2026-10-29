"""The Fastmail provider: its dialect, its folders, and each verb over a Fastmail mailbox.

A Fastmail mailbox has no all-mail folder, so catalog and fetch walk every folder but
Trash, Spam and the four non-mail folders, one message per EMAILID (RFC 8474). No test
reaches the network: a fake connection answers every IMAP command.
"""

from __future__ import annotations

import imaplib
import json
import os
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import cast

import pytest

import wicket.__main__ as root
import wicket.root.lib.catalog as catalog_lib
import wicket.root.lib.fetch as fetch_lib
from wicket import auth, env
from wicket.manifest import Row, load_store
from wicket.provider import FASTMAIL, GMAIL, parse_staged, provider_of
from wicket.root import api as root_api

ME = "you@enine.com"
LOGIN = "you@fastmail.com"

# Two folders worth walking, two the walk skips, and one that cannot be selected.
LIST = [
    b'(\\HasNoChildren) "/" INBOX',
    b'(\\HasNoChildren \\Archive) "/" Archive',
    b'(\\HasNoChildren) "/" "Receipts/Uber"',
    b'(\\Noinferiors \\HasNoChildren \\Junk) "/" Spam',
    b'(\\Noinferiors \\HasNoChildren \\Trash) "/" Trash',
    b'(\\HasNoChildren \\XNotes) "/" Notes',
    b'(\\Noselect \\HasChildren) "/" Leads',
    (b'(\\HasNoChildren) "/" {11}', b"Grand Canyon"),
]

# Each folder's messages: uid -> (EMAILID, THREADID, INTERNALDATE). M1 is in two folders.
FOLDERS: dict[str, dict[int, tuple[str, str, str]]] = {
    "INBOX": {7: ("Str0hkL1JFLB", "AlN-Somskwhs", "30-Jan-2026 12:26:16 -0500")},
    "Archive": {
        3: ("StqfklOovjb-", "A71LyBN4hCHo", "17-Feb-2025 09:32:51 -0500"),
        9: ("Str0hkL1JFLB", "AlN-Somskwhs", "30-Jan-2026 12:26:16 -0500"),
    },
    '"Receipts/Uber"': {},
    '"Grand Canyon"': {},
    '"Trash"': {5: ("SdeleTed____", "Agone_______", "1-Mar-2026 10:00:00 +0000")},
}


class FakeFastmail:
    """A Fastmail connection: LIST, a read-only SELECT per folder, UID SEARCH and FETCH."""

    def __init__(self, *, capabilities: bytes = b"IMAP4rev1 OBJECTID SPECIAL-USE"):
        self.capabilities = capabilities
        self.folder: str | None = None
        self.selects: list[tuple[str, bool]] = []
        self.fetches: list[tuple[str, str]] = []

    def login(self, _email: str, _password: str) -> tuple[str, list[bytes]]:
        return "OK", [b""]

    def capability(self) -> tuple[str, list[bytes]]:
        """What the server announces after login."""
        return "OK", [self.capabilities]

    def list(self) -> tuple[str, Sequence[object]]:
        """The folder list, as `LIST`."""
        return "OK", list(LIST)

    def select(
        self, mailbox: str, readonly: bool = False
    ) -> tuple[str, Sequence[bytes]]:
        """Record the SELECT (its quoted name and whether read-only) and switch folder."""
        self.selects.append((mailbox, readonly))
        self.folder = mailbox if mailbox.startswith('"') else f'"{mailbox}"'
        if self.folder == '"INBOX"':
            self.folder = "INBOX"
        elif self.folder == '"Archive"':
            self.folder = "Archive"
        return "OK", [b"1"]

    def uid(self, command: str, *args: str) -> tuple[str, Sequence[object]]:
        """``UID SEARCH`` (every UID of the selected folder) or ``UID FETCH`` of them."""
        held = FOLDERS.get(cast(str, self.folder), {})
        if command == "SEARCH":
            return "OK", [" ".join(str(u) for u in sorted(held)).encode()]
        uids = [int(u) for u in args[0].split(",")]
        self.fetches.append((cast(str, self.folder), args[1]))
        data: list[object] = []
        for uid in uids:
            emailid, threadid, when = held[uid]
            meta = (
                f"{uid} (UID {uid} EMAILID ({emailid}) THREADID ({threadid}) "
                f'INTERNALDATE "{when}" RFC822.SIZE 60'
            ).encode()
            if "BODY.PEEK[]" in args[1]:
                body = f"Message-ID: <{emailid}@x>\r\n\r\n".encode().ljust(60, b"b")
                data += [(meta + b" BODY[] {60}", body), b")"]
            elif "BODY.PEEK[HEADER" in args[1]:
                header = (
                    f"From: shop@uber.com\r\nTo: {ME}\r\nSubject: s\r\n"
                    f"Message-ID: <{emailid}@x>\r\nDate: Fri, 30 Jan 2026 17:26:16 +0000"
                    "\r\n\r\n"
                ).encode()
                data += [(meta + b" BODY[HEADER.FIELDS] {1}", header), b")"]
            else:
                data.append(meta + b")")
        return "OK", data

    def logout(self) -> tuple[str, Sequence[bytes]]:
        return "BYE", [b""]


def _imap(fake: FakeFastmail) -> imaplib.IMAP4_SSL:
    return cast(imaplib.IMAP4_SSL, fake)


@pytest.fixture(name="account")
def _account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The vishal@enine.com shape: an account folder named for its store, linked into
    ``fastmail/<login>/``; a store of that name; the network refused."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    login = tmp_path / "fastmail" / LOGIN / "imap.json"
    login.parent.mkdir(parents=True)
    login.write_text(json.dumps({"email": LOGIN, "app_password": "pw"}))
    folder = tmp_path / "wicket" / "accounts" / ME
    folder.mkdir(parents=True)
    (folder / "imap.json").symlink_to(login)
    mail = tmp_path / "mail"
    (mail / ME).mkdir(parents=True)
    monkeypatch.setattr(env, "MAIL_ROOT", mail)
    monkeypatch.setattr(root_api, "MAIL_ROOT", mail)
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("no network in tests")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse)
    return folder / "imap.json"


def _connect(monkeypatch: pytest.MonkeyPatch, fake: FakeFastmail) -> list[str]:
    """Every connect goes to ``fake``; returns the hosts asked for."""
    hosts: list[str] = []

    def connect(host: str, *_args: object, **_kwargs: object) -> FakeFastmail:
        hosts.append(host)
        return fake

    monkeypatch.setattr(imaplib, "IMAP4_SSL", connect)
    return hosts


# --- which provider: where imap.json links -------------------------------------


def test_the_link_target_names_the_provider(tmp_path: Path) -> None:
    for provider in (GMAIL, FASTMAIL):
        link = tmp_path / provider.name / "acct" / "imap.json"
        link.parent.mkdir(parents=True)
        link.symlink_to(
            tmp_path / ".config" / provider.name / "you@x.com" / "imap.json"
        )
        assert provider_of(link) is provider  # a target not made yet still names it


@pytest.mark.parametrize("kind", ["real file", "missing", "link elsewhere"])
def test_anything_but_a_provider_link_is_refused(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "acct" / "imap.json"
    path.parent.mkdir()
    if kind == "real file":
        path.write_text("{}")
    elif kind == "link elsewhere":
        path.symlink_to(tmp_path / "secrets" / "you@x.com" / "imap.json")
    with pytest.raises(ValueError, match="no provider is assumed|not a link"):
        provider_of(path)


# --- the dialect -------------------------------------------------------------------


def test_fastmail_reads_rfc_8474_ids() -> None:
    meta = b"1 (UID 1315 EMAILID (Str0hkL1JFLB) THREADID (AlN-Somskwhs))"
    assert FASTMAIL.parse_id(meta) == "Str0hkL1JFLB"
    assert FASTMAIL.parse_thread(meta) == "AlN-Somskwhs"
    assert FASTMAIL.parse_id(b"1 (UID 1 X-GM-MSGID 12)") is None


def test_fastmail_names_a_file_by_a_lowercase_digest_of_its_id() -> None:
    upper, lower = FASTMAIL.file_stem("AbCdEf123456"), FASTMAIL.file_stem(
        "abcdef123456"
    )
    assert upper != lower and upper == upper.lower() and len(upper) == 16


def test_fastmail_orders_by_internaldate_then_id() -> None:
    later = FASTMAIL.order("A", "2026-02-01T00:00:00+00:00")
    earlier = FASTMAIL.order("Z", "2026-01-01T00:00:00+00:00")
    assert earlier < later


def test_fastmail_searches_by_date_and_refuses_gmail_search() -> None:
    assert FASTMAIL.year_search(2025) == ("SINCE", "1-Jan-2025", "BEFORE", "1-Jan-2026")
    today = date(2026, 3, 31)
    assert FASTMAIL.since_search("2d", today) == ("SINCE", "29-Mar-2026")
    assert FASTMAIL.since_search("1m", today) == ("SINCE", "28-Feb-2026")
    assert FASTMAIL.since_search("1y", today) == ("SINCE", "31-Mar-2025")
    with pytest.raises(ValueError, match="takes no --query or --domains"):
        FASTMAIL.raw_search("from:acme.com")


def test_a_staged_name_reads_back_as_the_provider_that_wrote_it() -> None:
    when = datetime(2026, 1, 30, 17, 26, 16, tzinfo=timezone.utc)
    for provider, thread, msgid in [
        (GMAIL, "1765", "18f2"),
        (FASTMAIL, "AlN-Somskwhs", "Str0hkL1JFLB"),
    ]:
        name = provider.staged_name(thread, msgid, when)
        staged = parse_staged(name)
        assert staged is not None and staged.provider is provider
        assert (staged.thread_id, staged.msgid, staged.internal_dt) == (
            thread,
            msgid,
            when,
        )
    fastmail = FASTMAIL.staged_name("AlN-Somskwhs", "Str0hkL1JFLB", when)
    ids = fastmail.split("@", maxsplit=1)[0]
    assert fastmail.startswith("fastmail/") and ids == ids.lower()  # case-safe
    assert parse_staged("fastmail/zz/00@20260130T172616Z.eml") is None  # not hex
    assert (
        parse_staged("fastmail/2a/2a@20260130T172616Z.eml") is None
    )  # "*" no objectid


# --- the folders -------------------------------------------------------------------


def test_the_walk_skips_trash_spam_notes_and_unselectable_folders() -> None:
    walked = auth.folders(_imap(FakeFastmail()), FASTMAIL)
    assert walked == ["INBOX", "Archive", "Receipts/Uber", "Grand Canyon"]
    assert auth.folders(_imap(FakeFastmail()), GMAIL) == ["[Gmail]/All Mail"]


def test_every_folder_is_selected_read_only() -> None:
    fake = FakeFastmail()
    auth.select_folder(_imap(fake), "Receipts/Uber")
    assert fake.selects == [('"Receipts/Uber"', True)]


def test_the_first_year_is_the_lowest_across_folders() -> None:
    fake = FakeFastmail()
    assert auth.first_year(_imap(fake), FASTMAIL) == 2025
    assert all(readonly for _name, readonly in fake.selects)


def test_open_mailbox_connects_to_fastmail_and_examines_inbox(
    account: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeFastmail()
    hosts = _connect(monkeypatch, fake)
    auth.open_mailbox(account, interactive=False, provider=FASTMAIL)
    assert hosts == ["imap.fastmail.com"] and fake.selects == [('"INBOX"', True)]


def test_a_server_without_objectid_is_refused_with_no_halt(
    account: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connect(monkeypatch, FakeFastmail(capabilities=b"IMAP4rev1 SPECIAL-USE"))
    with pytest.raises(auth.AuthError, match="does not announce OBJECTID"):
        auth.open_mailbox(account, interactive=False, provider=FASTMAIL)
    assert not (Path(os.environ["XDG_STATE_HOME"]) / "wicket" / "halt" / ME).exists()


# --- catalog: every folder, one row per EMAILID ------------------------------------


def test_catalog_keeps_one_row_per_emailid_labelled_with_its_folders(
    account: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connect(monkeypatch, FakeFastmail())
    rows, stats = catalog_lib._sweep_year(
        credentials_path=account, mailbox=None, year=2026, provider=FASTMAIL
    )
    assert [(r["msgid"], r["labels"]) for r in rows] == [
        ("Str0hkL1JFLB", ["Archive", "INBOX"])
    ]
    assert stats["messages"] == 1  # the Trash copy is never walked


@pytest.mark.usefixtures("account")
def test_catalog_refuses_mailbox_on_fastmail(
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    code, _out, err = run_cli(
        root.main, ["catalog", "--mail-account", ME, "--mailbox", "INBOX"]
    )
    assert code == 2 and "--mailbox is refused" in err


# --- fetch: every folder, one download per EMAILID ---------------------------------


def _ctx() -> fetch_lib.ThreadContext:
    mail = env.MAIL_ROOT / ME
    return fetch_lib.ThreadContext(
        dest=mail / "archive",
        account=ME,
        domain_aliases={},
        dry_run=False,
        store_dir=mail / "manifest",
        staging_dir=mail / "staging",
        duplicates_dir=env.MAIL_ROOT / ".duplicates" / ME,
        provider=FASTMAIL,
    )


def test_fetch_finds_a_message_in_two_folders_once(account: Path) -> None:
    records, total, unheld = fetch_lib._discover_records(
        account,
        FASTMAIL.year_search(2026),
        set(),
        None,
        conn=_imap(FakeFastmail()),
        provider=FASTMAIL,
    )
    assert (total, unheld) == (2, 2)  # Str0hkL1JFLB once, StqfklOovjb- once
    assert sorted((r[1], r[8]) for r in records) == [
        ("StqfklOovjb-", "Archive"),
        ("Str0hkL1JFLB", "INBOX"),  # found first in INBOX, not again in Archive
    ]


def test_fetch_stages_each_body_under_fastmail_from_its_own_folder(
    account: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeFastmail()
    _connect(monkeypatch, fake)
    stats = fetch_lib.download(
        search=FASTMAIL.year_search(2026),
        ctx=_ctx(),
        credentials_path=account,
        limit=None,
        threads=1,
    )
    assert stats["downloaded"] == 2 and not stats["halted"]
    staged = sorted(
        p.relative_to(env.MAIL_ROOT / ME / "staging").as_posix()
        for p in (env.MAIL_ROOT / ME / "staging").rglob("*.eml")
    )
    read = [parse_staged(s) for s in staged]
    assert sorted(r.msgid for r in read if r is not None) == [
        "StqfklOovjb-",
        "Str0hkL1JFLB",
    ]
    assert all(s.startswith("fastmail/") for s in staged)
    bodies = [(f, items) for f, items in fake.fetches if "BODY.PEEK[]" in items]
    assert sorted(f for f, _items in bodies) == ["Archive", "INBOX"]


@pytest.mark.usefixtures("account")
def test_fetch_refuses_gmail_search_on_fastmail(
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    for flags in (["--domains", "acme.com"], ["--query", "from:acme.com"]):
        code, _out, err = run_cli(root.main, ["fetch", "--mail-account", ME, *flags])
        assert code == 2 and "Fastmail takes no --query or --domains" in err


@pytest.mark.usefixtures("account")
def test_fetch_last_on_fastmail_is_a_since_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_download(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {**fetch_lib._counts(), "passes": [], "halted": False}

    monkeypatch.setattr(root_api, "download", fake_download)
    root_api.fetch(ME, options=root_api.FetchOptions(last="2d", dry_run=True))
    search = cast(tuple[str, ...], seen["search"])
    assert search[0] == "SINCE" and len(search) == 2
    assert cast(fetch_lib.ThreadContext, seen["ctx"]).provider is FASTMAIL


def test_a_real_file_imap_json_is_refused_before_any_login(
    account: Path, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    target = account.resolve()
    account.unlink()
    account.write_text(target.read_text())  # a real file: it names no provider
    code, _out, err = run_cli(root.main, ["fetch", "--mail-account", ME])
    assert code == 2 and "not a link" in err


# --- ingest --staged: what fetch staged, filed by its provider ---------------------


@pytest.mark.usefixtures("account")
def test_ingest_files_a_staged_fastmail_body_by_its_digest_name() -> None:
    when = datetime(2026, 1, 30, 17, 26, 16, tzinfo=timezone.utc)
    rel = FASTMAIL.staged_name("AlN-Somskwhs", "Str0hkL1JFLB", when)
    staged = env.MAIL_ROOT / ME / "staging" / rel
    staged.parent.mkdir(parents=True)
    staged.write_bytes(
        b"Message-ID: <m1@uber.com>\r\nFrom: shop@uber.com\r\nTo: you@enine.com\r\n"
        b"Date: Fri, 30 Jan 2026 17:26:16 +0000\r\nSubject: s\r\n\r\nbody\r\n"
    )
    root_api.ingest(ME, options=root_api.IngestOptions(staged=True))
    rows: dict[str, Row] = load_store(env.MAIL_ROOT / ME / "manifest")
    row = rows["m1@uber.com"]
    assert (row["msgid"], row["thread_id"]) == ("Str0hkL1JFLB", "AlN-Somskwhs")
    stem = FASTMAIL.file_stem("Str0hkL1JFLB")
    assert str(row["path"]).endswith(f"/2026-01/{stem}.eml")
    assert (env.MAIL_ROOT / ME / "archive" / str(row["path"])).exists()
    assert not staged.exists()
