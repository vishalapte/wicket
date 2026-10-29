"""The wicket api: the one place a request becomes a decision.

Every surface (this noun's cli, an MCP tool, a REST route) calls these functions and
nothing below them. Each verb is one function returning one result type from
``lib.results``, plain JSON. A request the api cannot run raises ``ApiError``.

Each root verb's orchestration is here, moved unchanged from that verb's own api
module, beside the one request function per verb that ``VERBS`` binds: it takes the
command line's arguments, calls the orchestration, says each refusal as an
``ApiError``, and returns the verb's result type.

report: resolve the account, summarize the store. Read-only: no IMAP, no credentials.
Each function resolves the account (`env.resolve_account`), locates its manifest store,
and returns plain data. ``manifest`` is the whole-store read for library consumers.

catalog: resolve, seed credentials, sweep. It resolves the account, the per-account
store and credential paths, seeds credentials (gated on ``interactive``), dispatches into
`lib.catalog.sweep`, and returns the worker's stats dict with the store dir it wrote. A
real sweep holds the store's lock (`env.store_lock`) for its whole run; a dry run writes
nothing and takes none.

fetch: resolve, build query, seed, download. fetch only gets mail into the store's
staging dir; ``ingest --staged`` files it (plan rule 6). It resolves the account and its
per-account archive/staging/store/credential paths, builds the Gmail search expression
from ``domains`` or ``query`` (at most one), seeds credentials (gated on
``interactive``), and dispatches into `lib.fetch.download` (a filter: one pass) or
`lib.fetch.download_years` (no filter: every year until throttled). A real run holds the
store's lock (`env.store_lock`) and appends counts and years, never subjects, addresses
or paths, to the account's log (`env.append_log`: ``log/<account>.log`` under
``$XDG_STATE_HOME/wicket``). The read-side helper `held_messages` (no IMAP, no
credentials) lives here too.

ingest: resolve paths, dispatch to the worker. Read-only on any mailbox: ingest reads
``.eml`` already on disk and files them additively into the manifest + archive, so there
is no IMAP and no credentials to seed (like `report`). Three modes. ``--staged`` files
what ``fetch`` got into one account's ``staging/``: one store, no routing, no account
check, because a mailbox's own mail belongs to that mailbox. A local export (``--src``)
has two, and the default is the safe one:

- **Routed** (no account named): each *thread* goes to the account it was
  addressed to, resolved through the account-alias map and the stores that
  actually exist. Mail nobody claims is left alone. This is the mode that makes a
  mixed folder safe, because no single wrong destination exists to choose.
- **Single-target** (an explicit account): everything lands in that one store.
  ``--mail-account`` is a destination, not a filter, so the worker refuses when the
  folder is plainly addressed to a different account (``force`` overrides).
"""

from __future__ import annotations

import imaplib
import re
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, cast

from wicket.auth import AuthError, Halted, load_credentials
from wicket.domains import (
    DOMAIN_RE,
    domain_aliases_path,
    domain_routes_path,
    load_domain_aliases,
    load_domain_routes,
    route_of,
)
from wicket.env import (
    ALL_MAIL_MAILBOX,
    CREDENTIALS_FILENAME,
    DEFAULT_THREADS,
    MAIL_ROOT,
    NotLinked,
    StoreBusy,
    account_of,
    append_log,
    identities,
    known_accounts,
    load_account_aliases,
    normalize_account,
    resolve_account,
    resolve_archive_dir,
    resolve_duplicates_dir,
    resolve_staging_dir,
    resolve_state_dir,
    resolve_store_dir,
    store_lock,
)
from wicket.errors import ApiError
from wicket.filing import AccountFor, build_domain_query
from wicket.manifest import Row, latest_shard_year, load_store

from .lib import report as report_lib
from .lib.catalog import sweep
from .lib.fetch import RunHooks, ThreadContext, download, download_years
from .lib.ingest import Target, ingest_folder, ingest_routed, ingest_staged
from .lib.renderer.plaintext import busy_lines, fetch_lines, halt_text, ingest_lines
from .lib.results import (
    BucketStore,
    BucketView,
    CatalogRoot,
    FetchRoot,
    IngestRoot,
    PassRow,
    ReportRoot,
)

__all__ = [
    "CatalogOptions",
    "FetchOptions",
    "IngestOptions",
    "all_addresses",
    "bucket_rows",
    "catalog",
    "fetch",
    "held_messages",
    "ingest",
    "manifest",
    "report",
    "sender_counts",
]


def report(account: str | None = None) -> dict[str, int]:
    """One-screen counts for ``account``'s manifest (messages, held, addresses)."""
    return report_lib.summary(resolve_store_dir(resolve_account(account)))


def sender_counts(account: str | None = None) -> list[tuple[str, int]]:
    """``(sender, count)`` for every From address, descending by count."""
    return report_lib.sender_counts(resolve_store_dir(resolve_account(account)))


def all_addresses(account: str | None = None) -> list[str]:
    """Every distinct address seen in From or To, sorted."""
    return report_lib.all_addresses(resolve_store_dir(resolve_account(account)))


def manifest(account: str | None = None) -> dict[str, Row]:
    """The whole manifest for ``account`` (default: the only account folder)."""
    return load_store(resolve_store_dir(resolve_account(account)))


def bucket_rows(name: str) -> dict[str, list[tuple[str, Row]]]:
    """Every message any store holds that ``name`` claims, keyed by store.

    Reads across *all* accounts, not one: a bucket is a topical view over mail
    that physically lives wherever its mailbox put it (`domains.load_domain_routes`
    decides membership; `domains.load_domain_aliases` folds the domain first).
    """
    routes = load_domain_routes(domain_routes_path(MAIL_ROOT))
    aliases = load_domain_aliases(domain_aliases_path(MAIL_ROOT))
    found: dict[str, list[tuple[str, Row]]] = {}
    for account in sorted(known_accounts()):
        rows = report_lib.bucket_rows(resolve_store_dir(account), name, routes, aliases)
        if rows:
            found[account] = rows
    return found


@dataclass(frozen=True)
class CatalogOptions:
    """How to run a catalog sweep, independent of which account.

    A value object the face builds from its input (argv, JSON, a form) and hands
    to `catalog`. With neither ``years`` nor ``full``, the sweep is incremental
    (from the latest year already in the manifest through now); ``full`` forces a
    complete re-sweep from the oldest message. ``interactive`` is deliberately not
    a field: it is an ambient capability of the calling face (is a tty present),
    not part of the request.
    """

    store_dir: Path | None = None
    state_dir: Path | None = None
    mailbox: str = ALL_MAIL_MAILBOX
    years: list[int] | None = None
    dry_run: bool = False
    threads: int = DEFAULT_THREADS
    full: bool = False


def _sweep_years(store_dir: Path, options: CatalogOptions) -> list[int] | None:
    """Decide which years to sweep (the incremental-vs-full policy).

    Explicit ``options.years`` win. ``options.full`` (or a first run with an
    empty store) returns ``None``, letting `lib.sweep` derive the full range from
    the oldest message in the mailbox. Otherwise sweep incrementally: from the
    latest year already in the manifest through the current UTC year. The past
    does not change, so older shards are left untouched and no mailbox scan is
    needed to find the start.
    """
    if options.years is not None:
        return options.years
    if options.full:
        return None
    latest = latest_shard_year(store_dir)
    if latest is None:
        return None
    return list(range(latest, datetime.now(timezone.utc).year + 1))


def catalog(
    account: str | None = None,
    *,
    options: CatalogOptions | None = None,
    interactive: bool = False,
) -> dict[str, int | str]:
    """Sweep ``account``'s mailbox headers into the year-sharded manifest.

    ``options`` (a `CatalogOptions`) carries the run knobs. Resolves the account
    (`env.resolve_account`), the manifest store (``options.store_dir``
    override else ``<mail-root>/<account>/manifest``), and the credential file
    (``<state-dir>/accounts/<account>/imap.json``). Seeds credentials in this thread
    (the per-year workers never prompt). Sweeps incrementally by default (from the
    latest year in the manifest forward), fully when ``options.full`` is set, or
    the explicit ``options.years`` when given; returns `lib.sweep`'s stats dict,
    and ``store_dir``, the manifest store it swept into.
    ``interactive=False`` is the default: a missing credential raises `AuthError`
    instead of blocking on a prompt. Raises `Halted` when a HALT is in force or the
    sweep wrote one, and `env.StoreBusy` when another process holds the store's lock.
    """
    opts = options if options is not None else CatalogOptions()
    resolved = resolve_account(account)
    store = opts.store_dir
    if store is None:
        store = resolve_store_dir(resolved)
    credentials_path = (
        resolve_state_dir(resolved, opts.state_dir) / CREDENTIALS_FILENAME
    )
    load_credentials(credentials_path, interactive=interactive)

    def run() -> dict[str, int | str]:
        stats = sweep(
            store_dir=store,
            credentials_path=credentials_path,
            mailbox=opts.mailbox,
            years=_sweep_years(store, opts),
            dry_run=opts.dry_run,
            threads=opts.threads,
            archive_dir=store.parent / "archive",  # the store's own archive
        )
        return {**stats, "store_dir": str(store)}

    if opts.dry_run:
        return run()
    with store_lock(store):
        return run()


# Why ``--target`` / ``FetchOptions.dest`` is refused (the fetch/ingest split).
DEST_REFUSED = (
    "fetch no longer files mail, so it takes no destination (--target, "
    "FetchOptions.dest): it gets bodies into <mail-root>/<store>/staging/, and "
    "`wicket ingest --staged --mail-account <account>` files them into the archive."
)


@dataclass(frozen=True)
class FetchOptions:
    """What to fetch, independent of which account.

    At most one of ``domains`` (list or comma-string) or ``query`` (a raw Gmail
    expression) may be set; the invariant is enforced here so a face cannot build
    an ambiguous request. Neither means every message not got and not excluded.
    ``dest`` is refused (`DEST_REFUSED`): fetch stages and files nothing, so a
    destination would be silently ignored.
    ``limit`` bounds the candidates of the whole run. ``interactive`` is
    deliberately not a field: it is a capability of the calling face, not part of
    the request.
    """

    domains: list[str] | str | None = None
    query: str | None = None
    dest: Path | None = None
    state_dir: Path | None = None
    alias_file: Path | None = None
    limit: int | None = None
    threads: int = DEFAULT_THREADS
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.domains is not None and self.query is not None:
            raise ValueError("pass at most one of domains=... or query=...")
        if self.dest is not None:
            raise ValueError(DEST_REFUSED)


def _domain_query(
    domains: list[str] | str | None,
    query: str | None,
    domain_aliases: dict[str, str],
) -> str | None:
    """Turn the options' filter into a Gmail search expression, or None for no filter.

    At most one of ``domains`` / ``query`` is set (`FetchOptions`). A raw ``query``
    passes through; ``domains`` (list or comma-string) expands via the alias map.
    Raises ``ValueError`` if a domains filter cleans to empty.
    """
    if query is not None:
        return query
    if domains is None:
        return None
    items = domains.split(",") if isinstance(domains, str) else list(domains or [])
    names = [d.strip() for d in items if d.strip()]
    if not names:
        raise ValueError("domains was empty")
    return build_domain_query(names, domain_aliases)


def _run_log(account: str) -> Callable[[str], None]:
    """Append one timestamped line to the account's log (`env.append_log`)."""

    def log(line: str) -> None:
        append_log(account, line)

    return log


def _summary(stats: dict[str, object]) -> str:
    """The run's closing log line: counts, and whether it halted (never the HALT's text)."""
    line = (
        f"run end: {stats.get('downloaded', 0)} downloaded, "
        f"{stats.get('failed', 0)} failed, {stats.get('linked', 0)} linked"
    )
    if stats.get("halted"):
        line += "; HALT"
    return line


def fetch(
    account: str | None = None,
    *,
    options: FetchOptions,
    interactive: bool = False,
) -> dict[str, object]:
    """Get ``.eml`` for matching messages into the store's staging dir.

    ``options`` (a `FetchOptions`) carries the filter and paths; at most one of its
    ``domains`` / ``query`` is set, and neither means every year until throttled.
    Resolves the account and its per-account archive (``<mail-root>/<account>/
    archive``, which reconcile checks), staging dir (``<mail-root>/<account>/
    staging``, where bodies go), duplicates dir (``<mail-root>/.duplicates/
    <account>``, counted as got), store, and credential file
    (``<state-dir>/accounts/<account>/imap.json``). Loads the
    subdomain-fold map (``<mail-root>/domain-aliases.json`` by default — ONE home,
    shared with `ingest`, and not per-account: a fold is a fact about a domain, not
    about a mailbox, and a *bucket* has no state dir to put it in), seeds
    credentials (the login email is the account reconcile checks), then runs
    `lib.download` or `lib.download_years` and returns its stats dict, which
    carries ``halted`` (and the HALT's ``halt`` line and ``halt_file``) when a
    throttle stopped it. Raises `env.StoreBusy` when another process holds the
    store's lock; a dry run takes no lock and writes no log.
    """
    resolved = resolve_account(account)
    state = resolve_state_dir(resolved, options.state_dir)
    alias_path = options.alias_file or domain_aliases_path(MAIL_ROOT)
    domain_aliases = load_domain_aliases(alias_path)
    search = _domain_query(options.domains, options.query, domain_aliases)

    credentials_path = state / CREDENTIALS_FILENAME
    imap_email, _ = load_credentials(credentials_path, interactive=interactive)

    store = resolve_store_dir(resolved)
    ctx = ThreadContext(
        dest=resolve_archive_dir(resolved),
        imap_email=normalize_account(imap_email),
        domain_aliases=domain_aliases,
        dry_run=options.dry_run,
        store_dir=store,
        staging_dir=resolve_staging_dir(resolved),
        duplicates_dir=resolve_duplicates_dir(resolved),
    )
    log: Callable[[str], None] = (
        (lambda _line: None) if options.dry_run else _run_log(resolved)
    )
    lock: AbstractContextManager[None] = (
        nullcontext() if options.dry_run else store_lock(store)
    )
    with lock:
        if search is None:
            log("run start: every year")
            stats = download_years(
                ctx=ctx,
                credentials_path=credentials_path,
                limit=options.limit,
                threads=options.threads,
                hooks=RunHooks(log=log),
            )
        else:
            log("run start: one filtered pass")
            stats = download(
                query=search,
                ctx=ctx,
                credentials_path=credentials_path,
                limit=options.limit,
                threads=options.threads,
            )
        log(_summary(stats))
    return stats


def held_messages(
    account: str | None = None, domains: set[str] | None = None
) -> Iterator[tuple[Row, Path]]:
    """Yield ``(row, eml_path)`` for every downloaded message.

    Read-only (no IMAP, no credentials). Optionally restrict to a set of filing
    ``domains`` (e.g. the airline domains a travel parser cares about). The
    caller reads and parses the ``.eml``.
    """
    resolved = resolve_account(account)
    archive = resolve_archive_dir(resolved)
    for row in load_store(resolve_store_dir(resolved)).values():
        if not row.get("downloaded"):
            continue
        if domains is not None and row.get("domain") not in domains:
            continue
        path = row.get("path")
        if path:
            yield row, archive / str(path)


@dataclass(frozen=True)
class IngestOptions:
    """How to run an ingest, independent of which account.

    Exactly one source: ``src`` (a local export) or ``staged`` (what ``fetch`` got
    into the account's staging dir); the invariant is enforced here. ``src`` is a
    flat folder of ``.eml``, or a single ``.eml`` (one message is a folder of one;
    the worker treats them alike). ``source`` names the export
    profile ("local" is the only one today: an Outlook / Apple Mail drag-export). With
    ``dry_run`` the worker parses and reports what it would do but writes nothing
    and trashes nothing. ``force`` overrides the account-mismatch refusal (single-
    target only). ``to_trash`` moves the source files of messages that are durably
    archived to the Trash; an unrouted message always keeps its file.

    ``domain`` (the ``--domain`` flag) forces the filing domain for every
    newly-added message, standing in for the computed counterparty; a bare domain
    like ``acme.com``, validated in `ingest`. ``tags`` (the ``--tag`` flag) are
    recorded as each new row's ``labels``. Both touch only mail filed this run.
    """

    src: Path | None = None
    staged: bool = False
    source: str = "local"
    dry_run: bool = False
    force: bool = False
    to_trash: bool = False
    domain: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.src is None) == (not self.staged):
            raise ValueError("pass exactly one of src=... or staged=True")


def _resolver() -> tuple[AccountFor, set[str]]:
    """Bind `env.account_of` to the alias map and the stores that exist."""
    aliases = load_account_aliases()
    accounts = known_accounts()
    return (lambda address: account_of(address, aliases, accounts)), accounts


def _router(accounts: set[str]) -> AccountFor:
    """Bind `domains.route_of` to the counterparty map, filtered to real stores.

    A route to a bucket you never created resolves to nothing, so the thread
    stays unrouted instead of conjuring a directory.
    """
    routes = load_domain_routes(domain_routes_path(MAIL_ROOT))

    def route(address: str) -> str | None:
        destination = route_of(address, routes)
        return destination if destination in accounts else None

    return route


def _target(account: str, accounts: set[str], options: IngestOptions) -> Target:
    return Target(
        store_dir=resolve_store_dir(account),
        archive_dir=resolve_archive_dir(account),
        account=account,
        identities=identities(load_account_aliases(), accounts),
        domain_aliases=load_domain_aliases(domain_aliases_path(MAIL_ROOT)),
        filing_domain=options.domain,
        labels=list(options.tags) or None,
    )


def ingest(account: str | None = None, *, options: IngestOptions) -> dict[str, object]:
    """File the new ``.eml`` under ``options.src`` (routed or into one account), or staged.

    With ``options.staged``, ``account`` resolves as every verb resolves it
    (`env.resolve_account`) and that one store's staged mail is filed. Raises
    ``ValueError`` when ``options.domain`` is not a bare domain: it becomes
    an archive path segment, so a malformed one (or a path-traversal attempt) is
    refused here, at the boundary that accepts owner input, before the worker.
    """
    if options.domain is not None and not DOMAIN_RE.match(options.domain):
        raise ValueError(
            f"invalid --domain {options.domain!r}: expected a bare domain like acme.com"
        )
    account_for, accounts = _resolver()

    if options.staged:
        resolved = resolve_account(account)
        return ingest_staged(
            target=_target(resolved, accounts, options),
            staging_dir=resolve_staging_dir(resolved),
            duplicates_dir=resolve_duplicates_dir(resolved),
            dry_run=options.dry_run,
        )
    src = options.src
    assert src is not None  # IngestOptions: exactly one of src / staged
    if account is None:
        return ingest_routed(
            src=src,
            targets={a: _target(a, accounts, options) for a in sorted(accounts)},
            account_for=account_for,
            route_for=_router(accounts),
            dry_run=options.dry_run,
            to_trash=options.to_trash,
        )

    resolved = resolve_account(account)
    return ingest_folder(
        src=src,
        target=_target(resolved, accounts, options),
        account_for=account_for,
        dry_run=options.dry_run,
        force=options.force,
        to_trash=options.to_trash,
    )


# --- the request functions: one per verb, bound in VERBS ---------------------------


def _refused(exc: Exception) -> ApiError:
    """A refused catalog or fetch run, as the message and exit code it reports."""
    if isinstance(exc, Halted):
        return ApiError(halt_text(exc.line, str(exc.path)))
    if isinstance(exc, AuthError):
        return ApiError(f"auth error: {exc}")
    if isinstance(exc, imaplib.IMAP4.error):
        return ApiError(f"IMAP error: {exc}", exit_code=1)
    return ApiError(str(exc))  # a bad request, or a store another process holds


def _parse_years(raw: str) -> list[int]:
    years = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            years.append(int(part))
    if not years:
        raise ValueError("--years was empty")
    return years


def catalog_root(  # pylint: disable=too-many-arguments
    *,
    mail_account: str | None = None,
    years: str | None = None,
    full: bool = False,
    mailbox: str = ALL_MAIL_MAILBOX,
    store_dir: Path | None = None,
    state_dir: Path | None = None,
    threads: int = DEFAULT_THREADS,
    interactive: bool = False,
    dry_run: bool = False,
) -> CatalogRoot:
    """Observe the mailbox into the year-sharded manifest; headers only, never bodies."""
    swept = None
    if years:
        try:
            swept = _parse_years(years)
        except ValueError as exc:
            raise ApiError(f"bad --years: {exc}") from exc
    try:
        options = CatalogOptions(
            store_dir=store_dir.expanduser() if store_dir else None,
            state_dir=state_dir,
            mailbox=mailbox,
            years=swept,
            dry_run=dry_run,
            threads=threads,
            full=full,
        )
        stats = catalog(mail_account, options=options, interactive=interactive)
    except NotLinked:
        return {"account_linked": False, "dry_run": dry_run}  # a no-op, silent
    except (ValueError, StoreBusy, AuthError, imaplib.IMAP4.error) as exc:
        raise _refused(exc) from exc
    return {
        "account_linked": True,
        "dry_run": dry_run,
        "store_dir": str(stats["store_dir"]),
        "messages": int(stats["messages"]),
        "shards": int(stats["shards"]),
        "removed": int(stats["removed"]),
        "boundary_drops": int(stats["boundary_drops"]),
        "parse_skips": int(stats["parse_skips"]),
        "label_misses": int(stats["label_misses"]),
    }


def _bucket_view(name: str) -> BucketView:
    """Per store: how many messages the bucket claims, how many are held, by domain."""
    stores: list[BucketStore] = []
    for account, rows in bucket_rows(name).items():
        by_domain = Counter(domain for domain, _ in rows)
        stores.append(
            {
                "account": account,
                "messages": len(rows),
                "held": sum(1 for _, row in rows if row.get("downloaded")),
                "domains": [
                    {"domain": domain, "count": count}
                    for domain, count in by_domain.most_common()
                ],
            }
        )
    return {
        "name": name,
        "stores": stores,
        "total": sum(store["messages"] for store in stores),
    }


def report_root(
    mail_account: str | None = None,
    senders: bool = False,
    addresses: bool = False,
    bucket: str | None = None,
) -> ReportRoot:
    """Read-only reports over the manifest: a summary, senders, addresses, or a bucket's
    view."""
    try:
        if bucket:
            return {"view": "bucket", "bucket": _bucket_view(bucket)}
        if senders:
            return {
                "view": "senders",
                "senders": [
                    {"address": address, "count": count}
                    for address, count in sender_counts(mail_account)
                ],
            }
        if addresses:
            return {"view": "addresses", "addresses": all_addresses(mail_account)}
        counts = report(mail_account)
        store_dir = resolve_store_dir(resolve_account(mail_account))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return {
        "view": "summary",
        "store_dir": str(store_dir),
        "counts": {
            "messages": counts["messages"],
            "downloaded": counts["downloaded"],
            "downloaded_gone": counts["downloaded_gone"],
            "downloaded_present": counts["downloaded_present"],
            "observed_only": counts["observed_only"],
            "excluded": counts["excluded"],
            "senders": counts["senders"],
            "addresses": counts["addresses"],
        },
    }


def _pass_row(row: dict[str, object]) -> PassRow:
    """One pass of a fetch run, as its result type carries it."""
    copied: PassRow = {
        "year": cast(int | None, row["year"]),
        "pass": cast(int, row["pass"]),
        "matched": cast(int, row["matched"]),
        "got": cast(int, row["got"]),
        "candidates": cast(int, row["candidates"]),
        "pending": cast(int, row["pending"]),
        "linked": cast(int, row["linked"]),
        "downloaded": cast(int, row["downloaded"]),
        "failed": cast(int, row["failed"]),
    }
    if "got_total" in row:
        copied["got_total"] = cast(int, row["got_total"])
    return copied


def fetch_root(  # pylint: disable=too-many-arguments
    *,
    mail_account: str | None = None,
    query: str | None = None,
    domains: str | None = None,
    last: str | None = None,
    limit: int | None = None,
    target: Path | None = None,
    alias_file: Path | None = None,
    state_dir: Path | None = None,
    threads: int = DEFAULT_THREADS,
    interactive: bool = False,
    dry_run: bool = False,
) -> FetchRoot:
    """Get .eml for matching messages into the store's staging dir, filing nothing;
    with no filter, every message not yet got, until Gmail throttles.

    A run that halts partway is refused: the ``ApiError`` says what it did, then the
    HALT. ``last`` (``2d``, ``3m``, ``1y``) is the Gmail search ``newer_than:<last>``,
    run as a ``query``; at most one of ``domains``, ``query`` and ``last`` is given.
    """
    if last is not None:
        if not re.fullmatch("[1-9][0-9]*[dmy]", last):
            raise ApiError(f"--last takes N then d, m or y (2d, 3m, 1y), not {last!r}")
        if domains is not None or query is not None:
            raise ApiError("pass at most one of --domains, --query or --last")
        query = f"newer_than:{last}"
    try:
        options = FetchOptions(
            domains=domains,
            query=query,
            dest=target.expanduser() if target else None,
            state_dir=state_dir,
            alias_file=alias_file.expanduser() if alias_file else None,
            limit=limit,
            threads=threads,
            dry_run=dry_run,
        )
        stats = fetch(mail_account, options=options, interactive=interactive)
    except NotLinked:
        return {"account_linked": False, "dry_run": dry_run}  # a no-op, silent
    except (ValueError, StoreBusy, AuthError, imaplib.IMAP4.error) as exc:
        raise _refused(exc) from exc
    passes = cast(list[dict[str, object]], stats.get("passes", []))
    result: FetchRoot = {
        "account_linked": True,
        "dry_run": dry_run,
        "filtered": not (domains is None and query is None),
        "account": mail_account,
        "matched": cast(int, stats["matched"]),
        "got": cast(int, stats["got"]),
        "candidates": cast(int, stats["candidates"]),
        "pending": cast(int, stats["pending"]),
        "linked": cast(int, stats["linked"]),
        "downloaded": cast(int, stats["downloaded"]),
        "failed": cast(int, stats["failed"]),
        "passes": [_pass_row(row) for row in passes],
        "halted": bool(stats.get("halted")),
        "halt": cast(str | None, stats.get("halt")),
        "halt_file": cast(str | None, stats.get("halt_file")),
    }
    if result["halted"]:
        halt = halt_text(str(stats.get("halt")), str(stats.get("halt_file")))
        raise ApiError("\n".join([*fetch_lines(result), halt]))
    return result


def _ingest_stats(stats: dict[str, object]) -> dict[str, object]:
    """An ingest run's stats as JSON: year keys as strings, each top domain as a row."""
    out = dict(stats)
    if "added_by_year" in out:
        years = cast(dict[int, int], out["added_by_year"])
        out["added_by_year"] = {str(year): count for year, count in years.items()}
    if "top_domains" in out:
        top = cast(list[tuple[str, int]], out["top_domains"])
        out["top_domains"] = [{"domain": d, "count": n} for d, n in top]
    if "accounts" in out:
        accounts = cast(list[dict[str, object]], out["accounts"])
        out["accounts"] = [_ingest_stats(account) for account in accounts]
    return out


def ingest_root(  # pylint: disable=too-many-arguments
    *,
    mail_account: str | None = None,
    src: Path | None = None,
    staged: bool = False,
    source: str = "local",
    domain: str | None = None,
    tags: list[str] | None = None,
    no_delete: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> IngestRoot:
    """Additively file what fetch staged, or a local .eml folder, into the manifest and
    archive.

    A routed run that skips a store another process holds is refused: the ``ApiError``
    says what it filed, then each busy store.
    """
    if staged:
        mode = "staged"
    elif mail_account is None:
        mode = "routed"
    else:
        mode = "single"
    try:
        options = IngestOptions(
            src=src.expanduser() if src else None,
            staged=staged,
            source=source,
            dry_run=dry_run,
            force=force,
            to_trash=not no_delete,
            domain=domain,
            tags=tuple(tags or ()),
        )
        stats = ingest(mail_account, options=options)
    except (ValueError, FileNotFoundError, StoreBusy) as exc:
        raise ApiError(str(exc)) from exc
    result = cast(
        IngestRoot,
        {
            "mode": mode,
            "dry_run": dry_run,
            "to_trash": not no_delete,
            **_ingest_stats(stats),
        },
    )
    if result.get("busy"):
        raise ApiError("\n".join([*ingest_lines(result), *busy_lines(result["busy"])]))
    return result


# CLI spelling -> the function it binds. A verb spelled like a keyword or a builtin
# (`import`, `list`) is a key here rather than a function name; the lexicon reads this
# literal to find each verb.
VERBS: dict[str, Callable[..., object]] = {
    "catalog": catalog_root,
    "fetch": fetch_root,
    "ingest": ingest_root,
    "report": report_root,
}
