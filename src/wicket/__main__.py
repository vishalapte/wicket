"""CLI entry: python -m wicket

Portable, read-only mail tooling over IMAP: catalog a mailbox, fetch matching .eml,
report over the manifest, or ingest a local export.

The package's own command line: it lists the nouns this package composes in ``commands()``
and runs the verbs declared on the package itself, ``catalog``, ``fetch``, ``report`` and
``ingest`` (`python -m wicket <verb> ...`). Their code is ``wicket.root``, so a root verb
is addressed like any other noun's, from ``api`` to its renderer.

A thin surface over ``api``: each verb is one ``_cmd_<verb>`` that calls one api function
and hands its result to a renderer. It decides nothing and prints nothing itself. Every
verb takes ``--json``: the api result as JSON, the shape ``output()`` publishes, instead of
the text view.

The owner-authored maps are nouns, each its own node: `python -m wicket.alias`
(domain-aliases.json) and `python -m wicket.store` (domain-routes.json and
account-aliases.json). So is the HALT, `python -m wicket.halt`, the one door that sets
and clears it (each logged), and the account, `python -m wicket.account`, a folder in
~/.config/wicket/accounts holding an imap.json link. This node lists the four in
`commands()` beside its verbs; a bare `python -m wicket` prints them, runnable as printed.
``main()`` is ``lib.cli.run``: an ``ApiError`` the api raises is printed on stderr and its
``exit_code`` returned.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from wicket.env import ACCOUNT_ENV_VAR, DEFAULT_THREADS
from wicket.errors import ApiError
from wicket.lib.cli import NounParser, print_commands, run
from wicket.lib.renderer import json as json_renderer
from wicket.root import api
from wicket.root.lib.renderer import plaintext
from wicket.schema import returns


def commands() -> list[tuple[str, str]]:
    return [
        (
            "alias",
            (
                "A domain that is really another: their domain → the domain "
                "it files under (domain-aliases.json)."
            ),
        ),
        (
            "store",
            (
                "Which store mail goes to: a sender domain (--from, "
                "domain-routes.json) or one of your addresses (--to, "
                "account-aliases.json) → a store under the mail root."
            ),
        ),
        (
            "halt",
            (
                "A HALT stops every IMAP connection to an account until it is "
                "cleared: a file under ~/.local/state/wicket/halt/ (present means "
                "halted), each set and clear logged in log/<account>.log."
            ),
        ),
        (
            "account",
            (
                "An account is a folder, ~/.config/wicket/accounts/<name>/, holding "
                "an imap.json link to its credential file."
            ),
        ),
        (
            "exclude",
            (
                "A mailing list whose mail is never fetched or filed "
                "(excluded-lists.json), for every account or one."
            ),
        ),
    ]


def _print_commands() -> None:
    """The bare-invocation listing: the nouns, then the verbs, each runnable as printed."""
    print_commands(__package__ or "wicket", commands(), subcommands())


def _cmd_catalog(args: argparse.Namespace) -> int:
    result = api.catalog_root(
        mail_account=args.mail_account,
        years=args.years,
        full=args.full,
        mailbox=args.mailbox,
        store_dir=args.store_dir,
        state_dir=args.state_dir,
        threads=args.threads,
        interactive=(not args.non_interactive) and sys.stdin.isatty(),
        dry_run=args.dry_run,
    )
    return json_renderer.show(args, result, plaintext.print_catalog)


def _cmd_fetch(args: argparse.Namespace) -> int:
    result = api.fetch_root(
        mail_account=args.mail_account,
        query=args.query,
        domains=args.domains,
        last=args.last,
        limit=args.limit,
        target=args.target,
        alias_file=args.alias_file,
        state_dir=args.state_dir,
        threads=args.threads,
        interactive=(not args.non_interactive) and sys.stdin.isatty(),
        dry_run=args.dry_run,
    )
    return json_renderer.show(args, result, plaintext.print_fetch)


def _cmd_report(args: argparse.Namespace) -> int:
    result = api.report_root(
        mail_account=args.mail_account,
        senders=args.senders,
        addresses=args.addresses,
        bucket=args.bucket,
    )
    return json_renderer.show(args, result, plaintext.print_report)


def _cmd_ingest(args: argparse.Namespace) -> int:
    result = api.ingest_root(
        mail_account=args.mail_account,
        src=args.src,
        staged=args.staged,
        source=args.source,
        domain=args.domain,
        tags=args.tag,
        no_delete=args.no_delete,
        force=args.force,
        dry_run=args.dry_run,
    )
    return json_renderer.show(args, result, plaintext.print_ingest)


def subcommands() -> list[tuple[str, str]]:
    return [
        ("catalog", "Observe the mailbox into the year-sharded manifest"),
        ("fetch", "Download .eml for matching threads, filed by sender domain"),
        ("report", "Read-only reports over the manifest (senders, addresses)"),
        ("ingest", "Additively file a local .eml folder into the manifest + archive"),
    ]


def parser() -> argparse.ArgumentParser:
    """Build the parser without parsing: the audit and --help both read this."""
    p = NounParser(
        prog="python -m wicket",
        description=(
            "Portable, read-only mail tooling over IMAP: catalog a mailbox, "
            "fetch matching .eml, report over the manifest, or ingest a local "
            "export. Append --help to any verb for its options."
        ),
        nodes=commands(),
    )
    sub = p.verbs(subcommands(), dest="phase")

    p_catalog = sub.add_parser(
        "catalog",
        description=(
            "Sweep mailbox headers (never bodies) into the year-sharded "
            "manifest at <mail-root>/<account>/manifest/YYYY.jsonl, one row per "
            "message (the observation fields and the `deleted` flag). Merges "
            "with settlement already in the store; re-running is idempotent."
        ),
    )
    p_catalog.add_argument(
        "--mail-account",
        default=None,
        help="Account address; scopes the manifest store. "
        "`+tag` aliases (you+x@...) are normalized automatically. "
        f"Default: ${ACCOUNT_ENV_VAR}, else the only account folder.",
    )
    p_catalog.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Override the manifest store dir. Default: <mail-root>/<account>/manifest/.",
    )
    p_catalog.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="The base holding accounts/<account>/imap.json (email + app password) "
        "(default: ~/.config/wicket; shared with the other tools).",
    )
    p_catalog.add_argument(
        "--mailbox",
        default=None,
        help="IMAP mailbox to sweep (default: the provider's all-mail folder, "
        'Gmail\'s "[Gmail]/All Mail"; Fastmail has none, walks every folder and '
        "refuses --mailbox, exit 2). Sweeping any other mailbox into the default "
        "store dir would make shards lie about what exists, pass --store-dir too.",
    )
    p_catalog.add_argument(
        "--years",
        default=None,
        help="Comma-separated INTERNALDATE years, e.g. 2025,2026. Only these "
        "years' shards are replaced. Omit to sweep incrementally from the "
        "latest year in the manifest; pass --full for a complete re-sweep.",
    )
    p_catalog.add_argument(
        "--threads",
        type=int,
        default=DEFAULT_THREADS,
        help="Concurrent year workers, one IMAP connection each (default: 4; "
        "providers cap simultaneous IMAP connections; Gmail's is ~15).",
    )
    p_catalog.add_argument(
        "--dry-run",
        action="store_true",
        help="Sweep and report per-year counts; write nothing.",
    )
    p_catalog.add_argument(
        "--full",
        action="store_true",
        help="Re-sweep every year from the oldest message through now, not just "
        "from the latest year in the manifest. The incremental default assumes "
        "the past does not change; use --full to rebuild or after deleting old "
        "mail.",
    )
    p_catalog.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt for credentials; fail loudly if imap.json is "
        "missing or rejected (for cron/launchd).",
    )
    p_catalog.set_defaults(func=_cmd_catalog)

    p_fetch = sub.add_parser(
        "fetch",
        description=(
            "Get full .eml for every matching message into the store's staging dir "
            "(<mail-root>/<account>/staging/; Gmail's <thread>/<msgid>@<date>.eml, "
            "Fastmail's under fastmail/). The provider is where the account's "
            "imap.json links: ~/.config/gmail/ or ~/.config/fastmail/. "
            "fetch files nothing: `wicket ingest --staged` files staged mail into "
            "the archive, and until then it is not in report or the manifest's "
            "downloaded rows. With no --domains, --query or --last, gets every "
            "message not yet got and not excluded, this year, last year, then "
            "older years oldest first, until the provider throttles: the first throttle "
            "writes the account's HALT and exits 2, and no IMAP connection opens "
            "past a HALT. Exits 2 when another process holds the store's lock."
        ),
    )
    filter_group = p_fetch.add_mutually_exclusive_group()
    filter_group.add_argument(
        "--domains",
        help='Comma-separated list, e.g. "acme.com,globex.com". '
        "Tool builds the Gmail search expression for messages from/to "
        "any of these domains; Fastmail refuses it, exit 2. Omit every filter "
        "to fetch everything.",
    )
    filter_group.add_argument(
        "--query",
        help="Raw Gmail search expression (escape hatch). Use this for "
        "anything --domains can't express. Fastmail refuses it, exit 2.",
    )
    filter_group.add_argument(
        "--last",
        metavar="AGE",
        help="Messages newer than AGE: N then d, m or y (2d, 3m, 1y): Gmail's "
        "newer_than:AGE, Fastmail's SINCE that date. Anything else exits 2.",
    )
    p_fetch.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Refused: fetch no longer files mail, so it takes no destination. "
        "Bodies go to <mail-root>/<account>/staging/; `wicket ingest --staged` "
        "files them.",
    )
    p_fetch.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="The base holding accounts/<account>/imap.json (email + app password) "
        "(default: ~/.config/wicket; shared with wicket.catalog).",
    )
    p_fetch.add_argument(
        "--mail-account",
        default=None,
        help="Account that scopes the staging dir and the manifest store "
        "(<mail-root>/<account>/). `+tag` aliases are normalized "
        "automatically. Default: $WICKET_ACCOUNT, else the only account folder.",
    )
    p_fetch.add_argument(
        "--alias-file",
        type=Path,
        default=None,
        help="JSON file of domain aliases, shape "
        '{"primary.com": ["alias1.com", "alias2.com"]}. '
        "Search expands to include aliases; the filing domain canonicalizes "
        "to primary. Default: <state-dir>/domain-aliases.json (silently "
        "skipped if absent).",
    )
    p_fetch.add_argument(
        "--max",
        type=int,
        default=None,
        dest="limit",
        metavar="N",
        help="Stop after N new messages this run (after the store dedup), "
        "across every year of an unfiltered run.",
    )
    p_fetch.add_argument(
        "--threads",
        type=int,
        default=DEFAULT_THREADS,
        help="Concurrent thread-processing workers, one IMAP connection "
        "each (default: 4; providers cap simultaneous connections, Gmail's at ~15).",
    )
    p_fetch.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan; download nothing; write nothing.",
    )
    p_fetch.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt for credentials; fail loudly if imap.json is "
        "missing or rejected (for cron/launchd).",
    )
    p_fetch.set_defaults(func=_cmd_fetch)

    p_report = sub.add_parser(
        "report",
        description=(
            "Read-only reports over the year-sharded manifest (no IMAP). With "
            "no flag, prints a one-screen summary; otherwise one record per line."
        ),
    )
    view = p_report.add_mutually_exclusive_group()
    view.add_argument(
        "--senders",
        action="store_true",
        help="Every From address with its message count, descending "
        "(tab-separated `count<TAB>address`).",
    )
    view.add_argument(
        "--addresses",
        action="store_true",
        help="Every distinct address seen in From or To, sorted.",
    )
    view.add_argument(
        "--bucket",
        metavar="NAME",
        help="Every message ANY store holds that this bucket claims (travel, "
        "shopping), grouped by store. A view, not a location: mail owned by a "
        "mailbox stays in that mailbox's store. Ignores --mail-account.",
    )
    p_report.add_argument(
        "--mail-account",
        default=None,
        help="Account address; scopes the manifest store. `+tag` aliases "
        f"are normalized. Default: ${ACCOUNT_ENV_VAR}, else the only account folder.",
    )
    p_report.set_defaults(func=_cmd_report)

    p_ingest = sub.add_parser(
        "ingest",
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
    source = p_ingest.add_mutually_exclusive_group(required=True)
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
    p_ingest.add_argument(
        "--mail-account",
        default=None,
        help="Send every thread to this one account, instead of routing each to "
        "the account it was addressed to. A destination, NOT a filter: it does "
        "not select which messages are ingested, so naming the wrong one files "
        "the whole folder into the wrong store (refused unless --force). Omit it "
        "to route.",
    )
    p_ingest.add_argument(
        "--source",
        default="local",
        choices=["local"],
        help="Export profile (only 'local' today: RFC822 .eml on disk).",
    )
    p_ingest.add_argument(
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
    p_ingest.add_argument(
        "--tag",
        action="append",
        default=[],
        help="A tag recorded in the manifest `labels` for every newly-added "
        "message (a flat .eml export carries no provider labels); repeatable "
        "(--tag a --tag b). Applied only to messages filed this run; "
        "already-archived rows are left untouched.",
    )
    p_ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report what would be added; write nothing.",
    )
    p_ingest.add_argument(
        "--force",
        action="store_true",
        help="Ingest even when the source is addressed to a different account "
        "than --mail-account. Refused by default: --mail-account names the "
        "destination store, it does not filter the source.",
    )
    p_ingest.add_argument(
        "--no-delete",
        action="store_true",
        help="Leave the source files untouched. By default an ingested .eml is "
        "moved to ~/.Trash once it is durably archived (filed now, or already in "
        "the store), because the archive is the record and the source is a "
        "volatile inbox. A message whose thread could not be routed always keeps "
        "its file, and nothing is ever unlinked: the Trash is the undo.",
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    for verb_parser in sub.choices.values():
        json_renderer.add_flag(verb_parser)
    return p


def main(argv: list[str] | None = None) -> int:
    """Run one verb; with none, list the nouns and the verbs."""
    # Streaming output (`report`, in particular) should let a closed downstream
    # pipe (`| head`) end the process quietly via SIGPIPE rather than raising a
    # BrokenPipeError traceback.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    # The workers' progress goes through `logging`; this shows it on stderr, as printed.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run(
        parser(), argv, refusal=ApiError, error=plaintext.error, listing=_print_commands
    )


def output() -> list[tuple[dict[str, object], dict[str, object]]]:
    """(params, schema) per verb, generated from each api function's return type."""
    return [({"phase": verb}, returns(fn)) for verb, fn in sorted(api.VERBS.items())]


def example_output() -> list[tuple[str, str, str, int]]:
    """Illustrative sample transcripts for the CLI-docs generator (arch-python/CLI.md).

    Never executed, never real: every account, domain, path and count below is
    fabricated. One scenario per verb (catalog/fetch/report/ingest), one for the
    store and halt nouns, plus the shape a bad `--mail-account` actually reports, so a reader sees a
    representative range without pulling any figure from an actual mailbox.
    """
    return [
        (
            "catalog: incremental sweep",
            "wicket catalog --mail-account you@example.com",
            "done: observed 128 message(s); wrote 2 year shard(s) under "
            "~/.delphi/mail/you@example.com/manifest\n",
            0,
        ),
        (
            "fetch: dry run",
            "wicket fetch --domains acme.com,globex.com --dry-run "
            "--mail-account you@example.com",
            "done (dry-run): 14 to download, 3 already got. Nothing written.\n",
            0,
        ),
        (
            "ingest: file what fetch staged",
            "wicket ingest --staged --mail-account you@example.com",
            "staged: 14 file(s); filed 14 to you@example.com (2026:14)\n"
            "  by rule: rule 11, self 1, sent 2\n"
            "    acme.example/2026-07/\n"
            "      18f2a3b4c5d6e7f8.eml\n",
            0,
        ),
        (
            "report: one-screen summary",
            "wicket report --mail-account you@example.com",
            "manifest: ~/.delphi/mail/you@example.com/manifest\n"
            "  messages               3241\n"
            "  downloaded             2987  (held locally)\n"
            "    gone from mailbox      41  (only copy)\n"
            "    still in mailbox     2946\n"
            "  observed only            254  (not held)\n"
            "  distinct senders         318\n"
            "  distinct addresses      512\n",
            0,
        ),
        (
            "ingest: routed local export",
            "wicket ingest --src ~/Downloads/export",
            "read: 40 file(s), 37 unique, 3 in-folder dup(s)\n"
            "  added 22 to you@example.com (2026:22); 9 already archived\n"
            "  added 6 to travel (2026:6)\n"
            "  trashed: 37 source file(s) -> ~/.Trash\n",
            0,
        ),
        (
            "store: list each store's senders and addresses",
            "python -m wicket.store list",
            "travel\n  from: example-air.test\n  to:   reservations@example-travel.test\n",
            0,
        ),
        (
            "halt: list halted accounts",
            "python -m wicket.halt list",
            "account          halted                age\n"
            "you@example.com  2026-10-27 08:00 IST  3h12m\n",
            0,
        ),
        (
            "error: unknown account",
            "wicket report --mail-account nobody@example.com",
            "unknown account 'nobody@example.com': it has no store under "
            "~/.delphi/mail and is not listed in account-aliases.json. If it "
            "is an address you receive at, add it as an alias of the account "
            "that owns it; if it is genuinely a new mailbox, create its store "
            "directory first.\n",
            2,
        ),
    ]


if __name__ == "__main__":
    raise SystemExit(main())
