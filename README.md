# wicket

Archive a Gmail mailbox to local `.eml` files, filed by sender domain and
queryable offline. **Read-only on the mailbox** by design: wicket can never send,
delete, flag, or modify a message. Pure Python standard library, an app password
instead of OAuth, no cloud project. What you keep lands in plain files under
`~/mail/`, and a small library lets other tools read it.

## What it does for you

- **A durable, searchable archive of mail you care about** (receipts, statements,
  travel). Fetch it once and keep it, even after the original leaves the mailbox.
- **A data source for other tools.** wicket exposes the held `.eml` and a manifest
  as a library, so a parser reads your mail without re-implementing IMAP.
- **A safe way to touch your mailbox.** Every operation is read-only; the worst it
  can do is download.

## Getting Started

```bash
# install (Python 3.12+)
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. Observe the mailbox into a year-sharded manifest (headers only, no bodies).
#    First run prompts for your Gmail address + app password (generate one at
#    myaccount.google.com/apppasswords) and saves it 0600 to
#    ~/.config/gmail/you@gmail.com/imap.json.
wicket-catalog --account you@gmail.com

# 2. Download full .eml for one or more sender domains, filed under <domain>/YYYY-MM/.
wicket-fetch --account you@gmail.com --domains chase.com,amazon.com

# 3. Read the manifest, no IMAP: who emails you most, every address ever seen.
wicket-report --account you@gmail.com --senders
```

Everything lives under `~/mail/you@gmail.com/`: the manifest in
`manifest/YYYY.jsonl`, the downloaded `.eml` in `archive/<domain>/YYYY-MM/`. Add
`--dry-run` to a verb to preview without writing. Once a single account exists
under `~/mail`, `--account` is optional (or set `$WICKET_ACCOUNT`).

## Using wicket

**Commands.** Three verbs over one year-sharded manifest:

| Verb | What it does |
|---|---|
| `wicket-catalog` | Observe the mailbox (headers only) into the manifest, recording **what exists**. |
| `wicket-fetch` | Download full `.eml` for matching threads, filed by sender domain, recording **what is held**. |
| `wicket-report` | Read-only summaries over the manifest (top senders, every address). No IMAP. |

Each message's state is two booleans on its manifest row, `deleted` and
`downloaded`. The design is recorded in
[`docs/adr/0002-unified-message-manifest.md`](docs/adr/0002-unified-message-manifest.md).

**How it's structured.** Each verb is a self-contained vertical: the library
(`lib`) does the work over IMAP and the manifest, `api` owns orchestration
(resolve the account, its paths, and credentials, then dispatch), and each face
is a thin wrapper on `api`, the CLI today and an MCP server later. Import a verb
from its vertical (`wicket.<verb>.api`); the package root is a namespace, not an
API surface. Every entry point takes `account=` (multi-user, n accounts); a single
account under `~/mail` is the default.

**Example.** wicket is meant to be depended on, not copied. Drive the verbs or
read the held mail and manifest directly:

```python
from wicket.catalog.api import catalog
from wicket.fetch.api import fetch, FetchOptions, held_messages
from wicket.report.api import report, manifest
from wicket.config import resolve_archive_dir

# drive the verbs (each returns a stats dict; non-interactive by default)
catalog(account="you@gmail.com")                    # observe into the manifest
fetch(                                              # download matching .eml
    account="you@gmail.com",
    options=FetchOptions(domains=["chase.com", "amazon.com"]),  # or query="..."
)
counts = report(account="you@gmail.com")            # one-screen summary

# read the held mail and manifest (no IMAP, no credentials)
for row, eml in held_messages(domains={"delta.com", "united.com"}):
    itinerary = parse_flight(eml.read_bytes())      # the consumer's own parser

rows = manifest()                  # the whole year-sharded store, {id: row}
archive = resolve_archive_dir("you@gmail.com")
```

To depend on wicket from another project, pin a git tag:

```toml
dependencies = ["wicket @ git+https://github.com/vishalapte/wicket.git@main"]
```

## When, where, and why

**Providers.** Today every verb speaks **Gmail** over IMAP (Gmail's `X-GM-*`
extensions). **Fastmail** support is planned behind a provider interface, selected
per run with `--source {gmail|fastmail}`. The two providers do not share a search
dialect; the portable query grammar that reconciles them is specified in
[`docs/adr/0001-portable-query-grammar.md`](docs/adr/0001-portable-query-grammar.md),
with the living key-by-key divergence table in
[`docs/QUERY-FIDELITY.md`](docs/QUERY-FIDELITY.md).

**Why it exists.** The mail layer started inside `factotum` (a personal
kitchen-sink repo) but is general-purpose: more than one project wants to fetch
and archive mail. wicket is that layer extracted into a standalone library so its
consumers depend on it rather than copy it.

## Contributing

This repo follows the racecar standards. Set up with `make install-dev` (adds the
dev tools and pre-commit hooks), then run `make check` before opening a change;
`make help` lists every target. Agent-facing rules live in
[`CLAUDE.md`](CLAUDE.md) (repo-level) and
[`src/wicket/CLAUDE.md`](src/wicket/CLAUDE.md) (package-level). Full usage and the
security model are in the package README:
[`src/wicket/README.md`](src/wicket/README.md).

## License

MIT. See the [`LICENSE`](LICENSE) file.
