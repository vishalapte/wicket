"""Mailing-list exclusions: the map, its noun, and fetch and ingest consuming it.

``excluded-lists.json`` at the mail root holds ``{"*": [list-id], "<account>": [list-id]}``:
``"*"`` is every account (owner, 2026-10-29). fetch leaves an excluded list out of every
search; ingest skips its mail and leaves the file where it is; catalog and ingest record
each row's ``list_id``, and report leaves out a row whose list is on the map. No test
reaches the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import wicket.__main__ as root
import wicket.exclude.__main__ as exclude_cli
import wicket.root.lib.fetch as fetch_lib
import wicket.root.lib.ingest as ingest_lib
from wicket import env
from wicket.errors import ApiError
from wicket.exclusions import (
    excluded_for,
    excluded_lists_path,
    normalize_list_id,
    parse_excluded_lists,
)
from wicket.manifest import load_store, shard_path, write_shard
from wicket.provider import FASTMAIL, GMAIL
from wicket.root import api as root_api

ME = "you@gmail.com"
LIST = "made-of-meat.googlegroups.com"
Run = Callable[..., tuple[int, str, str]]


@pytest.fixture(name="mail")
def _mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A mail root with ME's store and a ``travel`` bucket; ME's account folder links Gmail."""
    mail = tmp_path / "mail"
    (mail / ME).mkdir(parents=True)
    (mail / "travel").mkdir()
    monkeypatch.setattr(env, "MAIL_ROOT", mail)
    monkeypatch.setattr(root_api, "MAIL_ROOT", mail)
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    folder = tmp_path / "wicket" / "accounts" / ME
    folder.mkdir(parents=True)
    login = tmp_path / "gmail" / ME / "imap.json"
    login.parent.mkdir(parents=True)
    login.write_text(json.dumps({"email": ME, "app_password": "pw"}), encoding="utf-8")
    (folder / "imap.json").symlink_to(login)
    monkeypatch.delenv(env.ACCOUNT_ENV_VAR, raising=False)
    monkeypatch.delenv(env.EXCLUDED_LISTS_FILE_ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    return mail


def _write_map(mail: Path, mapping: dict[str, list[str]]) -> None:
    (mail / "excluded-lists.json").write_text(json.dumps(mapping), encoding="utf-8")


# --- the map ------------------------------------------------------------------------


def test_every_account_and_one_account_add_up() -> None:
    mapping = parse_excluded_lists(
        {"*": [LIST], ME: ["other.example.org", LIST]}, Path("m.json"), [ME]
    )
    assert excluded_for(ME, mapping) == [LIST, "other.example.org"]
    assert excluded_for("someone@else.com", mapping) == [LIST]


@pytest.mark.parametrize(
    "raw,why",
    [
        (
            {"travel": [LIST]},
            "not '\\*' or an account folder",
        ),  # a bucket is no account
        ({"typo@gmail.com": [LIST]}, "not '\\*' or an account folder"),
        ({"*": ["<made-of-meat.googlegroups.com>"]}, "is not a list-id"),
        ({"*": ["two words"]}, "is not a list-id"),
        ({"*": LIST}, "must be a list"),
        ([LIST], "top-level value must be an object"),
    ],
)
def test_a_malformed_map_refuses(raw: object, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        parse_excluded_lists(raw, Path("m.json"), [ME])


def test_a_list_id_header_reads_as_its_bracketed_id() -> None:
    assert normalize_list_id(f"Made of meat <{LIST.upper()}>") == LIST
    assert normalize_list_id(LIST) == LIST
    assert normalize_list_id("") == ""


def test_each_provider_leaves_the_list_out_of_its_search() -> None:
    assert GMAIL.exclude_list(LIST) == ("NOT", "X-GM-RAW", f'"list:{LIST}"')
    assert FASTMAIL.exclude_list(LIST) == ("NOT", "HEADER", "List-Id", f'"<{LIST}>"')


# --- the noun -----------------------------------------------------------------------


def test_create_without_an_account_excludes_for_every_account(
    mail: Path, run_cli: Run
) -> None:
    code, out, _err = run_cli(exclude_cli.main, ["create", "--list", LIST])
    assert code == 0 and out == f"*: {LIST}\n"
    code, out, _err = run_cli(
        exclude_cli.main, ["create", "--list", "x.example.org", "--mail-account", ME]
    )
    assert code == 0 and out == f"{ME}: x.example.org\n"
    stored = json.loads(excluded_lists_path(mail).read_text(encoding="utf-8"))
    assert stored == {"*": [LIST], ME: ["x.example.org"]}
    code, out, _err = run_cli(exclude_cli.main, ["list"])
    assert out == f"*: {LIST}\n{ME}: x.example.org\n"
    code, out, _err = run_cli(exclude_cli.main, ["delete", "--list", LIST])
    assert code == 0 and out == "*: (none)\n"
    assert json.loads(excluded_lists_path(mail).read_text(encoding="utf-8")) == {
        ME: ["x.example.org"]
    }


@pytest.mark.usefixtures("mail")
def test_create_for_a_store_with_no_account_folder_refuses(run_cli: Run) -> None:
    code, _out, err = run_cli(
        exclude_cli.main, ["create", "--list", LIST, "--mail-account", "travel"]
    )
    assert code == 2 and "not '*' or an account folder" in err


# --- fetch --------------------------------------------------------------------------


def _fetched(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def fake_download(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {**fetch_lib._counts(), "passes": [], "halted": False}

    monkeypatch.setattr(root_api, "load_credentials", lambda *_a, **_k: (ME, "pw"))
    monkeypatch.setattr(root_api, "download", fake_download)
    return seen


def test_fetch_carries_every_and_own_exclusions(
    mail: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_map(mail, {"*": [LIST], ME: ["x.example.org"]})
    seen = _fetched(monkeypatch)
    root_api.fetch(ME, options=root_api.FetchOptions(last="2d", dry_run=True))
    ctx = cast(fetch_lib.ThreadContext, seen["ctx"])
    assert ctx.exclude == GMAIL.exclude_list(LIST) + GMAIL.exclude_list("x.example.org")


def test_fetch_with_no_map_searches_as_before(
    mail: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not (mail / "excluded-lists.json").exists()
    seen = _fetched(monkeypatch)
    root_api.fetch(ME, options=root_api.FetchOptions(last="2d", dry_run=True))
    assert cast(fetch_lib.ThreadContext, seen["ctx"]).exclude == ()


def test_every_pass_appends_the_exclusion_to_its_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    searched: list[tuple[str, ...]] = []

    def discover(
        _creds: Path, search: tuple[str, ...], *_a: object, **_k: object
    ) -> tuple[list[object], int, int]:
        searched.append(search)
        return [], 0, 0

    ctx = fetch_lib.ThreadContext(
        dest=tmp_path,
        account=ME,
        domain_aliases={},
        dry_run=True,
        exclude=GMAIL.exclude_list(LIST),
    )
    monkeypatch.setattr(fetch_lib, "_discover_records", discover)
    fetch_lib._pass(
        conn=None,
        search=GMAIL.year_search(2026),
        ctx=ctx,
        credentials_path=tmp_path / "imap.json",
        limit=None,
        threads=1,
    )
    assert searched == [GMAIL.year_search(2026) + GMAIL.exclude_list(LIST)]


def test_a_malformed_map_refuses_the_fetch(mail: Path, run_cli: Run) -> None:
    _write_map(mail, {"travel": [LIST]})
    code, _out, err = run_cli(
        root.main, ["fetch", "--mail-account", ME, "--last", "2d"]
    )
    assert code == 2 and "not '*' or an account folder" in err


# --- ingest -------------------------------------------------------------------------


def _eml(mid: str, list_id: str | None = None) -> bytes:
    header = f"List-Id: Made of meat <{list_id}>\r\n" if list_id else ""
    return (
        f"Message-ID: <{mid}>\r\nFrom: someone@acme.com\r\nTo: {ME}\r\n{header}"
        "Date: Mon, 06 Jul 2026 09:00:00 +0000\r\nSubject: s\r\n\r\nbody\r\n"
    ).encode()


def test_ingest_staged_leaves_an_excluded_lists_mail_staged(mail: Path) -> None:
    _write_map(mail, {"*": [LIST]})
    staging = mail / ME / "staging" / "1765"
    staging.mkdir(parents=True)
    (staging / "18f2@20260706T090004Z.eml").write_bytes(_eml("a@acme", LIST))
    (staging / "18f3@20260706T090004Z.eml").write_bytes(_eml("b@acme"))
    result = root_api.ingest(ME, options=root_api.IngestOptions(staged=True))
    assert (result["added"], result["excluded_lists"]) == (1, 1)
    assert (staging / "18f2@20260706T090004Z.eml").exists()  # left staged
    assert set(load_store(mail / ME / "manifest")) == {"b@acme"}


def test_ingest_src_files_nothing_of_an_excluded_list_and_trashes_nothing(
    mail: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_map(mail, {ME: [LIST]})
    src = tmp_path / "export"
    src.mkdir()
    (src / "a.eml").write_bytes(_eml("a@acme", LIST))
    (src / "b.eml").write_bytes(_eml("b@acme"))
    trashed: list[Path] = []

    def trash(paths: list[Path]) -> int:
        trashed.extend(paths)
        return len(paths)

    monkeypatch.setattr(ingest_lib, "trash", trash)
    result = root_api.ingest(ME, options=root_api.IngestOptions(src=src, to_trash=True))
    assert (result["added"], result["excluded_lists"]) == (1, 1)
    assert trashed == [src / "b.eml"]  # the excluded list's file stays in the export
    assert set(load_store(mail / ME / "manifest")) == {"b@acme"}


def test_ingest_records_list_id_only_when_the_message_has_one(
    mail: Path, tmp_path: Path
) -> None:
    src = tmp_path / "export"
    src.mkdir()
    (src / "a.eml").write_bytes(_eml("a@acme", LIST))
    (src / "b.eml").write_bytes(_eml("b@acme"))
    root_api.ingest(ME, options=root_api.IngestOptions(src=src, to_trash=False))
    rows = load_store(mail / ME / "manifest")
    assert rows["a@acme"]["list_id"] == LIST
    assert "list_id" not in rows["b@acme"]  # absent, never null


# --- report -------------------------------------------------------------------------


def _rows_on_lists(mail: Path) -> None:
    write_shard(
        shard_path(mail / ME / "manifest", 2026),
        {
            "a@x": {"id": "a@x", "from": "a@acme.com", "list_id": LIST},
            "b@x": {"id": "b@x", "from": "b@acme.com", "list_id": "other.example.org"},
            "c@x": {"id": "c@x", "from": "c@acme.com", "downloaded": True},
        },
    )


def test_report_leaves_out_a_row_whose_list_is_on_the_map_now(mail: Path) -> None:
    _rows_on_lists(mail)
    _write_map(mail, {"*": [LIST]})
    counts = root_api.report(ME)
    assert (counts["messages"], counts["excluded"], counts["observed_only"]) == (
        2,
        1,
        1,
    )
    assert "a@acme.com" not in dict(root_api.sender_counts(ME))
    _write_map(mail, {})  # the list removed: its rows count again, with no catalog
    assert root_api.report(ME)["messages"] == 3


def test_report_refuses_a_malformed_map(mail: Path) -> None:
    _rows_on_lists(mail)
    (mail / "excluded-lists.json").write_text("{", encoding="utf-8")
    with pytest.raises(ApiError):
        root_api.report_root(ME)
