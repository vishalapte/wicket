"""The alias noun: each verb returns the type it declares, publishes its schema, and keeps
domain-aliases.json a file its reader accepts.

What every node shares (bare invocation, argument errors) is tested once, over all nodes,
in ``test_cli.py``. This file holds what only alias knows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_type_hints

import pytest

import wicket.schema
from wicket import env
from wicket.alias import __main__ as cli
from wicket.alias import api
from wicket.domains import load_domain_aliases
from wicket.errors import ApiError

# One real call per verb, for the test that checks each result against its type.
SAMPLES: dict[str, dict[str, Any]] = {
    "create": {"primary": "united.com", "item": ["*.united.com"]},
    "delete": {"primary": "delta.com", "item": ["t.delta.com"]},
    "list": {},
    "update": {"primary": "delta.com", "add": ["delta.net"], "remove": []},
}


@pytest.fixture(name="root")
def _root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A mail root holding one alias entry: delta.com <- *.delta.com, t.delta.com."""
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "domain-aliases.json").write_text(
        json.dumps({"delta.com": ["*.delta.com", "t.delta.com"]}), encoding="utf-8"
    )
    return tmp_path


def _file(root: Path) -> dict[str, list[str]]:
    loaded: dict[str, list[str]] = json.loads(
        (root / "domain-aliases.json").read_text(encoding="utf-8")
    )
    return loaded


@pytest.mark.parametrize("verb", sorted(api.VERBS))
def test_alias_verbs_return_the_type_they_declare(verb: str, root: Path) -> None:
    del root
    fn = api.VERBS[verb]
    result = fn(**SAMPLES[verb])
    assert wicket.schema.conforms(result, get_type_hints(fn)["return"]) == []


def test_alias_output_publishes_one_schema_per_verb() -> None:
    phases = sorted(str(params["phase"]) for params, _ in cli.output())
    assert phases == sorted(api.VERBS)


def test_create_writes_an_entry_the_reader_loads(root: Path) -> None:
    api.create_alias("united.com", ["*.united.com", "ual.com"])
    assert load_domain_aliases(root / "domain-aliases.json")["ual.com"] == "united.com"


def test_create_refuses_an_existing_primary_and_writes_nothing(root: Path) -> None:
    before = _file(root)
    with pytest.raises(ApiError, match="already exists"):
        api.create_alias("delta.com", ["x.com"])
    assert _file(root) == before


def test_a_domain_already_folded_elsewhere_is_refused(root: Path) -> None:
    with pytest.raises(ApiError, match="aliased to both"):
        api.create_alias("united.com", ["t.delta.com"])
    assert "united.com" not in _file(root)


def test_an_invalid_domain_is_refused(root: Path) -> None:
    with pytest.raises(ApiError, match="invalid alias domain"):
        api.update_alias("delta.com", add=["not a domain"])


def test_delete_skips_what_is_already_gone(root: Path) -> None:
    api.delete_alias("delta.com", ["t.delta.com", "never-there.com"])
    assert _file(root) == {"delta.com": ["*.delta.com"]}
    assert api.delete_alias("delta.com")["domains"] == []
    assert _file(root) == {}


def test_list_reads_every_entry(root: Path) -> None:
    del root
    assert api.list_alias() == {
        "aliases": [{"primary": "delta.com", "domains": ["*.delta.com", "t.delta.com"]}]
    }


def test_an_absent_mail_root_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path / "not-mounted")
    with pytest.raises(ApiError, match="does not exist"):
        api.list_alias()


def test_item_repeats_on_the_command_line(root: Path, run_cli: Any) -> None:
    code, out, err = run_cli(
        cli.main,
        ["create", "--primary", "aa.com", "--item", "a.aa.com", "--item", "b.aa.com"],
    )
    assert (code, err) == (0, "")
    assert out == "aa.com: a.aa.com, b.aa.com\n"
    assert _file(root)["aa.com"] == ["a.aa.com", "b.aa.com"]
