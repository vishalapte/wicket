"""The list-folder map: group addresses whose threads file under ``archive/lists/<name>/``.

``list-folders.json`` at the mail root holds ``{"<name>": [address, ...]}`` (owner,
2026-11-04). The rank that applies it is tested in ``test_filing``; here, the map's own
rule and ingest reading it from the mail root. No test reaches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wicket import env
from wicket.lists import list_folders_path, load_list_folders, parse_list_folders
from wicket.manifest import load_store
from wicket.root import api as root_api

ME = "you@gmail.com"
QSE = "qseprimarycontacts@lists.ercot.com"


@pytest.fixture(name="mail")
def _mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A mail root with ME's store; ME's account folder links Gmail."""
    mail = tmp_path / "mail"
    (mail / ME).mkdir(parents=True)
    monkeypatch.setattr(env, "MAIL_ROOT", mail)
    monkeypatch.setattr(root_api, "MAIL_ROOT", mail)
    monkeypatch.setattr(env, "DEFAULT_STATE_DIR", tmp_path / "wicket")
    folder = tmp_path / "wicket" / "accounts" / ME
    folder.mkdir(parents=True)
    login = tmp_path / "gmail" / ME / "imap.json"
    login.parent.mkdir(parents=True)
    login.write_text(json.dumps({"email": ME, "app_password": "pw"}), encoding="utf-8")
    (folder / "imap.json").symlink_to(login)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    return mail


def test_the_map_reads_address_to_name_lowercased() -> None:
    mapping = parse_list_folders(
        {"qse": ["QSEPrimaryContacts@Lists.ERCOT.com"], "gfem": ["gfem@corp.com"]},
        Path("m.json"),
    )
    assert mapping == {QSE: "qse", "gfem@corp.com": "gfem"}


@pytest.mark.parametrize(
    "raw,why",
    [
        ({"Not A Slug": [QSE]}, "invalid primary list"),
        ({"qse": ["not-an-address"]}, "invalid alias list"),
        ({"qse": [QSE], "other": [QSE]}, "aliased to both"),
        ({"qse": QSE}, "must be a list"),
        ([QSE], "top-level value must be an object"),
    ],
)
def test_a_malformed_map_refuses(raw: object, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        parse_list_folders(raw, Path("m.json"))


def test_an_absent_map_is_empty(tmp_path: Path) -> None:
    assert not load_list_folders(tmp_path / "list-folders.json")


def test_the_variable_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(env.LIST_FOLDERS_ENV_VAR, str(tmp_path / "elsewhere.json"))
    assert list_folders_path(tmp_path / "mail") == tmp_path / "elsewhere.json"


def test_ingest_files_list_mail_under_its_list_folder(
    mail: Path, tmp_path: Path
) -> None:
    (mail / "list-folders.json").write_text(
        json.dumps({"qse": [QSE]}), encoding="utf-8"
    )
    src = tmp_path / "export"
    src.mkdir()
    (src / "a.eml").write_text(
        f"Message-ID: <a@ercot>\nFrom: ops@ercot.com\nTo: {QSE}\n"
        "Date: Mon, 06 Jul 2026 09:00:00 +0000\nSubject: a\n\nbody\n",
        encoding="utf-8",
    )
    root_api.ingest(
        ME, options=root_api.IngestOptions(src=src, to_trash=False, force=True)
    )
    row = load_store(mail / ME / "manifest")["a@ercot"]
    assert (row["domain"], row["filed"]) == ("lists/qse", "list")
    assert (mail / ME / "archive" / str(row["path"])).exists()
    assert str(row["path"]).startswith("lists/qse/2026-07/")
