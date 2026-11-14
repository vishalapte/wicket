"""The shared flags: ``--mail-account`` on every verb, and ingest's repeatable ``--tag``."""

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


def test_tag_is_repeatable_not_comma_separated() -> None:
    args = root.parser().parse_args(
        ["ingest", "--src", "x", "--tag", "receipts", "--tag", "2026"]
    )
    assert args.tag == ["receipts", "2026"]


def test_tag_defaults_to_empty_list() -> None:
    args = root.parser().parse_args(["ingest", "--src", "x"])
    assert args.tag == []
