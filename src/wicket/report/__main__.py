"""CLI entry: python -m wicket.report

Pattern 3 (leaf CLI): read-only reports over the manifest. No IMAP, no
credentials; it only reads the year-sharded store. Output goes to stdout, one
record per line, so it pipes (`... | head`, `> senders.tsv`).
"""

import argparse
import signal
import sys

from wicket.config import ACCOUNT_ENV_VAR, resolve_account, resolve_store_dir
from wicket.report.api import addresses, report, senders


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
    """Call the api for the requested report and print it to stdout."""
    try:
        if args.senders:
            for sender, count in senders(args.account):
                print(f"{count}\t{sender}")
        elif args.addresses:
            for address in addresses(args.account):
                print(address)
        else:
            counts = report(args.account)
            store_dir = resolve_store_dir(resolve_account(args.account))
            print(f"manifest: {store_dir}")
            print(f"  messages           {counts['messages']:>8}")
            print(f"  downloaded         {counts['downloaded']:>8}  (held locally)")
            print(f"    gone from Gmail  {counts['downloaded_gone']:>8}  (only copy)")
            print(f"    still in mailbox {counts['downloaded_present']:>8}")
            print(f"  observed only      {counts['observed_only']:>8}  (not held)")
            print(f"  distinct senders   {counts['senders']:>8}")
            print(f"  distinct addresses {counts['addresses']:>8}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def main() -> int:
    # Streaming output: let a closed downstream pipe (`| head`) end the process
    # quietly via SIGPIPE rather than raising a BrokenPipeError traceback.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
