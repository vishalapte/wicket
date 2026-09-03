"""The `report` verb's argparse face: `parser()` + `dispatch()`.

Not a CLI entry (no `__main__.py` in this package on purpose) — `python -m
wicket report ...` is the only way in. The root `wicket/__main__.py`
(Pattern 2) imports this module, folds `parser()`'s arguments into its own
`report` subparser via `parents=[...]`, and calls `dispatch()` once argparse
has resolved the verb.
"""

import argparse
import sys
from collections import Counter

from wicket.env import ACCOUNT_ENV_VAR, resolve_account, resolve_store_dir
from wicket.report.api import addresses, bucket, report, senders


def parser() -> argparse.ArgumentParser:
    """Argument definitions only (`add_help=False`): folded into the root's subparser."""
    build = argparse.ArgumentParser(
        add_help=False,
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
    group.add_argument(
        "--bucket",
        metavar="NAME",
        help="Every message ANY store holds that this bucket claims (travel, "
        "shopping), grouped by store. A view, not a location: mail owned by a "
        "mailbox stays in that mailbox's store. Ignores --account.",
    )
    build.add_argument(
        "--account",
        default=None,
        help="Account address; scopes the manifest store. Gmail `+tag` aliases "
        f"are normalized. Required if ${ACCOUNT_ENV_VAR} is unset.",
    )
    return build


def dispatch(args: argparse.Namespace) -> int:
    """Call the api for the requested report and print it to stdout."""
    try:
        if args.bucket:
            _print_bucket(args.bucket)
        elif args.senders:
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


def _print_bucket(name: str) -> None:
    """Per store: how many messages the bucket claims, and from which domains."""
    found = bucket(name)
    if not found:
        print(f"{name}: no mail in any store")
        return
    total = 0
    for account, rows in found.items():
        held = sum(1 for _, row in rows if row.get("downloaded"))
        by_domain = Counter(domain for domain, _ in rows)
        total += len(rows)
        print(f"{account}: {len(rows)} message(s), {held} held locally")
        for domain, count in by_domain.most_common():
            print(f"  {count:>6}  {domain}")
    print(f"{name}: {total} message(s) across {len(found)} store(s)")
