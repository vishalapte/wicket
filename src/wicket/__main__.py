"""CLI entry: python -m wicket

Pattern 2: discovery plus its own CLI. Four verbs -- catalog, fetch, report,
ingest -- are argparse subcommands on this one node (`python -m wicket <verb>
...`) rather than separate `wicket.<verb>` dotted CLI entries: a dotted path
would read as a noun/sub-noun, and none of these are things you address, they
are actions. Their argument definitions live in their own (non-entry)
`cli.py`; there is no `__main__.py` under catalog/fetch/report/ingest, so
`python -m wicket.catalog` is refused by the interpreter itself, and
`python -m wicket catalog` is the only way in.

The owner-authored maps are nouns, each its own node: `python -m wicket.alias`
(domain-aliases.json) and `python -m wicket.store` (domain-routes.json and
account-aliases.json). So is the HALT, `python -m wicket.halt`, the one door that sets
and clears it (each logged), and the account, `python -m wicket.account`, a folder in
~/.config/wicket/accounts holding an imap.json link. This node lists the four in
`commands()` beside its verbs; a bare `python -m wicket` prints them, runnable as printed.
"""

import argparse
import signal
import sys
from typing import Protocol

from wicket.catalog import cli as catalog_cli
from wicket.fetch import cli as fetch_cli
from wicket.ingest import cli as ingest_cli
from wicket.lib.cli import print_commands
from wicket.report import cli as report_cli


class _VerbCLI(Protocol):
    """The shape every verb's `cli.py` exposes: `parser()` + `dispatch(args)`."""

    def parser(self) -> argparse.ArgumentParser: ...
    def dispatch(self, args: argparse.Namespace) -> int: ...


_VERBS: dict[str, _VerbCLI] = {
    "catalog": catalog_cli,
    "fetch": fetch_cli,
    "report": report_cli,
    "ingest": ingest_cli,
}


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
    ]


def _print_commands() -> None:
    """The bare-invocation listing: the nouns, then the verbs, each runnable as printed."""
    print_commands(__package__ or "wicket", commands(), subcommands())


def subcommands() -> list[tuple[str, str]]:
    return [
        ("catalog", "Observe the mailbox into the year-sharded manifest"),
        ("fetch", "Download .eml for matching threads, filed by sender domain"),
        ("report", "Read-only reports over the manifest (senders, addresses)"),
        ("ingest", "Additively file a local .eml folder into the manifest + archive"),
    ]


def parser() -> argparse.ArgumentParser:
    """Build the CLI parser (factory contract: introspectable by tooling)."""
    build = argparse.ArgumentParser(
        prog=f"python -m {__package__}",
        description=(
            "Portable, read-only mail tooling over IMAP: catalog a mailbox, "
            "fetch matching .eml, report over the manifest, or ingest a local "
            "export. Append --help to any verb for its options."
        ),
        epilog="nouns: "
        + ", ".join(f"python -m {__package__}.{name}" for name, _ in commands()),
    )
    sub = build.add_subparsers(dest="verb", required=True)
    help_by_verb = dict(subcommands())
    # One literal add_parser() call per verb, not a loop over subcommands():
    # check_cli_commands.py statically audits that every registered name is a
    # string literal, so it can cross-check subcommands() against the tree
    # without running it. A dynamic name would be unauditable.
    sub.add_parser(
        "catalog",
        parents=[catalog_cli.parser()],
        help=help_by_verb["catalog"],
        description=catalog_cli.parser().description,
    )
    sub.add_parser(
        "fetch",
        parents=[fetch_cli.parser()],
        help=help_by_verb["fetch"],
        description=fetch_cli.parser().description,
    )
    sub.add_parser(
        "report",
        parents=[report_cli.parser()],
        help=help_by_verb["report"],
        description=report_cli.parser().description,
    )
    sub.add_parser(
        "ingest",
        parents=[ingest_cli.parser()],
        help=help_by_verb["ingest"],
        description=ingest_cli.parser().description,
    )
    return build


def main() -> int:
    """Hold the parser, describe on a bare invocation, else dispatch to the resolved verb."""
    # Streaming output (`report`, in particular) should let a closed downstream
    # pipe (`| head`) end the process quietly via SIGPIPE rather than raising a
    # BrokenPipeError traceback.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    built = parser()
    if not sys.argv[1:]:
        _print_commands()
        return 0
    args = built.parse_args()
    return _VERBS[args.verb].dispatch(args)


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
            "    gone from Gmail        41  (only copy)\n"
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
    sys.exit(main())
