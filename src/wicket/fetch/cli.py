"""The `fetch` verb's argparse face: `parser()` + `dispatch()`.

Not a CLI entry (no `__main__.py` in this package on purpose) — `python -m
wicket fetch ...` is the only way in. The root `wicket/__main__.py`
(Pattern 2) imports this module, folds `parser()`'s arguments into its own
`fetch` subparser via `parents=[...]`, and calls `dispatch()` once argparse
has resolved the verb.
"""

import argparse
import imaplib
import sys
from pathlib import Path

from wicket.env import DEFAULT_THREADS
from wicket.fetch.api import AuthError, FetchOptions, fetch


def parser() -> argparse.ArgumentParser:
    """Argument definitions only (`add_help=False`): folded into the root's subparser."""
    build = argparse.ArgumentParser(
        add_help=False,
        description=(
            "Download full .eml for every message in matching Gmail threads, "
            "filed under <target>/<domain>/YYYY-MM/<msg-id>.eml. The domain is "
            "computed from the first message of each thread (inbound: the "
            "sender's domain; outbound: the single recipient's); threads it "
            "cannot resolve are skipped. Settlement is recorded in the "
            "year-sharded manifest."
        ),
    )
    filter_group = build.add_mutually_exclusive_group(required=True)
    filter_group.add_argument(
        "--domains",
        help='Comma-separated list, e.g. "acme.com,globex.com". '
        "Tool builds the Gmail search expression for messages from/to "
        "any of these domains.",
    )
    filter_group.add_argument(
        "--query",
        help="Raw Gmail search expression (escape hatch). Use this for "
        "anything --domains can't express.",
    )
    build.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Destination root for .eml files; files go into "
        "<domain>/YYYY-MM/. Default: <mail-root>/<account>/archive/.",
    )
    build.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Where imap.json (email + app password) lives "
        "(default: ~/.config/gmail; shared with wicket.catalog).",
    )
    build.add_argument(
        "--mail-account",
        default=None,
        help="Account that scopes --target and the manifest store "
        "(<mail-root>/<account>/). Gmail `+tag` aliases are normalized "
        "automatically. Required if $WICKET_ACCOUNT is unset.",
    )
    build.add_argument(
        "--alias-file",
        type=Path,
        default=None,
        help="JSON file of domain aliases, shape "
        '{"primary.com": ["alias1.com", "alias2.com"]}. '
        "Search expands to include aliases; the filing domain canonicalizes "
        "to primary. Default: <state-dir>/domain-aliases.json (silently "
        "skipped if absent).",
    )
    build.add_argument(
        "--max",
        type=int,
        default=None,
        dest="limit",
        help="Stop after N new messages this run (after the store dedup).",
    )
    build.add_argument(
        "--threads",
        type=int,
        default=DEFAULT_THREADS,
        help="Concurrent thread-processing workers, one IMAP connection "
        "each (default: 4; Gmail caps simultaneous connections at ~15).",
    )
    build.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan; download nothing; do not update the manifest.",
    )
    build.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt for credentials; fail loudly if imap.json is "
        "missing or rejected (for cron/launchd).",
    )
    return build


def dispatch(args: argparse.Namespace) -> int:
    """Parse argv into typed kwargs, call the api, render the summary."""
    interactive = (not args.non_interactive) and sys.stdin.isatty()
    try:
        options = FetchOptions(
            domains=args.domains,
            query=args.query,
            dest=args.target.expanduser() if args.target else None,
            state_dir=args.state_dir,
            alias_file=args.alias_file.expanduser() if args.alias_file else None,
            limit=args.limit,
            threads=args.threads,
            dry_run=args.dry_run,
        )
        stats = fetch(args.mail_account, options=options, interactive=interactive)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except AuthError as exc:
        print(f"auth error: {exc}", file=sys.stderr)
        return 2
    except imaplib.IMAP4.error as exc:
        print(f"IMAP error: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(
            f"done (dry-run): {stats['pending']} to download, "
            f"{stats['on_disk']} already on disk; {stats['held']} already held. "
            "Nothing written."
        )
    else:
        print(
            f"done: {stats['downloaded']} downloaded, {stats['failed']} failed; "
            f"{stats['held']} already held."
        )
    return 0
