"""The shapes the wicket api returns: one TypedDict per verb, and its rows.

The one home of every wicket result shape. The api returns them, the renderers read
them, and ``output()`` publishes them as JSON Schema; nothing restates them. They live in
``lib`` so the api and the renderers can both import them while nothing in ``lib``
imports the api. A result holds JSON types only: a date is an ISO string, an exact
amount a decimal string, never a float.

A key a result carries only in some cases is ``NotRequired``. This module does not
defer its annotations (no ``from __future__ import annotations``): a TypedDict class
reads ``NotRequired`` only from an evaluated annotation, and would otherwise require
every key.
"""

from typing import Literal, NotRequired, TypedDict


class DomainCount(TypedDict):
    """One domain and how many messages it holds, most first."""

    domain: str
    count: int


class ReportCounts(TypedDict):
    """The one-screen summary of one store's manifest."""

    messages: int
    downloaded: int
    downloaded_gone: int
    downloaded_present: int
    observed_only: int
    excluded: int
    senders: int
    addresses: int


class SenderRow(TypedDict):
    """One From address and its message count."""

    address: str
    count: int


class BucketStore(TypedDict):
    """One store's share of a bucket: the messages it claims, those held, by domain."""

    account: str
    messages: int
    held: int
    domains: list[DomainCount]


class BucketView(TypedDict):
    """Every store's share of a bucket, and the total."""

    name: str
    stores: list[BucketStore]
    total: int


class YearCount(TypedDict):
    """One account's messages in one year: held locally, and still to get."""

    year: int
    local: int
    not_local: int


class AccountYears(TypedDict):
    """One account's row of the by-year view, and its totals."""

    account: str
    years: list[YearCount]
    local: int
    not_local: int


class ByYearView(TypedDict):
    """Every account by year: the years any account has, each account's row, the totals."""

    years: list[int]
    accounts: list[AccountYears]
    local: int
    not_local: int


class AccountCounts(TypedDict):
    """One account's messages: downloaded, excluded, not downloaded, and their total."""

    account: str
    downloaded: int
    excluded: int
    not_downloaded: int
    total: int


class AccountsView(TypedDict):
    """Every account's counts, and the totals across them."""

    accounts: list[AccountCounts]
    downloaded: int
    excluded: int
    not_downloaded: int
    total: int


class CatalogRoot(TypedDict):
    """What ``wicket catalog`` returns: the sweep's counts, and where it wrote.

    ``account_linked`` is false when the account has no folder in
    ~/.config/wicket/accounts: nothing ran, and the result carries no other key but
    ``dry_run``.
    """

    account_linked: bool
    dry_run: bool
    store_dir: NotRequired[str]
    messages: NotRequired[int]
    shards: NotRequired[int]
    removed: NotRequired[int]
    boundary_drops: NotRequired[int]
    parse_skips: NotRequired[int]
    label_misses: NotRequired[int]


# One pass of a fetch run. Spelled as a call, not a class: ``pass`` is a keyword.
PassRow = TypedDict(
    "PassRow",
    {
        "year": int | None,
        "pass": int,
        "matched": int,
        "got": int,
        "candidates": int,
        "pending": int,
        "linked": int,
        "downloaded": int,
        "failed": int,
        "got_total": NotRequired[int],
    },
)


class FetchRoot(TypedDict):
    """What ``wicket fetch`` returns: the run's counts, one row per pass, and its HALT.

    ``filtered`` is true for a ``--domains`` or ``--query`` run (one pass, ``year``
    null), false for a run over every year. ``account`` is the account as named, or
    null. A returned result never has ``halted`` true: a run that halts is refused. As
    for catalog, ``account_linked`` false carries no other key but ``dry_run``.
    """

    account_linked: bool
    dry_run: bool
    filtered: NotRequired[bool]
    account: NotRequired[str | None]
    matched: NotRequired[int]
    got: NotRequired[int]
    candidates: NotRequired[int]
    pending: NotRequired[int]
    linked: NotRequired[int]
    downloaded: NotRequired[int]
    failed: NotRequired[int]
    passes: NotRequired[list[PassRow]]
    halted: NotRequired[bool]
    halt: NotRequired[str | None]
    halt_file: NotRequired[str | None]


class IngestStore(TypedDict):
    """One store's share of an ingest: what was filed there, and where."""

    account: str
    added: int
    unfiled: int
    added_by_year: dict[str, int]
    top_domains: list[DomainCount]
    by_rule: dict[str, int]
    filed: dict[str, list[str]]
    skipped: int
    excluded: int
    excluded_lists: int


class BusyStore(TypedDict):
    """A store a routed ingest skipped: another process holds its lock."""

    account: str
    pid: str
    messages: int


class IngestRoot(TypedDict):
    """What ``wicket ingest`` returns: the mode it ran in, and that mode's stats.

    Every mode carries ``added``, ``excluded``, ``excluded_lists`` (mail sent via a
    mailing list the store excludes, skipped and left where it is) and ``filed``
    (archive dir -> the ``.eml`` filed there). ``staged`` and ``single`` carry one
    store's counts at the top (``account`` ... ``by_rule``); ``routed`` carries them
    per store in ``accounts``. ``added_by_year`` is keyed by the year as a string. A returned
    result never names a busy store: a routed run that skips one is refused.
    """

    mode: Literal["staged", "routed", "single"]
    dry_run: bool
    to_trash: bool
    added: int
    excluded: int
    excluded_lists: int
    filed: dict[str, list[str]]
    account: NotRequired[str]
    unfiled: NotRequired[int]
    added_by_year: NotRequired[dict[str, int]]
    top_domains: NotRequired[list[DomainCount]]
    by_rule: NotRequired[dict[str, int]]
    skipped: NotRequired[int]
    warning: NotRequired[str | None]
    files: NotRequired[int]
    unique: NotRequired[int]
    dup: NotRequired[int]
    accounts: NotRequired[list[IngestStore]]
    unrouted: NotRequired[int]
    unrouted_threads: NotRequired[int]
    busy: NotRequired[list[BusyStore]]
    trashable: NotRequired[int]
    trashed: NotRequired[int]
    staged: NotRequired[int]
    archived: NotRequired[int]
    duplicates: NotRequired[int]
    linked: NotRequired[int]
    refused: NotRequired[list[str]]
    unrecognized: NotRequired[list[str]]


class ReportRoot(TypedDict):
    """What ``wicket report`` returns: one view, and only that view's key.

    ``summary`` carries ``store_dir`` and ``counts``, ``senders`` its rows, ``addresses``
    its list, ``bucket`` the bucket's view, ``by_year`` every account by year,
    ``accounts`` every account's counts.
    """

    view: Literal["summary", "senders", "addresses", "bucket", "by_year", "accounts"]
    store_dir: NotRequired[str]
    counts: NotRequired[ReportCounts]
    senders: NotRequired[list[SenderRow]]
    addresses: NotRequired[list[str]]
    bucket: NotRequired[BucketView]
    by_year: NotRequired[ByYearView]
    accounts: NotRequired[AccountsView]
