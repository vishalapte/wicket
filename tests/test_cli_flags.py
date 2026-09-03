"""Regression coverage for the cross-repo CLI vocabulary normalization pass.

Pins three renames: ``--account`` -> ``--mail-account`` (catalog/fetch/report/
ingest), ``--tags`` (comma-separated) -> ``--tag`` (repeatable) on ingest, and
``fetch --dest`` -> ``--target``. See wicket#2, wicket#3, wicket#4.
"""

from __future__ import annotations

import pytest

from wicket.catalog import cli as catalog_cli
from wicket.fetch import cli as fetch_cli
from wicket.ingest import cli as ingest_cli
from wicket.report import cli as report_cli

_BASE_ARGV = {
    catalog_cli: [],
    report_cli: [],
    fetch_cli: ["--domains", "acme.com"],
    ingest_cli: ["--src", "x"],
}


@pytest.mark.parametrize(
    "cli",
    [catalog_cli, fetch_cli, report_cli, ingest_cli],
)
def test_mail_account_flag_resolves_on_every_verb(cli) -> None:  # type: ignore[no-untyped-def]
    args = cli.parser().parse_args(
        [*_BASE_ARGV[cli], "--mail-account", "you@example.com"]
    )
    assert args.mail_account == "you@example.com"


@pytest.mark.parametrize(
    "cli",
    [catalog_cli, fetch_cli, report_cli, ingest_cli],
)
def test_bare_account_flag_no_longer_exists(cli) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit):
        cli.parser().parse_args([*_BASE_ARGV[cli], "--account", "you@example.com"])


def test_tag_is_repeatable_not_comma_separated() -> None:
    args = ingest_cli.parser().parse_args(
        ["--src", "x", "--tag", "receipts", "--tag", "2026"]
    )
    assert args.tag == ["receipts", "2026"]


def test_tag_defaults_to_empty_list() -> None:
    args = ingest_cli.parser().parse_args(["--src", "x"])
    assert args.tag == []


def test_bare_tags_flag_no_longer_exists() -> None:
    with pytest.raises(SystemExit):
        ingest_cli.parser().parse_args(["--src", "x", "--tags", "a,b"])


def test_fetch_target_flag_resolves() -> None:
    args = fetch_cli.parser().parse_args(["--domains", "acme.com", "--target", "out"])
    assert str(args.target) == "out"


def test_fetch_bare_dest_flag_no_longer_exists() -> None:
    with pytest.raises(SystemExit):
        fetch_cli.parser().parse_args(["--domains", "acme.com", "--dest", "out"])
