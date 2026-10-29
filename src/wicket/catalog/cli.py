"""The `catalog` verb's argparse face: `parser()` + `dispatch()`.

Not a CLI entry (no `__main__.py` in this package on purpose) — `python -m
wicket catalog ...` is the only way in. The root `wicket/__main__.py`
(Pattern 2) imports this module, folds `parser()`'s arguments into its own
`catalog` subparser via `parents=[...]`, and calls `dispatch()` once argparse
has resolved the verb.
"""

import argparse
import imaplib
import sys
from pathlib import Path

from wicket.catalog.api import AuthError, CatalogOptions, Halted, catalog
from wicket.env import (
    ACCOUNT_ENV_VAR,
    ALL_MAIL_MAILBOX,
    DEFAULT_THREADS,
    NotLinked,
    StoreBusy,
    resolve_account,
    resolve_store_dir,
)

EXIT_HALTED = 3  # a HALT is in force, or this run wrote one
EXIT_BUSY = 75  # EX_TEMPFAIL: another process holds the store's lock


def parser() -> argparse.ArgumentParser:
    """Argument definitions only (`add_help=False`): folded into the root's subparser."""
    build = argparse.ArgumentParser(
        add_help=False,
        description=(
            "Sweep mailbox headers (never bodies) into the year-sharded "
            "manifest at <mail-root>/<account>/manifest/YYYY.jsonl, one row per "
            "message (the observation fields and the `deleted` flag). Merges "
            "with settlement already in the store; re-running is idempotent."
        ),
    )
    build.add_argument(
        "--mail-account",
        default=None,
        help="Account address; scopes the manifest store. "
        "Gmail `+tag` aliases are normalized automatically. "
        f"Default: ${ACCOUNT_ENV_VAR}, else the only account folder.",
    )
    build.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Override the manifest store dir. Default: <mail-root>/<account>/manifest/.",
    )
    build.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="The base holding accounts/<account>/imap.json (email + app password) "
        "(default: ~/.config/wicket; shared with the other tools).",
    )
    build.add_argument(
        "--mailbox",
        default=ALL_MAIL_MAILBOX,
        help='IMAP mailbox to sweep (default: "[Gmail]/All Mail"). '
        "Sweeping any other mailbox into the default store dir would "
        "make shards lie about what exists, pass --store-dir too.",
    )
    build.add_argument(
        "--years",
        default=None,
        help="Comma-separated INTERNALDATE years, e.g. 2025,2026. Only these "
        "years' shards are replaced. Omit to sweep incrementally from the "
        "latest year in the manifest; pass --full for a complete re-sweep.",
    )
    build.add_argument(
        "--threads",
        type=int,
        default=DEFAULT_THREADS,
        help="Concurrent year workers, one IMAP connection each (default: 4; "
        "Gmail caps simultaneous IMAP connections, stay well under ~15).",
    )
    build.add_argument(
        "--dry-run",
        action="store_true",
        help="Sweep and report per-year counts; write nothing.",
    )
    build.add_argument(
        "--full",
        action="store_true",
        help="Re-sweep every year from the oldest message through now, not just "
        "from the latest year in the manifest. The incremental default assumes "
        "the past does not change; use --full to rebuild or after deleting old "
        "mail.",
    )
    build.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt for credentials; fail loudly if imap.json is "
        "missing or rejected (for cron/launchd).",
    )
    return build


def _parse_years(raw: str) -> list[int]:
    years = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            years.append(int(part))
    if not years:
        raise ValueError("--years was empty")
    return years


def _refused(exc: Exception) -> int:
    """Print why the api refused, and return the exit code for it."""
    if isinstance(exc, Halted):
        print(f"halted: {exc.line}", file=sys.stderr)
        print(
            f"HALT at {exc.path}; clear it with `python -m wicket.halt clear "
            f"--mail-account {exc.path.name}` (if Gmail rejected the app password, "
            "re-seed it first)",
            file=sys.stderr,
        )
        return EXIT_HALTED
    if isinstance(exc, StoreBusy):
        print(str(exc), file=sys.stderr)
        return EXIT_BUSY
    if isinstance(exc, AuthError):
        print(f"auth error: {exc}", file=sys.stderr)
        return 2
    if isinstance(exc, imaplib.IMAP4.error):
        print(f"IMAP error: {exc}", file=sys.stderr)
        return 1
    print(str(exc), file=sys.stderr)
    return 2


def dispatch(args: argparse.Namespace) -> int:
    """Parse argv into typed kwargs, call the api, render the summary."""
    interactive = (not args.non_interactive) and sys.stdin.isatty()

    years = None
    if args.years:
        try:
            years = _parse_years(args.years)
        except ValueError as exc:
            print(f"bad --years: {exc}", file=sys.stderr)
            return 2

    try:
        options = CatalogOptions(
            store_dir=args.store_dir.expanduser() if args.store_dir else None,
            state_dir=args.state_dir,
            mailbox=args.mailbox,
            years=years,
            dry_run=args.dry_run,
            threads=args.threads,
            full=args.full,
        )
        stats = catalog(args.mail_account, options=options, interactive=interactive)
    except NotLinked:
        return 0  # no folder in ~/.config/wicket/accounts: a no-op, silent
    except (ValueError, StoreBusy, AuthError, imaplib.IMAP4.error) as exc:
        return _refused(exc)

    store_dir = (
        args.store_dir.expanduser()
        if args.store_dir
        else resolve_store_dir(resolve_account(args.mail_account))
    )
    verb = "would write" if args.dry_run else "wrote"
    print(
        f"done: observed {stats['messages']} message(s); {verb} "
        f"{stats['shards']} year shard(s) under {store_dir}"
        + (f"; removed {stats['removed']} stale shard(s)" if stats["removed"] else "")
        + (
            f"; {stats['boundary_drops']} boundary overlap(s) deduped"
            if stats["boundary_drops"]
            else ""
        )
    )
    for key, label in (
        ("parse_skips", "unparseable FETCH response(s)"),
        ("label_misses", "row(s) with labels unrecoverable (labels: null)"),
    ):
        if stats[key]:
            print(f"warning: {stats[key]} {label}", file=sys.stderr)
    return 0
