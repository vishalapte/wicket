"""CLI entry: python -m wicket.ingest

Pattern 3 (leaf CLI): argparse owns this layer; additive local ``.eml`` ingest
lives in the worker it dispatches to. No IMAP, no credentials.
"""

import argparse
import sys
from pathlib import Path

from wicket.config import ACCOUNT_ENV_VAR, resolve_account, resolve_store_dir
from wicket.ingest.api import IngestOptions, ingest


def commands() -> list[tuple[str, str]]:
    return []  # leaf — no sub-packages


def parser() -> argparse.ArgumentParser:
    """Build the CLI parser (factory contract: introspectable by tooling)."""
    build = argparse.ArgumentParser(
        prog="python -m wicket.ingest",
        description=(
            "Ingest a flat folder of .eml (an Outlook / Apple Mail drag-export) "
            "into the year-sharded manifest + archive, for a mailbox wicket "
            "cannot reach over IMAP. Additive and non-destructive: a message "
            "already archived is left untouched, only new ones are filed, and "
            "nothing is ever deleted. Re-running is idempotent."
        ),
    )
    build.add_argument(
        "--src",
        type=Path,
        required=True,
        help="Folder of .eml to ingest (flat drag-export; read-only, never modified).",
    )
    build.add_argument(
        "--account",
        default=None,
        help="Account address; scopes the manifest store and archive. "
        "Gmail `+tag` aliases are normalized automatically. "
        f"Required if ${ACCOUNT_ENV_VAR} is unset.",
    )
    build.add_argument(
        "--source",
        default="local",
        choices=["local"],
        help="Export profile (only 'local' today: RFC822 .eml on disk).",
    )
    build.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report what would be added; write nothing.",
    )
    return build


def run(args: argparse.Namespace) -> int:
    """Parse argv into typed options, call the api, render the summary."""
    try:
        options = IngestOptions(
            src=args.src.expanduser(), source=args.source, dry_run=args.dry_run
        )
        stats = ingest(args.account, options=options)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    store_dir = resolve_store_dir(resolve_account(args.account))
    verb = "would add" if args.dry_run else "added"
    by_year = ", ".join(f"{y}:{n}" for y, n in stats["added_by_year"].items())
    print(
        f"done: {stats['files']} file(s), {stats['unique']} unique, "
        f"{stats['dup']} in-folder dup(s); {verb} {stats['added']} new "
        f"message(s) to {store_dir}"
        + (f" ({by_year})" if by_year else "")
        + (f"; {stats['unfiled']} unfiled" if stats["unfiled"] else "")
    )
    return 0


def main() -> int:
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
