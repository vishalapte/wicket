"""The `fetch` verb's argparse face: `parser()` + `dispatch()`.

Not a CLI entry (no `__main__.py` in this package on purpose) — `python -m
wicket fetch ...` is the only way in. The root `wicket/__main__.py`
(Pattern 2) imports this module, folds `parser()`'s arguments into its own
`fetch` subparser via `parents=[...]`, and calls `dispatch()` once argparse
has resolved the verb.
"""

import argparse
import imaplib
import re
import sys
from pathlib import Path

from wicket.env import DEFAULT_THREADS, NotLinked, StoreBusy
from wicket.fetch.api import AuthError, FetchOptions, Halted, fetch

EXIT_HALTED = 3  # a HALT is in force, or this run wrote one
EXIT_BUSY = 75  # EX_TEMPFAIL: another process holds the store's lock


def parser() -> argparse.ArgumentParser:
    """Argument definitions only (`add_help=False`): folded into the root's subparser."""
    build = argparse.ArgumentParser(
        add_help=False,
        description=(
            "Get full .eml for every matching Gmail message into the store's "
            "staging dir (<mail-root>/<account>/staging/<thread>/<msgid>@<date>.eml). "
            "fetch files nothing: `wicket ingest --staged` files staged mail into "
            "the archive, and until then it is not in report or the manifest's "
            "downloaded rows. With no --domains, --query or --last, gets every "
            "message not yet got and not excluded, this year, last year, then "
            "older years oldest first, until Gmail throttles: the first throttle "
            "writes the account's HALT and exits 3, and no IMAP connection opens "
            "past a HALT. Exits 75 when another process holds the store's lock."
        ),
    )
    filter_group = build.add_mutually_exclusive_group()
    filter_group.add_argument(
        "--domains",
        help='Comma-separated list, e.g. "acme.com,globex.com". '
        "Tool builds the Gmail search expression for messages from/to "
        "any of these domains. Omit every filter to fetch everything.",
    )
    filter_group.add_argument(
        "--query",
        help="Raw Gmail search expression (escape hatch). Use this for "
        "anything --domains can't express.",
    )
    filter_group.add_argument(
        "--last",
        metavar="AGE",
        help="Messages newer than AGE: N then d, m or y (2d, 3m, 1y), Gmail's "
        "newer_than:AGE. Anything else exits 2.",
    )
    build.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Refused: fetch no longer files mail, so it takes no destination. "
        "Bodies go to <mail-root>/<account>/staging/; `wicket ingest --staged` "
        "files them.",
    )
    build.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="The base holding accounts/<account>/imap.json (email + app password) "
        "(default: ~/.config/wicket; shared with wicket.catalog).",
    )
    build.add_argument(
        "--mail-account",
        default=None,
        help="Account that scopes the staging dir and the manifest store "
        "(<mail-root>/<account>/). Gmail `+tag` aliases are normalized "
        "automatically. Default: $WICKET_ACCOUNT, else the only account folder.",
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
        metavar="N",
        help="Stop after N new messages this run (after the store dedup), "
        "across every year of an unfiltered run.",
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
        help="Print the plan; download nothing; write nothing.",
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
        query = args.query
        if args.last is not None:  # Gmail's newer_than, run as a query
            if not re.fullmatch("[1-9][0-9]*[dmy]", args.last):
                raise ValueError(
                    f"--last takes N then d, m or y (2d, 3m, 1y), not {args.last!r}"
                )
            query = f"newer_than:{args.last}"
        options = FetchOptions(
            domains=args.domains,
            query=query,
            dest=args.target.expanduser() if args.target else None,
            state_dir=args.state_dir,
            alias_file=args.alias_file.expanduser() if args.alias_file else None,
            limit=args.limit,
            threads=args.threads,
            dry_run=args.dry_run,
        )
        stats = fetch(args.mail_account, options=options, interactive=interactive)
    except NotLinked:
        return 0  # no folder in ~/.config/wicket/accounts: a no-op, silent
    except (ValueError, StoreBusy, AuthError, imaplib.IMAP4.error) as exc:
        return _refused(exc)
    _render(stats, args)
    if stats.get("halted"):
        _print_halt(str(stats.get("halt")), str(stats.get("halt_file")))
        return EXIT_HALTED
    return 0


def _refused(exc: Exception) -> int:
    """Print why the api refused, and return the exit code for it."""
    if isinstance(exc, Halted):
        _print_halt(exc.line, str(exc.path))
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


def _print_halt(line: str, path: str) -> None:
    """The HALT's first line, its file, and the command that clears it (named by account)."""
    print(f"halted: {line}", file=sys.stderr)
    print(
        f"HALT at {path}; clear it with `python -m wicket.halt clear --mail-account "
        f"{Path(path).name}` (if Gmail rejected the app password, re-seed it first)",
        file=sys.stderr,
    )


def _render(stats: dict[str, object], args: argparse.Namespace) -> None:
    """Print the run summary: one pass with a filter, every year's passes without.

    "got" is mail already staged, filed, excluded or set aside as a duplicate, never
    "held", which means filed in the archive.
    """
    # Said only when non-zero, so a run that links nothing prints what it always did.
    linked = stats.get("linked", 0)
    link_note = f" {linked} linked to an archived copy." if linked else ""
    if args.domains is None and args.query is None:
        passes: list[dict[str, object]] = stats.get("passes", [])  # type: ignore[assignment]
        years = len({p["year"] for p in passes})
        span = f"over {len(passes)} pass(es) in {years} year(s)."
    else:
        span = f"{stats['got']} already got."
    if args.dry_run:
        print(
            f"done (dry-run): {stats['pending']} to download, {span}{link_note} "
            "Nothing written."
        )
        return
    print(
        f"done: {stats['downloaded']} downloaded, {stats['failed']} failed, {span}"
        f"{link_note}"
    )
    if stats["downloaded"]:
        print(
            "staged, not yet filed: wicket ingest --staged --mail-account "
            f"{_account(args)}"
        )


def _account(args: argparse.Namespace) -> str:
    """The account as the owner named it, for the hint (resolution is the api's)."""
    return str(args.mail_account or "<account>")
