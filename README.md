# wicket

Archive a Gmail mailbox to local `.eml` files, filed by sender domain and
queryable offline. **Read-only on the mailbox** by design: wicket can never send,
delete, flag, or modify a message. Pure Python standard library (no dependencies),
an app password instead of OAuth, no cloud project. Secrets live under
`~/.config/gmail/`; what you keep lands in plain files under `~/mail/`; and a small
library lets other tools read it.

## What it does for you

- **A durable, searchable archive of mail you care about** (receipts, statements,
  travel). Fetch it once and keep it, even after the original leaves the mailbox.
- **A data source for other tools.** wicket exposes the held `.eml` and a manifest
  as a library, so a parser reads your mail without re-implementing IMAP.
- **A safe way to touch your mailbox.** Every operation is read-only; the worst it
  can do is download.

## Getting started

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

Add `--dry-run` to `catalog`, `fetch`, or `ingest` to preview without writing.
Once a single account exists under `~/mail`, `--account` is optional (or set
`$WICKET_ACCOUNT`).

For a mailbox wicket **can't** reach over IMAP (e.g. Microsoft 365 with
basic-auth IMAP disabled), drag-export the messages to `.eml` from your desktop
client and file them into the same manifest + archive offline:

```bash
# 4. Ingest a local folder of .eml into ~/mail/<account>/, additively (no IMAP).
wicket-ingest --src ~/Downloads/Outlook --account you@work.com
```

## Commands

Four verbs over one year-sharded manifest: three that speak to the mailbox over
IMAP, and `ingest` that files a local export offline. Each ships as a console
script (`wicket-<verb>`) and an equivalent module (`python -m wicket.<verb>`);
`python -m wicket` lists them.

| Verb | What it does |
|---|---|
| `wicket-catalog` | Observe the mailbox (headers only) into the manifest, recording **what exists**. |
| `wicket-fetch` | Download full `.eml` for matching threads, filed by sender domain, recording **what is held**. |
| `wicket-report` | Read-only summaries over the manifest (top senders, every address). No IMAP. |
| `wicket-ingest` | File a local folder of `.eml` (a desktop-client drag-export) into the manifest + archive, **additively**. Offline, for mailboxes wicket can't reach over IMAP. Never deletes. |

Each message's state is two booleans on its manifest row, `deleted` and
`downloaded`. The design is recorded in
[`docs/adr/0002-unified-message-manifest.md`](docs/adr/0002-unified-message-manifest.md).

## CLI reference

Every flag below is what the code accepts today; `wicket-<verb> --help` is the
authoritative, live source. Read-only verbs never prompt when `--non-interactive`
is set (for cron/launchd), and fail loudly instead.

**`wicket-catalog`** sweeps mailbox headers into `~/mail/<account>/manifest/YYYY.jsonl`.
Idempotent, incremental by default.

| Flag | Meaning |
|---|---|
| `--account ACCOUNT` | Account address; scopes the manifest store. Gmail `+tag` aliases normalized. Required if `$WICKET_ACCOUNT` is unset. |
| `--store-dir DIR` | Override the manifest store dir. Default `~/mail/<account>/manifest/`. |
| `--state-dir DIR` | Where `imap.json` lives. Default `~/.config/gmail/`. |
| `--mailbox NAME` | IMAP mailbox to sweep. Default `"[Gmail]/All Mail"`. |
| `--years Y,Y` | Comma-separated years to replace, e.g. `2025,2026`. Omit for incremental. |
| `--full` | Re-sweep every year from the oldest message through now (rebuild). |
| `--threads N` | Concurrent year workers, one IMAP connection each. Default 4. |
| `--dry-run` | Sweep and report per-year counts; write nothing. |
| `--non-interactive` | Never prompt; fail loudly if `imap.json` is missing or rejected. |

**`wicket-fetch`** downloads `.eml` for matching threads into
`~/mail/<account>/archive/<domain>/YYYY-MM/`. Pass exactly one of `--domains` / `--query`.

| Flag | Meaning |
|---|---|
| `--domains A,B` | Comma-separated domains; builds the Gmail from/to search. (mutually exclusive with `--query`) |
| `--query EXPR` | Raw Gmail search expression (escape hatch). |
| `--dest DIR` | Destination root for `.eml`. Default `~/mail/<account>/archive/`. |
| `--alias-file PATH` | Domain-alias JSON `{"primary.com": ["alias.com", "*.primary.com"]}`. Default `~/.config/gmail/<account>/domain-aliases.json` (skipped if absent). |
| `--max N` | Stop after N new messages this run (after store dedup). |
| `--account ACCOUNT` | Scopes `--dest` and the manifest store. Required if `$WICKET_ACCOUNT` is unset. |
| `--state-dir DIR` | Where `imap.json` lives. Default `~/.config/gmail/`. |
| `--threads N` | Concurrent per-thread workers, one IMAP connection each. Default 4. |
| `--dry-run` | Print the plan; download nothing; do not update the manifest. |
| `--non-interactive` | Never prompt; fail loudly if `imap.json` is missing or rejected. |

**`wicket-report`** gives read-only summaries over the manifest (no IMAP, no credentials).
With no flag, prints a one-screen summary.

| Flag | Meaning |
|---|---|
| `--senders` | Every From address with its message count, descending (`count<TAB>address`). |
| `--addresses` | Every distinct address seen in From or To, sorted. |
| `--account ACCOUNT` | Scopes the manifest store. Required if `$WICKET_ACCOUNT` is unset. |

**`wicket-ingest`** files a flat folder of `.eml` into
`~/mail/<account>/{manifest,archive}/`, for a mailbox wicket can't reach over
IMAP. No IMAP, no credentials, no prompt. **Additive and non-destructive:** a
message already archived (by its portable `Message-ID`) is left untouched, only
new ones are filed, and nothing is ever deleted — so removing a file from the
source folder can never remove it from the archive. Re-running is idempotent.
Threads are reconstructed from headers and each message files under its
counterparty domain, exactly as `fetch` does.

| Flag | Meaning |
|---|---|
| `--src DIR` | Folder of `.eml` to ingest (a flat drag-export). Read-only; never modified. **Required.** |
| `--account ACCOUNT` | Scopes the manifest store and archive. Required if `$WICKET_ACCOUNT` is unset. |
| `--source {local}` | Export profile. Only `local` today (RFC822 `.eml` on disk); reserves the flag for future kinds. |
| `--dry-run` | Parse and report what would be added; write nothing. |

## Where config lives

Secrets and per-account settings live **outside the data tree**, under
`~/.config/gmail/<account>/` (dir `0700`), and are never committed:

| Path | What | Notes |
|---|---|---|
| `~/.config/gmail/<account>/imap.json` | Gmail address + app password | mode `0600`; seeded on first run; never on the command line. Override the base dir with `--state-dir`. |
| `~/.config/gmail/<account>/domain-aliases.json` | Optional sender-domain aliases | `{"primary.com": ["alias.com", "*.primary.com"]}`; folds aliases/subdomains onto a primary when filing. Override with `--alias-file`. |
| `$WICKET_ACCOUNT` (env) | Default account | Used when `--account` is omitted; else the sole directory under `~/mail`. |

An app password is account-scoped and revocable at
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
that is the kill switch. Full security model:
[`src/wicket/README.md`](src/wicket/README.md).

## Where data lives

Everything wicket writes lives under one per-account root, `~/mail/<account>/`:

| Path | What |
|---|---|
| `~/mail/<account>/manifest/YYYY.jsonl` | The year-sharded manifest, one JSON row per message (identity, observation, and settlement fields). Override with `--store-dir`. |
| `~/mail/<account>/archive/<domain>/YYYY-MM/<msg-id>.eml` | Downloaded messages, filed by sender/counterparty domain and month. Override with `--dest`. |

The manifest is a rebuildable cache: `downloaded` is ground-truthed against the
`.eml` on disk and the filing domain is derived from observation, so the whole
store can be reconstructed from mailbox + disk by re-running the verbs (ADR
[`0002`](docs/adr/0002-unified-message-manifest.md)).

## Using wicket as a library

wicket is meant to be depended on, not copied. Each verb is a self-contained
vertical: the library (`lib`) does the work over IMAP and the manifest, `api` owns
orchestration (resolve the account, its paths, and credentials, then dispatch), and
each face is a thin wrapper on `api` (the CLI today, an MCP server later). Import a
verb from its vertical (`wicket.<verb>.api`); the package root is a namespace, not
an API surface. Every entry point takes `account=`; a single account under `~/mail`
is the default.

```python
from pathlib import Path

from wicket.catalog.api import catalog
from wicket.fetch.api import fetch, FetchOptions, held_messages
from wicket.report.api import report, manifest
from wicket.ingest.api import ingest, IngestOptions
from wicket.config import resolve_archive_dir

# drive the verbs (each returns a stats dict; non-interactive by default)
catalog(account="you@gmail.com")                    # observe into the manifest
fetch(                                              # download matching .eml
    account="you@gmail.com",
    options=FetchOptions(domains=["chase.com", "amazon.com"]),  # or query="..."
)
counts = report(account="you@gmail.com")            # one-screen summary
ingest(                                             # file a local .eml export (no IMAP)
    account="you@work.com",
    options=IngestOptions(src=Path("~/Downloads/Outlook").expanduser()),
)

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

## Providers

Today the IMAP verbs (`catalog`, `fetch`) speak **Gmail** (Gmail's `X-GM-*`
extensions). **Fastmail** support is planned behind a provider interface,
selected per run with `--source {gmail|fastmail}`. The two providers do not share
a search dialect; the portable query grammar that reconciles them is specified in
[`docs/adr/0001-portable-query-grammar.md`](docs/adr/0001-portable-query-grammar.md),
with the living key-by-key divergence table in
[`docs/QUERY-FIDELITY.md`](docs/QUERY-FIDELITY.md).

When a mailbox can't be reached over IMAP at all — Microsoft 365 tenants with
basic-auth IMAP disabled and OAuth app-registration off the table — there is no
provider to select. `wicket-ingest` is the offline path instead: drag-export the
messages to `.eml` from a desktop client and ingest them into the same manifest +
archive, no server round-trip.

## Why it exists

The mail layer started inside `factotum` (a personal kitchen-sink repo) but is
general-purpose: more than one project wants to fetch and archive mail. wicket is
that layer extracted into a standalone library so its consumers depend on it rather
than copy it.

## Contributing

This repo follows the racecar standards. Set up with `make install-dev` (adds the
dev tools and pre-commit hooks), then run `make check` before opening a change;
`make help` lists every target. Notable changes are recorded in
[`CHANGELOG.md`](CHANGELOG.md). Agent-facing rules live in
[`CLAUDE.md`](CLAUDE.md) (repo-level) and
[`src/wicket/CLAUDE.md`](src/wicket/CLAUDE.md) (package-level). Full usage and the
security model are in the package README:
[`src/wicket/README.md`](src/wicket/README.md).

## License

MIT. See the [`LICENSE`](LICENSE) file.
</content>
</invoke>
