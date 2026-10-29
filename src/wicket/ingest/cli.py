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

from wicket.env import StoreBusy, resolve_account, resolve_store_dir
from wicket.ingest.api import IngestOptions, ingest

EXIT_BUSY = 75  # EX_TEMPFAIL: a store this run writes is locked by another process


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
            "deleted. Re-running is idempotent. With no --mail-account, each thread is "
            "routed to the account it was addressed to and mail nobody claims is "
            "left alone. With --staged instead of --src, files what `wicket fetch` "
            "got into one account's staging dir. A message whose row is excluded "
            "is skipped and counted."
        ),
    )
    source = build.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--src",
        type=Path,
        help="What to ingest: a folder of .eml (flat drag-export, not recursive) "
        "or a single .eml file.",
    )
    source.add_argument(
        "--staged",
        action="store_true",
        help="File what `wicket fetch` got into <mail-root>/<account>/staging/ "
        "for one account (--mail-account, $WICKET_ACCOUNT, or the only account "
        "folder): "
        "no routing, no account check, nothing to trash. A body the store already "
        "archives under another name goes to <mail-root>/.duplicates/<account>/.",
    )
    build.add_argument(
        "--mail-account",
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
        "(<domain>/YYYY-MM/), overriding the self, sent and counterparty rules. "
        "A destination, NOT a filter: it does not select which messages are "
        "ingested; with --staged it applies to every staged message of the "
        "account. A bare domain like acme.com. A reply that joins an "
        "already-archived thread still files with its thread, so this only "
        "redirects mail that has no archived home yet.",
    )
    build.add_argument(
        "--tag",
        action="append",
        default=[],
        help="A tag recorded in the manifest `labels` for every newly-added "
        "message (a flat .eml export carries no provider labels); repeatable "
        "(--tag a --tag b). Applied only to messages filed this run; "
        "already-archived rows are left untouched.",
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
        "than --mail-account. Refused by default: --mail-account names the "
        "destination store, it does not filter the source.",
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
        tags = tuple(args.tag)
        options = IngestOptions(
            src=args.src.expanduser() if args.src else None,
            staged=args.staged,
            source=args.source,
            dry_run=args.dry_run,
            force=args.force,
            to_trash=not args.no_delete,
            domain=args.domain,
            tags=tags,
        )
        stats = ingest(args.mail_account, options=options)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except StoreBusy as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_BUSY

    if args.staged:
        _render_staged(stats, args)
        return 0
    if stats.get("warning"):
        print(f"warning: {stats['warning']}", file=sys.stderr)
    _render(stats, args)
    busy: list[dict[str, object]] = stats.get("busy", [])  # type: ignore[assignment]
    for store in busy:
        print(
            f"busy: {store['account']} is locked by pid {store['pid'] or '?'}; its "
            f"{store['messages']} message(s) were left for the next run",
            file=sys.stderr,
        )
    return EXIT_BUSY if busy else 0


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
        if got.get("excluded"):
            line += f"; {got['excluded']} excluded, skipped"
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
        "account": resolve_store_dir(resolve_account(args.mail_account)).parent.name,
        "added": stats["added"],
        "skipped": stats["skipped"],
        "excluded": stats.get("excluded", 0),
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


def _render_staged(stats: dict[str, object], args: argparse.Namespace) -> None:
    """Print a staged run: what was staged, what was filed where, what was left."""
    verb = "would file" if args.dry_run else "filed"
    years: dict[int, int] = stats["added_by_year"]  # type: ignore[assignment]
    by_year = ", ".join(f"{y}:{n}" for y, n in years.items())
    line = f"staged: {stats['staged']} file(s); {verb} {stats['added']} to {stats['account']}"
    if by_year:
        line += f" ({by_year})"
    print(line)
    rules: dict[str, int] = stats["by_rule"]  # type: ignore[assignment]
    if rules:
        print("  by rule: " + ", ".join(f"{k} {n}" for k, n in rules.items()))
    for key, label in (
        ("archived", "already archived (staged copy removed)"),
        ("duplicates", "set aside as duplicates"),
        ("excluded", "excluded, skipped and left staged"),
        ("unfiled", "unfiled"),
    ):
        if stats[key]:
            print(f"  {stats[key]} {label}")
    for key, label in (
        ("refused", "refused (a different file is in the way; left staged)"),
        ("unrecognized", "not a staged name (left alone)"),
    ):
        names: list[str] = stats[key]  # type: ignore[assignment]
        if names:
            print(f"  {len(names)} {label}:", file=sys.stderr)
            for name in names:
                print(f"    {name}", file=sys.stderr)
    _print_filed(stats.get("filed", {}))  # type: ignore[arg-type]
