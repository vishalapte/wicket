---
summary: The human storefront: what wicket is, the four verbs, where the mail root lives, and the full CLI reference.
pnode: []
bearing: orientation
content_blind: true                 # public repo (github.com/vishalapte/wicket):
                                    # no deal-shaped figures in tracked prose,
                                    # only variables (docs-orchestrator/CONTENT_BLINDNESS.md)
# A standards citation is never a content-blindness candidate: an RFC number
# identifies a public document, not a person or a record, so it discloses
# nothing whatever it happens to satisfy. `RFC3339` satisfies the SEDOL check
# digit by coincidence, and wicket is mail tooling that carries no securities
# identifier of any kind — so the TYPE is off here, not the sentence rewritten.
content_blind_identifiers_off: [gb-sedol]
---

# wicket

Archive a Gmail mailbox to local `.eml` files, filed by sender domain and
queryable offline. **Read-only on the mailbox** by design: wicket can never send,
delete, flag, or modify a message. Pure Python standard library (no dependencies),
an app password instead of OAuth, no cloud project. Secrets live under
`~/.config/gmail/`; what you keep lands in plain files under the **mail root**
(`~/.delphi/mail/`, overridable with `$WICKET_MAIL_ROOT`); and a small library lets
other tools read it.

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
wicket catalog --account you@gmail.com

# 2. Download full .eml for one or more sender domains, filed under <domain>/YYYY-MM/.
wicket fetch --account you@gmail.com --domains chase.com,amazon.com

# 3. Read the manifest, no IMAP: who emails you most, every address ever seen.
wicket report --account you@gmail.com --senders
```

Add `--dry-run` to `catalog`, `fetch`, or `ingest` to preview without writing.
Once a single account exists under the mail root, `--account` is optional for the
IMAP verbs (or set `$WICKET_ACCOUNT`).

For a mailbox wicket **can't** reach over IMAP (e.g. Microsoft 365 with
basic-auth IMAP disabled), drag-export the messages to `.eml` from your desktop
client and file them into the same manifest + archive offline. Omit `--account`
and each thread is **routed** to the account it belongs to, so one mixed folder
lands in the right stores:

```bash
# 4. Ingest local .eml, routed per thread, additively (no IMAP). A folder,
#    or a single message. Ingested sources move to ~/.Trash once archived;
#    --no-delete keeps them.
wicket ingest --src ~/Downloads/Outlook
wicket ingest --src ~/Downloads/one-message.eml
```

To see mail grouped by topic across every store — travel, shopping — without
moving anything:

```bash
wicket report --bucket travel
```

## Commands

Four verbs over one year-sharded manifest: three that speak to the mailbox over
IMAP, and `ingest` that files a local export offline. These are subcommands of
one CLI, not separate dotted modules: the `wicket` console script and `python
-m wicket` are the same dispatcher (`wicket <verb> ...` == `python -m wicket
<verb> ...`); bare `python -m wicket` (or `wicket` with no args) prints this
list. `config` sits alongside them but is not a fifth action — it is a genuine
**noun** (the three owner-authored maps below), so it takes its own nested
subparsers instead of flags: `wicket config <account|domain> <aliases|routes>
<list|create|update|delete>`.

| Verb | What it does |
|---|---|
| `wicket catalog` | Observe the mailbox (headers only) into the manifest, recording **what exists**. |
| `wicket fetch` | Download full `.eml` for matching threads, filed by sender domain, recording **what is held**. |
| `wicket report` | Read-only summaries over the manifest (top senders, every address, a bucket across every store). No IMAP. |
| `wicket ingest` | File local `.eml` — a folder (a desktop-client drag-export) or a single message — into the manifest + archive, **additively**, routing each thread to the account it belongs to. Offline, for mailboxes wicket can't reach over IMAP. Never deletes an archived message. |
| `wicket config` | CRUD over `account-aliases.json` / `domain-aliases.json` / `domain-routes.json` instead of hand-editing them. No IMAP. |

Each message's state is two booleans on its manifest row, `deleted` and
`downloaded`. The design is recorded in
[`docs/adr/0002-unified-message-manifest.md`](docs/adr/0002-unified-message-manifest.md).

### Where a message goes

`ingest` routes **per thread**, never per message: a conversation you were
addressed on in some replies and cc'd on in others would otherwise split across
two stores, and a thread has exactly one `thread_id` and one folder.

Two rules decide the destination, and **the mailbox wins**:

1. **The account that took part** — anyone in `From`, `To`, `Cc`, `Delivered-To`,
   or `X-Original-To` that resolves to one of your accounts. The sender counts:
   mail *you sent* is addressed entirely to counterparties, so a
   recipients-only rule would strand every outbound thread.
2. **A bucket**, only when no mailbox claims the thread — an order to a burner
   address, a catch-all subdomain, a drag-export of a mailbox wicket cannot reach.

The order is not a preference. A manifest is the observation record *of a
mailbox*: `catalog` sweeps Gmail and writes what it sees into Gmail's manifest.
If a topical rule could pull a Gmail-owned message into `shopping`, the next
`catalog` would re-observe it in the mailbox and write it straight back, and the
same message would live in two stores forever. So a bucket is the **physical
home** only for mail no mailbox owns; grouping mailbox-owned mail by topic is a
**view** (`wicket report --bucket`), never a relocation.

Mail nothing claims is **left alone**: not filed, and its source file is not
trashed. Create a store (`mkdir <mail-root>/travel`) or add a mapping to claim it.

## CLI reference

Every flag below is what the code accepts today; `wicket <verb> --help` is the
authoritative, live source. Read-only verbs never prompt when `--non-interactive`
is set (for cron/launchd), and fail loudly instead.

**`wicket catalog`** sweeps mailbox headers into `<mail-root>/<account>/manifest/YYYY.jsonl`.
Idempotent, incremental by default.

| Flag | Meaning |
|---|---|
| `--account ACCOUNT` | Account address; scopes the manifest store. Gmail `+tag` aliases normalized. Required if `$WICKET_ACCOUNT` is unset. |
| `--store-dir DIR` | Override the manifest store dir. Default `<mail-root>/<account>/manifest/`. |
| `--state-dir DIR` | Where `imap.json` lives. Default `~/.config/gmail/`. |
| `--mailbox NAME` | IMAP mailbox to sweep. Default `"[Gmail]/All Mail"`. |
| `--years Y,Y` | Comma-separated years to replace, e.g. `2025, 2026`. Omit for incremental. |
| `--full` | Re-sweep every year from the oldest message through now (rebuild). |
| `--threads N` | Concurrent year workers, one IMAP connection each. Default 4. |
| `--dry-run` | Sweep and report per-year counts; write nothing. |
| `--non-interactive` | Never prompt; fail loudly if `imap.json` is missing or rejected. |

**`wicket fetch`** downloads `.eml` for matching threads into
`<mail-root>/<account>/archive/<domain>/YYYY-MM/`. Pass exactly one of `--domains` / `--query`.

| Flag | Meaning |
|---|---|
| `--domains A,B` | Comma-separated domains; builds the Gmail from/to search. (mutually exclusive with `--query`) |
| `--query EXPR` | Raw Gmail search expression (escape hatch). |
| `--dest DIR` | Destination root for `.eml`. Default `<mail-root>/<account>/archive/`. |
| `--alias-file PATH` | Subdomain-fold JSON `{"primary.com": ["alias.com", "*.primary.com"]}`. Default `<mail-root>/domain-aliases.json` (skipped if absent) — one home, shared with `ingest`. |
| `--max N` | Stop after N new messages this run (after store dedup). |
| `--account ACCOUNT` | Scopes `--dest` and the manifest store. Required if `$WICKET_ACCOUNT` is unset. |
| `--state-dir DIR` | Where `imap.json` lives. Default `~/.config/gmail/`. |
| `--threads N` | Concurrent per-thread workers, one IMAP connection each. Default 4. |
| `--dry-run` | Print the plan; download nothing; do not update the manifest. |
| `--non-interactive` | Never prompt; fail loudly if `imap.json` is missing or rejected. |

**`wicket report`** gives read-only summaries over the manifest (no IMAP, no credentials).
With no flag, prints a one-screen summary.

| Flag | Meaning |
|---|---|
| `--senders` | Every From address with its message count, descending (`count<TAB>address`). |
| `--addresses` | Every distinct address seen in From or To, sorted. |
| `--bucket NAME` | Every message **any** store holds that this bucket claims (`travel`, `shopping`), grouped by store. A view, not a location; ignores `--account`. |
| `--account ACCOUNT` | Scopes the manifest store. Required if `$WICKET_ACCOUNT` is unset. |

**`wicket ingest`** files local `.eml` — a flat folder, or a single message — into
the manifest + archive, for a mailbox wicket can't reach over IMAP. No IMAP, no credentials, no prompt.
**Additive:** a message already archived (by its portable `Message-ID`) is left
untouched, only new ones are filed, and no archived message is ever deleted.
Re-running is idempotent. Threads are reconstructed from headers, each thread is
routed to the store it belongs to (see [Where a message goes](#where-a-message-goes)),
and each files under its counterparty domain, exactly as `fetch` does.

**The source folder is not read-only.** Once a message is durably archived (filed
now, or already in the store), its `.eml` is **moved to `~/.Trash`** — the archive
is the record, the source folder is a volatile inbox. Nothing is ever unlinked:
the Trash is the undo. A message whose thread could not be routed always keeps its
file. `--dry-run` moves nothing.

| Flag | Meaning |
|---|---|
| `--src PATH` | What to ingest: a folder of `.eml` (a flat drag-export; not recursive), or a single `.eml` file. **Required.** |
| `--account ACCOUNT` | Send **every** thread to this one store instead of routing each. A *destination, not a filter*: it does not select which messages are ingested, so naming the wrong one files the whole folder into the wrong store. Refused when the folder is plainly addressed elsewhere (see `--force`). Omit it to route. |
| `--source {local}` | Export profile. Only `local` today (RFC822 `.eml` on disk); reserves the flag for future kinds. |
| `--domain DOMAIN` | File every newly-added message under this one domain folder (`<domain>/YYYY-MM/`), overriding the computed counterparty domain. A *destination, not a filter*: it does not select which messages are ingested. A bare domain like `acme.com`. A reply that joins an already-archived thread still files with its thread, so this only redirects mail with no archived home yet. |
| `--tags a,b,c` | Comma-separated tags recorded as the manifest `labels` for every newly-added message (a flat export carries no provider labels). Applied only to mail filed this run; archived rows are untouched. |
| `--force` | Ingest anyway when `--account` disagrees with the folder's recipients. |
| `--no-delete` | Leave the source folder untouched (do not move archived `.eml` to `~/.Trash`). |
| `--dry-run` | Parse and report what would be added; write nothing, trash nothing. |

The run summary lists each filed `.eml` grouped by its archive directory
(`<domain>/YYYY-MM/`), so you can see exactly what landed where.

## Where config lives

**Secrets** live outside the data tree, under `~/.config/gmail/<account>/` (dir
`0700`), and are never committed:

| Path | What | Notes |
|---|---|---|
| `~/.config/gmail/<account>/imap.json` | Gmail address + app password | mode `0600`; seeded on first run; never on the command line. Override the base dir with `--state-dir`. |
| `$WICKET_ACCOUNT` (env) | Default account | Used when `--account` is omitted; else the sole directory under the mail root. |
| `$WICKET_MAIL_ROOT` (env) | Where the mail tree lives | Default `~/.delphi/mail`. Set it when the tree lives elsewhere (e.g. an encrypted vault that mounts at another path). |
| `$WICKET_ACCOUNT_ALIASES_FILE` (env) | Override `account-aliases.json`'s location | Any name, any path. Default `<mail-root>/account-aliases.json`. |
| `$WICKET_DOMAIN_ALIASES_FILE` (env) | Override `domain-aliases.json`'s location | Same shape as above. |
| `$WICKET_DOMAIN_ROUTES_FILE` (env) | Override `domain-routes.json`'s location | Same shape as above. |

An app password is account-scoped and revocable at
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
that is the kill switch. Full security model:
[`src/wicket/README.md`](src/wicket/README.md).

**Three owner-authored maps** sit at the mail root. All three are optional, and
none of them is inferred — an entry exists because you wrote it. They answer three
different questions, and keeping them apart is load bearing:

| File | Answers | Example |
|---|---|---|
| `<mail-root>/account-aliases.json` | *Which addresses are **me**?* Maps an address you receive at to the store that owns it. **Exceptions only:** an address that already names a store (`you@work.com`) resolves to itself and must not be listed. A primary may be an account address or a **bucket** name; an alias may be a literal address or a `*@domain` catch-all. | `{"travel": ["hyatt@xcv.org"], "shopping": ["*@shop.you.com"]}` |
| `<mail-root>/domain-routes.json` | *Which counterparties are **not me**, and where does their mail go?* Used only when no mailbox claims the thread. | `{"shopping": ["ikea.com", "llbean.com"]}` |
| `<mail-root>/domain-aliases.json` | *What is this domain **called**?* Folds a marketing subdomain onto its primary when filing, so a store does not fragment into one folder per campaign host. Shared by `fetch` and `ingest`; override with `--alias-file`. | `{"delta.com": ["*.delta.com"]}` |

> A counterparty must **never** go in `account-aliases.json`. That file means "this
> is me", so listing IKEA there makes every message *from* IKEA look outbound, and
> it files under the recipient's domain instead of `ikea.com`. Same file shape,
> opposite meaning — hence two files.

Domains are folded **before** routes are matched, so `domain-routes.json` lists
only primaries (`delta.com`) and never has to repeat every subdomain the fold map
already knows about.

**`wicket config`** edits all three without hand-editing JSON:

```
wicket config account aliases create --primary travel --item hyatt@xcv.org
wicket config account aliases update --primary travel --add delta@xcv.org --remove hyatt@xcv.org
wicket config account aliases list
wicket config account aliases delete --primary travel --item delta@xcv.org   # one item
wicket config account aliases delete --primary travel                       # the whole entry

wicket config domain aliases {list,create,update,delete}   # domain-aliases.json
wicket config domain routes  {list,create,update,delete}   # domain-routes.json
```

Every write validates `--primary`/`--item` with the same rules the read side
enforces (an address vs. a bucket, a domain vs. a `*.parent` wildcard), so a row
this writes is always one `load_account_aliases` etc. can read back. Each of the
three files' location can be overridden independently — any name, any path —
with `$WICKET_ACCOUNT_ALIASES_FILE`, `$WICKET_DOMAIN_ALIASES_FILE`, and
`$WICKET_DOMAIN_ROUTES_FILE`, the same escape hatch `$WICKET_MAIL_ROOT` gives the
mail tree itself.

## Where data lives

Everything wicket writes lives under the **mail root** — `~/.delphi/mail/` by
default, or `$WICKET_MAIL_ROOT` — one directory per store:

| Path | What |
|---|---|
| `<mail-root>/<store>/manifest/YYYY.jsonl` | The year-sharded manifest, one JSON row per message (identity, observation, and settlement fields). Override with `--store-dir`. |
| `<mail-root>/<store>/archive/<domain>/YYYY-MM/<msg-id>.eml` | Downloaded messages, filed by sender/counterparty domain and month. Override with `--dest`. |

A **store** is either an **account** (a real mailbox, named by its address:
`you@gmail.com`) or a **bucket** (a topical destination that is not a mailbox:
`travel`, `shopping`). Only accounts are swept by `catalog` and `fetch`; a bucket
holds only mail no mailbox owns (see [Where a message goes](#where-a-message-goes)).

**wicket never creates a store.** Creating a destination is always an explicit
owner `mkdir` — so a mistyped `--account` is a hard error instead of a new
directory, and an unclaimed thread is left alone instead of minting a store.

**The mail root must exist, and wicket fails closed if it does not.** The tree is
designed to sit inside an encrypted (Cryptomator) vault; a *locked* vault leaves
its mount point as an ordinary empty directory. If wicket read that as "a fresh,
empty store" it would `mkdir` an account and write your mail **in plaintext**
underneath the vault. So every verb refuses, and creates nothing, when the mail
root is absent.

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
an API surface. Every entry point takes `account=`; a single account under the mail
root is the default. `ingest` is the exception: `account=None` means **route**, not
"guess the default".

```python
from pathlib import Path

from wicket.catalog.api import catalog
from wicket.fetch.api import fetch, FetchOptions, held_messages
from wicket.report.api import report, manifest, bucket
from wicket.ingest.api import ingest, IngestOptions
from wicket.env import resolve_archive_dir

# drive the verbs (each returns a stats dict; non-interactive by default)
catalog(account="you@gmail.com")                    # observe into the manifest
fetch(                                              # download matching .eml
    account="you@gmail.com",
    options=FetchOptions(domains=["chase.com", "amazon.com"]),  # or query="..."
)
counts = report(account="you@gmail.com")            # one-screen summary
ingest(                                             # file a local .eml export (no IMAP)
    options=IngestOptions(src=Path("~/Downloads/Outlook").expanduser()),
)                                                   # account=None -> route per thread

# a bucket is a view across every store, not a place (travel, shopping)
for account, rows in bucket("travel").items():
    print(account, len(rows))

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
provider to select. `wicket ingest` is the offline path instead: drag-export the
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

<!-- BEGIN cli-tree (generated) -->
## CLI

<!-- GENERATED — DO NOT EDIT between the markers. Regenerate:  python scripts/gen_cli_docs.py --write -->

```text
python -m wicket   [Pattern 3 (leaf)] OK
```
<!-- END cli-tree -->
