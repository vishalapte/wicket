"""CLI entry: python -m wicket.report

Pattern 3 (leaf CLI): read-only reports over the manifest. No IMAP, no
credentials; it only reads the year-sharded store. Output goes to stdout, one
record per line, so it pipes (`... | head`, `> senders.tsv`).
"""

import argparse
import os
import signal
import sys

from wicket.config import (
    ACCOUNT_ENV_VAR,
    discover_account,
    normalize_account,
    resolve_store_dir,
)
from wicket.report.summary import all_addresses, sender_counts, summary


def commands() -> list[tuple[str, str]]:
    return []  # leaf — no sub-packages


def parser() -> argparse.ArgumentParser:
    """Build the CLI parser (factory contract: introspectable by tooling)."""
    build = argparse.ArgumentParser(
        prog="python -m wicket.report",
        description=(
            "Read-only reports over the year-sharded manifest (no IMAP). With "
            "no flag, prints a one-screen summary; otherwise one record per line."
        ),
    )
    group = build.add_mutually_exclusive_group()
    group.add_argument(
        "--senders",
        action="store_true",
        help="Every From address with its message count, descending "
        "(tab-separated `count<TAB>address`).",
    )
    group.add_argument(
        "--addresses",
        action="store_true",
        help="Every distinct address seen in From or To, sorted.",
    )
    build.add_argument(
        "--account",
        default=None,
        help="Account address; scopes the manifest store. Gmail `+tag` aliases "
        f"are normalized. Required if ${ACCOUNT_ENV_VAR} is unset.",
    )
    return build


def run(args: argparse.Namespace) -> int:
    """Resolve the store and print the requested report to stdout."""
    account = args.account or os.environ.get(ACCOUNT_ENV_VAR) or discover_account()
    if not account:
        print(
            f"no account: pass --account ADDR, set ${ACCOUNT_ENV_VAR}, or keep "
            "exactly one account directory under ~/mail.",
            file=sys.stderr,
        )
        return 2
    store_dir = resolve_store_dir(normalize_account(account))
    if args.senders:
        for sender, count in sender_counts(store_dir):
            print(f"{count}\t{sender}")
    elif args.addresses:
        for address in all_addresses(store_dir):
            print(address)
    else:
        report = summary(store_dir)
        print(f"manifest: {store_dir}")
        print(f"  messages           {report['messages']:>8}")
        print(f"  downloaded         {report['downloaded']:>8}  (held locally)")
        print(f"    gone from Gmail  {report['downloaded_gone']:>8}  (only copy)")
        print(f"    still in mailbox {report['downloaded_present']:>8}")
        print(f"  observed only      {report['observed_only']:>8}  (not held)")
        print(f"  distinct senders   {report['senders']:>8}")
        print(f"  distinct addresses {report['addresses']:>8}")
    return 0


def main() -> int:
    # Streaming output: let a closed downstream pipe (`| head`) end the process
    # quietly via SIGPIPE rather than raising a BrokenPipeError traceback.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
