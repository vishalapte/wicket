"""The store noun: each verb returns the type it declares, publishes its schema, and writes
domain-routes.json (`--from`) and account-aliases.json (`--to`) both or neither.

What every node shares (bare invocation, argument errors) is tested once, over all nodes,
in ``test_cli.py``. This file holds what only store knows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_type_hints

import pytest

import wicket.schema
from wicket import env
from wicket.domains import load_domain_routes
from wicket.errors import ApiError
from wicket.store import __main__ as cli
from wicket.store import api

# One real call per verb, for the test that checks each result against its type.
SAMPLES: dict[str, dict[str, Any]] = {
    "create": {"store": "travel", "from_": ["ba.com"], "to": ["ba@xcv.org"]},
    "delete": {"store": "travel", "from_": ["aa.com"]},
    "list": {},
}


@pytest.fixture(name="root")
def _root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A mail root where travel takes aa.com and aa@xcv.org, and shopping takes ikea.com."""
    monkeypatch.setattr(env, "MAIL_ROOT", tmp_path)
    (tmp_path / "domain-routes.json").write_text(
        json.dumps({"travel": ["aa.com"], "shopping": ["ikea.com"]}), encoding="utf-8"
    )
    (tmp_path / "account-aliases.json").write_text(
        json.dumps({"travel": ["aa@xcv.org"]}), encoding="utf-8"
    )
    return tmp_path


def _files(root: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    def read(name: str) -> dict[str, list[str]]:
        loaded: dict[str, list[str]] = json.loads(
            (root / name).read_text(encoding="utf-8")
        )
        return loaded

    return read("domain-routes.json"), read("account-aliases.json")


@pytest.mark.parametrize("verb", sorted(api.VERBS))
def test_store_verbs_return_the_type_they_declare(verb: str, root: Path) -> None:
    del root
    fn = api.VERBS[verb]
    result = fn(**SAMPLES[verb])
    assert wicket.schema.conforms(result, get_type_hints(fn)["return"]) == []


def test_store_output_publishes_one_schema_per_verb() -> None:
    phases = sorted(str(params["phase"]) for params, _ in cli.output())
    assert phases == sorted(api.VERBS)


def test_create_adds_to_an_existing_store_in_both_files(root: Path) -> None:
    result = api.create_store("travel", from_=["ba.com"], to=["ba@xcv.org"])
    assert result == {
        "store": "travel",
        "domains": ["aa.com", "ba.com"],
        "addresses": ["aa@xcv.org", "ba@xcv.org"],
    }
    routes, accounts = _files(root)
    assert routes["travel"] == ["aa.com", "ba.com"]
    assert accounts["travel"] == ["aa@xcv.org", "ba@xcv.org"]
    assert load_domain_routes(root / "domain-routes.json")["ba.com"] == "travel"
    assert env.load_account_aliases()["ba@xcv.org"] == "travel"


def test_only_the_files_a_flag_names_are_written(root: Path) -> None:
    accounts_before = (root / "account-aliases.json").read_bytes()
    api.create_store("shopping", from_=["llbean.com"])
    assert (root / "account-aliases.json").read_bytes() == accounts_before


def test_a_domain_already_routed_elsewhere_refuses_and_writes_neither(
    root: Path,
) -> None:
    before = _files(root)
    with pytest.raises(ApiError, match="aliased to both"):
        api.create_store("shopping", from_=["aa.com"], to=["shop@xcv.org"])
    assert _files(root) == before


def test_an_address_already_in_another_store_refuses_and_writes_neither(
    root: Path,
) -> None:
    before = _files(root)
    with pytest.raises(ApiError, match="aliased to both"):
        api.create_store("shopping", from_=["llbean.com"], to=["aa@xcv.org"])
    assert _files(root) == before


def test_an_explicit_domain_and_another_stores_wildcard_coexist(root: Path) -> None:
    api.create_store("travel", from_=["*.delta.com"])
    api.create_store("shopping", from_=["t.delta.com"])
    routes = load_domain_routes(root / "domain-routes.json")
    assert (routes["*.delta.com"], routes["t.delta.com"]) == ("travel", "shopping")


@pytest.mark.parametrize("fn", [api.create_store, api.delete_store])
def test_neither_from_nor_to_refuses(fn: Any, root: Path) -> None:
    before = _files(root)
    with pytest.raises(ApiError, match="at least one --from"):
        fn("travel")
    assert _files(root) == before


def test_an_invalid_store_is_refused(root: Path) -> None:
    with pytest.raises(ApiError, match="invalid primary"):
        api.create_store("Not A Store", from_=["x.com"])
    assert "Not A Store" not in _files(root)[0]


def test_delete_removes_and_skips_what_is_already_gone(root: Path) -> None:
    result = api.delete_store("travel", from_=["aa.com", "gone.com"], to=["aa@xcv.org"])
    assert result == {"store": "travel", "domains": [], "addresses": []}
    assert _files(root) == ({"shopping": ["ikea.com"]}, {})
    api.delete_store("travel", from_=["aa.com"])  # a re-run changes nothing
    assert _files(root) == ({"shopping": ["ikea.com"]}, {})


def test_list_merges_both_files_by_store(root: Path) -> None:
    del root
    assert api.list_store() == {
        "stores": [
            {"store": "shopping", "domains": ["ikea.com"], "addresses": []},
            {"store": "travel", "domains": ["aa.com"], "addresses": ["aa@xcv.org"]},
        ]
    }


def test_the_account_aliases_file_override_is_honoured(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "elsewhere" / "aliases.json"
    custom.parent.mkdir()
    monkeypatch.setenv(env.ACCOUNT_ALIASES_FILE_ENV_VAR, str(custom))
    api.create_store("travel", to=["ua@xcv.org"])
    assert json.loads(custom.read_text()) == {"travel": ["ua@xcv.org"]}
    assert _files(root)[1] == {"travel": ["aa@xcv.org"]}


def test_from_and_to_repeat_on_the_command_line(root: Path, run_cli: Any) -> None:
    code, out, err = run_cli(
        cli.main,
        [
            "create",
            "--store",
            "shopping",
            "--from",
            "a.com",
            "--from",
            "b.com",
            "--to",
            "s@xcv.org",
        ],
    )
    assert (code, err) == (0, "")
    assert out == "shopping\n  from: a.com, b.com, ikea.com\n  to:   s@xcv.org\n"
    assert _files(root)[0]["shopping"] == ["a.com", "b.com", "ikea.com"]


def test_a_refusal_prints_on_stderr_and_exits_2(root: Path, run_cli: Any) -> None:
    del root
    code, out, err = run_cli(cli.main, ["delete", "--store", "travel"])
    assert (code, out) == (2, "")
    assert "at least one --from" in err
