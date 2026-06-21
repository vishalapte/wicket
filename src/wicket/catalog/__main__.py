"""CLI entry: python -m wicket.catalog

Pattern 3 (leaf CLI): argparse owns this layer; the worker logic lives in
the utility modules (sweep / auth / config) it dispatches to.
"""

import argparse
import imaplib
import sys
from pathlib import Path

from wicket.catalog.api import AuthError, CatalogOptions, catalog
from wicket.config import (
    ACCOUNT_ENV_VAR,
    ALL_MAIL_MAILBOX,
    DEFAULT_THREADS,
    resolve_account,
    resolve_store_dir,
)


def commands() -> list[tuple[str, str]]:
    return []  # leaf — no sub-packages


def parser() -> argparse.ArgumentParser:
    """Build the CLI parser (factory contract: introspectable by tooling)."""
    build = argparse.ArgumentParser(
        prog="python -m wicket.catalog",
        description=(
            "Sweep mailbox headers (never bodies) into the year-sharded "
            "manifest at ~/mail/<account>/manifest/YYYY.jsonl, one row per "
            "message (the observation fields and the `deleted` flag). Merges "
            "with settlement already in the store; re-running is idempotent."
        ),
    )
    build.add_argument(
        "--account",
        default=None,
        help="Account address; scopes the manifest store. "
        "Gmail `+tag` aliases are normalized automatically. "
        f"Required if ${ACCOUNT_ENV_VAR} is unset.",
    )
    build.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Override the manifest store dir. Default: ~/mail/<account>/manifest/.",
    )
    build.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Where imap.json (email + app password) lives "
        "(default: ~/.config/gmail; shared with the other tools).",
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


def run(args: argparse.Namespace) -> int:
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
        stats = catalog(args.account, options=options, interactive=interactive)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except AuthError as exc:
        print(f"auth error: {exc}", file=sys.stderr)
        return 2
    except imaplib.IMAP4.error as exc:
        print(f"IMAP error: {exc}", file=sys.stderr)
        return 1

    store_dir = (
        args.store_dir.expanduser()
        if args.store_dir
        else resolve_store_dir(resolve_account(args.account))
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


def main() -> int:
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
