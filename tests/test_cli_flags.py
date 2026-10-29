"""Regression coverage for the cross-repo CLI vocabulary normalization pass.

Pins three renames: ``--account`` -> ``--mail-account`` (catalog/fetch/report/
ingest), ``--tags`` (comma-separated) -> ``--tag`` (repeatable) on ingest, and
``fetch --dest`` -> ``--target``. See wicket#2, wicket#3, wicket#4.
"""

from __future__ import annotations

import pytest

import wicket.__main__ as root

_BASE_ARGV = {
    "catalog": [],
    "report": [],
    "fetch": ["--domains", "acme.com"],
    "ingest": ["--src", "x"],
}


@pytest.mark.parametrize(
    "verb",
    ["catalog", "fetch", "report", "ingest"],
)
def test_mail_account_flag_resolves_on_every_verb(verb: str) -> None:
    args = root.parser().parse_args(
        [verb, *_BASE_ARGV[verb], "--mail-account", "you@example.com"]
    )
    assert args.mail_account == "you@example.com"


@pytest.mark.parametrize(
    "verb",
    ["catalog", "fetch", "report", "ingest"],
)
def test_bare_account_flag_no_longer_exists(verb: str) -> None:
    with pytest.raises(SystemExit):
        root.parser().parse_args(
            [verb, *_BASE_ARGV[verb], "--account", "you@example.com"]
        )


def test_tag_is_repeatable_not_comma_separated() -> None:
    args = root.parser().parse_args(
        ["ingest", "--src", "x", "--tag", "receipts", "--tag", "2026"]
    )
    assert args.tag == ["receipts", "2026"]


def test_tag_defaults_to_empty_list() -> None:
    args = root.parser().parse_args(["ingest", "--src", "x"])
    assert args.tag == []


def test_bare_tags_flag_no_longer_exists() -> None:
    with pytest.raises(SystemExit):
        root.parser().parse_args(["ingest", "--src", "x", "--tags", "a,b"])


def test_fetch_target_flag_resolves() -> None:
    args = root.parser().parse_args(
        ["fetch", "--domains", "acme.com", "--target", "out"]
    )
    assert str(args.target) == "out"


def test_fetch_bare_dest_flag_no_longer_exists() -> None:
    with pytest.raises(SystemExit):
        root.parser().parse_args(["fetch", "--domains", "acme.com", "--dest", "out"])
