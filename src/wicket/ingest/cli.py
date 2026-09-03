"""The `ingest` verb's argparse face: `parser()` + `dispatch()`.

Not a CLI entry (no `__main__.py` in this package on purpose) — `python -m
wicket ingest ...` is the only way in. The root `wicket/__main__.py`
(Pattern 2) imports this module, folds `parser()`'s arguments into its own
`ingest` subparser via `parents=[...]`, and calls `dispatch()` once argparse
has resolved the verb.
"""

import argparse
import sys
from pathlib import Path

from wicket.env import resolve_account, resolve_store_dir
from wicket.ingest.api import IngestOptions, ingest


def parser() -> argparse.ArgumentParser:
    """Argument definitions only (`add_help=False`): folded into the root's subparser."""
    build = argparse.ArgumentParser(
        add_help=False,
        description=(
            "Ingest local .eml (a folder — an Outlook / Apple Mail drag-export — "
            "or a single message) "
            "into the year-sharded manifest + archive, for a mailbox wicket "
            "cannot reach over IMAP. Additive: a message already archived is left "
            "untouched, only new ones are filed, and no archived message is ever "
            "deleted. Re-running is idempotent. With no --account, each thread is "
            "routed to the account it was addressed to and mail nobody claims is "
            "left alone."
        ),
    )
    build.add_argument(
        "--src",
        type=Path,
        required=True,
        help="What to ingest: a folder of .eml (flat drag-export, not recursive) "
        "or a single .eml file.",
    )
    build.add_argument(
        "--account",
        default=None,
        help="Send every thread to this one account, instead of routing each to "
        "the account it was addressed to. A destination, NOT a filter: it does "
        "not select which messages are ingested, so naming the wrong one files "
        "the whole folder into the wrong store (refused unless --force). Omit it "
        "to route.",
    )
    build.add_argument(
        "--source",
        default="local",
        choices=["local"],
        help="Export profile (only 'local' today: RFC822 .eml on disk).",
    )
    build.add_argument(
        "--domain",
        default=None,
        help="File every newly-added message under this one domain folder "
        "(<domain>/YYYY-MM/), overriding the counterparty domain the filing rule "
        "would compute. A destination, NOT a filter: it does not select which "
        "messages are ingested. A bare domain like acme.com. A reply that joins "
        "an already-archived thread still files with its thread, so this only "
        "redirects mail that has no archived home yet.",
    )
    build.add_argument(
        "--tags",
        default=None,
        help="Comma-separated tags recorded as the manifest `labels` for every "
        "newly-added message (a flat .eml export carries no provider labels). "
        "Applied only to messages filed this run; already-archived rows are left "
        "untouched.",
    )
    build.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report what would be added; write nothing.",
    )
    build.add_argument(
        "--force",
        action="store_true",
        help="Ingest even when the source is addressed to a different account "
        "than --account. Refused by default: --account names the destination "
        "store, it does not filter the source.",
    )
    build.add_argument(
        "--no-delete",
        action="store_true",
        help="Leave the source files untouched. By default an ingested .eml is "
        "moved to ~/.Trash once it is durably archived (filed now, or already in "
        "the store), because the archive is the record and the source is a "
        "volatile inbox. A message whose thread could not be routed always keeps "
        "its file, and nothing is ever unlinked: the Trash is the undo.",
    )
    return build


def dispatch(args: argparse.Namespace) -> int:
    """Parse argv into typed options, call the api, render the summary."""
    try:
        tags = (
            tuple(t.strip() for t in args.tags.split(",") if t.strip())
            if args.tags
            else ()
        )
        options = IngestOptions(
            src=args.src.expanduser(),
            source=args.source,
            dry_run=args.dry_run,
            force=args.force,
            to_trash=not args.no_delete,
            domain=args.domain,
            tags=tags,
        )
        stats = ingest(args.account, options=options)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if stats.get("warning"):
        print(f"warning: {stats['warning']}", file=sys.stderr)
    _render(stats, args)
    return 0


def _render(stats: dict[str, object], args: argparse.Namespace) -> None:
    """Print the run summary: what was parsed, where it went, what was left."""
    verb = "would add" if args.dry_run else "added"
    print(
        f"read: {stats['files']} file(s), {stats['unique']} unique, "
        f"{stats['dup']} in-folder dup(s)"
    )

    # Not stats.get(..., _single(...)): the default would be evaluated eagerly and
    # a routed run has no single account to resolve.
    per_account: list[dict[str, object]] = (
        stats["accounts"]  # type: ignore[assignment]
        if "accounts" in stats
        else [_single(stats, args)]
    )
    for got in per_account:
        years: dict[int, int] = got["added_by_year"]  # type: ignore[assignment]
        by_year = ", ".join(f"{y}:{n}" for y, n in years.items())
        line = f"  {verb} {got['added']} to {got['account']}"
        if by_year:
            line += f" ({by_year})"
        if got["skipped"]:
            line += f"; {got['skipped']} already archived"
        if got["unfiled"]:
            line += f"; {got['unfiled']} unfiled"
        print(line)
        _print_filed(got.get("filed", {}))  # type: ignore[arg-type]

    if stats["unrouted"]:
        print(
            f"  left alone: {stats['unrouted']} message(s) addressed to no known "
            "account (add an alias, or create the store, to claim them)"
        )
    if not args.no_delete:
        moved = "would trash" if args.dry_run else "trashed"
        count = stats["trashable" if args.dry_run else "trashed"]
        print(f"  {moved}: {count} source file(s) -> ~/.Trash")


def _single(stats: dict[str, object], args: argparse.Namespace) -> dict[str, object]:
    """The single-target run, shaped like one entry of a routed run's per-account list."""
    return {
        "account": resolve_store_dir(resolve_account(args.account)).parent.name,
        "added": stats["added"],
        "skipped": stats["skipped"],
        "unfiled": stats["unfiled"],
        "added_by_year": stats["added_by_year"],
        "filed": stats["filed"],
    }


def _print_filed(filed: dict[str, list[str]]) -> None:
    """List the filed ``.eml`` under each archive directory, both name-sorted."""
    for directory in sorted(filed):
        print(f"    {directory}/")
        for name in filed[directory]:
            print(f"      {name}")
