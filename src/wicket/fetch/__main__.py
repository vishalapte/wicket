"""CLI entry: python -m wicket.fetch

Pattern 3 (leaf CLI): argparse owns this layer; the worker logic lives in
the utility modules (fetch / auth / config) it dispatches to.
"""

import argparse
import imaplib
import os
import sys
from pathlib import Path

from wicket.auth import AuthError, load_credentials
from wicket.config import (
    ACCOUNT_ENV_VAR,
    ALIASES_FILENAME,
    CREDENTIALS_FILENAME,
    discover_account,
    normalize_account,
    resolve_archive_dir,
    resolve_state_dir,
    resolve_store_dir,
)
from wicket.domains import load_domain_aliases
from wicket.fetch.retrieve import ThreadContext, download
from wicket.reconcile import build_domain_query


def commands() -> list[tuple[str, str]]:
    return []  # leaf — no sub-packages


def parser() -> argparse.ArgumentParser:
    """Build the CLI parser (factory contract: introspectable by tooling)."""
    build = argparse.ArgumentParser(
        prog="python -m wicket.fetch",
        description=(
            "Download full .eml for every message in matching Gmail threads, "
            "filed under <dest>/<domain>/YYYY-MM/<msg-id>.eml. The domain is "
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
        "--dest",
        type=Path,
        default=None,
        help="Destination root for .eml files; files go into "
        "<domain>/YYYY-MM/. Default: ~/mail/<account>/archive/.",
    )
    build.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Where imap.json (email + app password) lives "
        "(default: ~/.config/gmail; shared with wicket.catalog).",
    )
    build.add_argument(
        "--account",
        default=None,
        help="Account that scopes --dest and the manifest store "
        "(~/mail/<account>/). Gmail `+tag` aliases are normalized "
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
        default=None,
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


def _resolve_account(arg_account: str | None) -> str | None:
    """ACCOUNT from --account, $WICKET_ACCOUNT, or the sole ~/mail account."""
    if arg_account:
        return arg_account
    env_account = os.environ.get(ACCOUNT_ENV_VAR)
    if env_account:
        return env_account
    return discover_account()


def _resolve_query(args: argparse.Namespace, domain_aliases: dict[str, str]) -> str:
    """Return the Gmail search expression from --domains or --query.

    Raises ``ValueError`` on an empty or invalid --domains list.
    """
    if args.domains:
        domains = [d.strip() for d in args.domains.split(",") if d.strip()]
        if not domains:
            raise ValueError("--domains was empty")
        return build_domain_query(domains, domain_aliases)
    return str(args.query)


def run(args: argparse.Namespace) -> int:
    """Resolve paths, seed credentials, dispatch into the fetch worker."""
    state_dir = resolve_state_dir(args.state_dir)
    interactive = not args.non_interactive and sys.stdin.isatty()

    account = _resolve_account(args.account)
    if not account:
        print(
            f"no account: pass --account ADDR, set ${ACCOUNT_ENV_VAR}, or keep "
            "exactly one account directory under ~/mail.",
            file=sys.stderr,
        )
        return 2
    primary_email = normalize_account(account)

    dest = (args.dest or resolve_archive_dir(primary_email)).expanduser()
    store_dir = resolve_store_dir(primary_email)

    alias_path = (
        args.alias_file.expanduser()
        if args.alias_file
        else state_dir / ALIASES_FILENAME
    )
    try:
        domain_aliases = load_domain_aliases(alias_path)
        query = _resolve_query(args, domain_aliases)
    except (ValueError, OSError) as exc:
        print(f"bad config: {exc}", file=sys.stderr)
        return 2

    creds_path = state_dir / CREDENTIALS_FILENAME
    try:
        # Seed/validate credentials once here (workers never prompt — a worker
        # blocking on getpass would hang the pool). The login email is also the
        # "me" identity for the filing-domain rule.
        imap_email, _ = load_credentials(creds_path, interactive=interactive)
    except AuthError as exc:
        print(f"auth error: {exc}", file=sys.stderr)
        return 2

    ctx = ThreadContext(
        dest=dest,
        imap_email=normalize_account(imap_email),
        domain_aliases=domain_aliases,
        dry_run=args.dry_run,
        store_dir=store_dir,
    )
    try:
        stats = download(
            query=query,
            ctx=ctx,
            credentials_path=creds_path,
            limit=args.limit,
            **({"threads": args.threads} if args.threads else {}),
        )
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


def main() -> int:
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
