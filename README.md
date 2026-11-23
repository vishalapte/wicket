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
an app password instead of OAuth, no cloud project. Secrets live in a file you choose,
linked from `~/.config/wicket/accounts/<account>/imap.json`; what you keep lands in plain files under the **mail root**
(`$WICKET_DATA_DIR`); and a small library lets
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

# 0. Make the account's folder and link its imap.json to the file that holds (or will
#    hold) the credential. An account with no folder is a silent no-op (exit 0) for
#    fetch and catalog. `python -m wicket.account create` does the same.
mkdir -p ~/.config/gmail/you@gmail.com ~/.config/wicket/accounts/you@gmail.com
ln -s ~/.config/gmail/you@gmail.com/imap.json ~/.config/wicket/accounts/you@gmail.com/imap.json

# 1. Observe the mailbox into a year-sharded manifest (headers only, no bodies).
#    First run prompts for your Gmail address + app password (generate one at
#    myaccount.google.com/apppasswords) and saves it 0600 to
#    ~/.config/wicket/accounts/you@gmail.com/imap.json, through the link.
wicket catalog --mail-account you@gmail.com

# 2. Get full .eml for one or more sender domains into staging/; ingest --staged files them.
#    Omit --domains to get every message, year by year, until Gmail throttles.
wicket fetch --mail-account you@gmail.com --domains chase.com,amazon.com

# 3. Read the manifest, no IMAP: who emails you most, every address ever seen.
wicket report --mail-account you@gmail.com --senders
```

Add `--dry-run` to `catalog`, `fetch`, or `ingest` to preview without writing.
When `~/.config/wicket/accounts` holds one folder with an `imap.json`, `--mail-account` is
optional: that folder is the account. `--mail-account` always works, and `$WICKET_ACCOUNT`
stands in for it; with no such folder, or several, a verb not given one exits 2.

For a mailbox wicket **can't** reach over IMAP (e.g. Microsoft 365 with
basic-auth IMAP disabled), drag-export the messages to `.eml` from your desktop
client and file them into the same manifest + archive offline. Omit `--mail-account`
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
list. The four owner-authored maps below are edited through three **nouns**, each
its own module: `python -m wicket.alias <list|create|update|delete>` and
`python -m wicket.store <list|create|delete>`. A third noun, `python -m wicket.halt
<list|set|clear>`, operates the HALT (below), and a fourth, `python -m wicket.account
<list|create|delete>`, an account's folder in `~/.config/wicket/accounts`.

| Verb | What it does |
|---|---|
| `wicket catalog` | Observe the mailbox (headers only) into the manifest, recording **what exists**. |
| `wicket fetch` | Get full `.eml` for matching messages into the store's `staging/`, **filing nothing**; `wicket ingest --staged` files them. |
| `wicket report` | Read-only summaries over the manifest (every account's counts, one account's summary, top senders, every address, a bucket across every store, every account by year). No IMAP. |
| `wicket ingest` | The only verb that files. `--staged` files what `fetch` got; `--src` files local `.eml` — a folder (a desktop-client drag-export) or a single message — routing each thread to the account it belongs to, for mailboxes wicket can't reach over IMAP. **Additive**; never deletes an archived message. |
| `python -m wicket.alias` | Edit `domain-aliases.json`: which domains are really another (`us.icapenergy.com` is `icapenergy.com`). No IMAP. |
| `python -m wicket.store` | Edit which store mail goes to: a sender domain (`--from`, `domain-routes.json`) or one of your addresses (`--to`, `account-aliases.json`). No IMAP. |
| `python -m wicket.halt` | List, set or clear an account's HALT, each set and clear logged. No IMAP. |
| `python -m wicket.account` | List, create or delete an account: a folder `~/.config/wicket/accounts/<name>/` holding an `imap.json` link. No IMAP, no mail root. |

Each message's state is two booleans on its manifest row, `deleted` and
`downloaded`. The design is recorded in
[`docs/adr/0002-unified-message-manifest.md`](docs/adr/0002-unified-message-manifest.md).

### Where a message goes

**Which store.** `ingest --staged` files a mailbox's own mail into that mailbox. `ingest --src`
routes **per thread**, never per message: a conversation you were
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
trashed unless a store already holds the message. Create a store (`mkdir <mail-root>/travel`) or add a mapping to claim it.

**Where the file goes, and which label.** Inside a store a message has one file,
`<store>/messages/YYYY/MM/<stem>.eml`, written once and never moved (ADR-MAIL,
2026-11-14). The stem is `sha1(message-id + "\n" + date + "\n" + from)[:16]` over the
normalized `Message-ID`, the `Date:` header in UTC and the `From:` address in lower case
(a missing or unreadable header contributes nothing), so every copy of one message in a
store lands on one path and is stored once; a second copy is not filed again, and the
run reports it when its `Subject` differs. `YYYY/MM` is the message's own `Date:`, else
its topmost `Received:`, else the provider's date. There are no folders: a folder view
is a query over the row's `from`, `to`, `cc`, `bcc` and `attachments`.

A message is linked to every domain it touches through its row's `from`, `to`, `cc`
and `bcc`; there is no filing rank and no single `domain`. A filter by domain
(`held_messages(domains=...)`) matches a row when any of those addresses, folded by
`domain-aliases.json`, is in the set. A message whose row is `excluded` is skipped and
counted, never filed.

## CLI reference

Every flag below is what the code accepts today; `wicket <verb> --help` is the
authoritative, live source. Read-only verbs never prompt when `--non-interactive`
is set (for cron/launchd), and fail loudly instead.

**`wicket catalog`** sweeps mailbox headers into `<mail-root>/<account>/manifest/YYYY.jsonl`.
Idempotent, incremental by default.

| Flag | Meaning |
|---|---|
| `--mail-account ACCOUNT` | Account address; scopes the manifest store. Gmail `+tag` aliases normalized. Default `$WICKET_ACCOUNT`, else the only account folder. |
| `--store-dir DIR` | Override the manifest store dir. Default `<mail-root>/<account>/manifest/`. |
| `--state-dir DIR` | The base holding `accounts/<account>/imap.json`. Default `~/.config/wicket/`. |
| `--mailbox NAME` | IMAP mailbox to sweep. Default `"[Gmail]/All Mail"`. |
| `--years Y,Y` | Comma-separated years to replace, e.g. `2025, 2026`; four digits, 1970 or later. Omit for incremental. |
| `--full` | Re-sweep every year from the oldest message through now (rebuild). |
| `--threads N` | Concurrent year workers, one IMAP connection each. Default 4. |
| `--dry-run` | Sweep and report per-year counts; write nothing. |
| `--non-interactive` | Never prompt; fail loudly if `imap.json` is missing or rejected. |
| `--json` | Print the result as JSON instead of text: `account_linked`, `dry_run`, `store_dir`, and the sweep's counts. |

**`wicket fetch`** gets `.eml` for matching messages into
`<mail-root>/<account>/staging/<thread-id>/<msgid>@<INTERNALDATE>.eml` and **files nothing**:
`wicket ingest --staged` files them into `messages/`, and until then they are not in
`report` or `held_messages`. A message already **got** (filed, staged, excluded, or set aside
under `<mail-root>/.duplicates/<account>/`) is never downloaded again. Pass at most one of
`--domains` / `--query` / `--last`; with one, fetch runs one pass over it.

With none, fetch gets every message in All Mail not yet got and not excluded, **until Gmail
throttles**: this year, last year, then every older year from All Mail's first, oldest first. Each
year is one pass: every message in it not yet got is fetched, month by month, so no year is
ever skipped and no done marker is kept. `--max` bounds the whole run. Each run appends counts and years, never subjects,
addresses or paths, to the account's log, `~/.local/state/wicket/log/<account>.log` (see
[Logs](#logs)).

**The HALT.** The first throttle writes `~/.local/state/wicket/halt/<account>` (the state home is
`$XDG_STATE_HOME` when set; present means halted, absent means not): an IMAP
command that is rejected, times out or aborts; a failed month; or a body empty or shorter than its
`RFC822.SIZE`, which is never written. The first cause wins; every worker stops before its next
month, and no IMAP connection to that account opens again, in `fetch` or `catalog`, until the HALT
is cleared. A HALT is an empty, touched file: its name is the account, its timestamp is when it
was set, and it never expires; only `python -m wicket.halt clear` removes it. A hand-made
`touch ~/.local/state/wicket/halt/<account>` halts the account the same way.

Every HALT set and every HALT cleared is a line in the account's log (`event=halt.set
cause=...` or `event=halt.clear`), because `wicket.auth` is the one door: nothing else writes
`halt/`. A rejected app password's cause says so; re-seeding it in a terminal shows where
that provider makes a new one. No cleared HALT is kept as a file.
`python -m wicket.halt` is that door for the owner:

```
python -m wicket.halt list                                   # halted accounts: when (the file's timestamp), age
python -m wicket.halt set --mail-account you@gmail.com
python -m wicket.halt set --all                              # every account in ~/.config/wicket/accounts
python -m wicket.halt clear --mail-account you@gmail.com
python -m wicket.halt clear --all                            # every account in ~/.config/wicket/accounts
python -m wicket.halt clear --all --min 960                  # only a HALT over 960 minutes old
```

`set` on an account already halted keeps the HALT in force and says so. `clear --min N` clears
a HALT only when its age in whole seconds is over N x 60, and reports a younger one as not halted;
N is a whole number, 0 or more; anything else exits 2. Without `--min`, `clear` clears. The noun writes only
`~/.local/state/wicket`. `--mail-account` is normalized as text (lowercased, `+tag` dropped) and
checked against nothing else; `--all` is every folder in `~/.config/wicket/accounts` that holds
an `imap.json`. Every verb takes `--json`.

**One writer per store.** `catalog`, `fetch` and `ingest` hold `<store>/manifest/.lock` while
they write; a store another process holds is refused with its pid. `--dry-run` takes no lock.

| Exit | Meaning (`catalog`, `fetch`, `ingest`) |
|---|---|
| `0` | Done. |
| `1` | Refused, and a retry may pass: the IMAP host could not be reached, or the server returned an error. |
| `2` | Refused: bad input; credentials missing or unreadable; a HALT is in force, or this run wrote one (stderr names its cause, or when it was set, and its file); or another process holds a store's lock (its pid is named). |

Every refusal on stderr ends with a `fix:` line, what to do about it (a HALT's names
`python -m wicket.halt clear --mail-account <account>`). Each refusal has a code; the codes,
their exit codes and their fixes are `src/wicket/error-codes.jsonl`, one JSON object per line
(`reason`, `exit`, `when`, `fix`). A `fetch` that did work before it halted, and a routed
`ingest` that filed the rest and skipped a busy store, print their summary on stderr with the
refusal.

| Flag | Meaning |
|---|---|
| `--domains A,B` | Comma-separated domains; builds the Gmail from/to search. (mutually exclusive with `--query`, `--last` and `--years`; omit all four to fetch everything) |
| `--query EXPR` | Raw Gmail search expression (escape hatch). |
| `--last AGE` | Messages newer than AGE: N then `d`, `m` or `y` (`2d`, `3m`, `1y`), run as the Gmail search `newer_than:AGE`. Anything else exits 2. |
| `--years Y,Y` | Walk only these years, oldest first, each as the no-filter run walks a year (`2021,2022`), for a backfill that does not re-list recent years. Four digits, 1970 or later; anything else exits 2. |
| `--alias-file PATH` | Subdomain-fold JSON `{"primary.com": ["alias.com", "*.primary.com"]}`. Default `<mail-root>/domain-aliases.json` (skipped if absent) — one home, shared with `ingest`. |
| `--max N` | Stop after N new messages this run (after store dedup), across every year of an unfiltered run. |
| `--mail-account ACCOUNT` | Scopes the staging dir and the manifest store. Default `$WICKET_ACCOUNT`, else the only account folder. |
| `--state-dir DIR` | The base holding `accounts/<account>/imap.json`. Default `~/.config/wicket/`. |
| `--threads N` | Concurrent per-thread workers, one IMAP connection each. Default 4. |
| `--dry-run` | Print the plan; download nothing; write nothing. |
| `--non-interactive` | Never prompt; fail loudly if `imap.json` is missing or rejected. |
| `--json` | Print the result as JSON instead of text: the run's counts, `passes` (one row per pass), and the HALT fields. |

**`wicket report`** gives read-only summaries over the manifest (no IMAP, no credentials).
With no flag, one row per account (a folder in `~/.config/wicket/accounts`, with or without
an `imap.json`, so an account whose mail is only ever ingested is listed too): downloaded,
excluded, not downloaded, their total and last download, with a total row. Excluded is a row you
excluded or one on an excluded list; not downloaded is neither downloaded nor deleted, so a message
the server deleted before it was downloaded is in no column. Last download is when the newest
`.eml` in the account's `messages/` or `staging/` was written (its file time), `YYYY-MM-DD HH:MM` in
local time, blank when there is none; the total row shows the latest. A message filed by `ingest`
is written when it is filed, and one from `ingest --src` counts too. With `--mail-account`, that
account's one-screen summary.

| Flag | Meaning |
|---|---|
| `--senders` | Every From address with its message count, descending (`count<TAB>address`). |
| `--addresses` | Every distinct address seen in From or To, sorted. |
| `--bucket NAME` | Every message **any** store holds that this bucket claims (`travel`, `shopping`), grouped by store. A view, not a location; ignores `--mail-account`. |
| `--by-year` | One row per account (a folder in `~/.config/wicket/accounts`, with or without an `imap.json`), one column per year, each cell `local/not local`, with totals. Local is downloaded; not local is not downloaded and not deleted. An excluded row (yours, or on an excluded list) is in neither count. Ignores `--mail-account`. |
| `--mail-account ACCOUNT` | Scopes the manifest store. Default `$WICKET_ACCOUNT`, else the only account folder. |
| `--json` | Print the result as JSON instead of text: `view` (`summary`, `senders`, `addresses`, `bucket`, `by_year`) and that view's data. |

**`wicket ingest`** is the only verb that files: it writes a body to
`messages/YYYY/MM/<stem>.eml` and records `downloaded` and `stem` on its row
(and `cc`, `bcc`, `attachments` on a row it creates). No IMAP, no credentials, no prompt.
**Additive:** a message already archived is left untouched, only new ones are filed,
and no archived message is ever deleted. Re-running is idempotent.

`--staged` files what `fetch` got for one account. It holds the store's lock,
reconciles first, then writes each row before it writes the body into `messages/`, so
a crash in between is repaired by the next run. A staged body whose message is already
archived with the same bytes is removed, and one with other bytes is refused and kept.
One whose stem the store already holds under another row (a local export of the same
message, or a second mailbox copy) is set aside under
`<mail-root>/.duplicates/<account>/<label>/YYYY-MM/<msgid>.eml` and its row is marked
`excluded: "duplicate of <row id>"`, so fetch never gets it again. A file another tool
filed into `messages/` with no row is taken by the message's row, never overwritten.
A file in `staging/` that is not a staged name is reported and left. An empty staging
dir is a no-op. Cron runs `fetch` and then `ingest --staged`.

`--src` files local `.eml` — a flat folder, or a single message — for a mailbox wicket
can't reach over IMAP. Threads are reconstructed from headers, and each thread is routed
to the store it belongs to.

**The source folder is not read-only.** Once a message is durably archived (filed
now, or already in the store), its `.eml` is **moved to `~/.Trash`** — the archive
is the record, the source folder is a volatile inbox. Nothing is ever unlinked:
the Trash is the undo. A message whose thread could not be routed always keeps its
file. `--dry-run` moves nothing.

| Flag | Meaning |
|---|---|
| `--src PATH` | What to ingest: a folder of `.eml` (a flat drag-export; not recursive), or a single `.eml` file. Exactly one of `--src` / `--staged`. A routed run skips, and names, a destination store another process holds (exit 2); its source files stay. |
| `--staged` | File what `wicket fetch` got into `<mail-root>/<account>/staging/` for one account (`--mail-account`, `$WICKET_ACCOUNT`, or the only account folder). No routing, no account check, nothing trashed. |
| `--mail-account ACCOUNT` | Send **every** thread to this one store instead of routing each. A *destination, not a filter*: it does not select which messages are ingested, so naming the wrong one files the whole folder into the wrong store. Refused when the folder is plainly addressed elsewhere (see `--force`). Omit it to route. |
| `--source {local}` | Export profile. Only `local` today (RFC822 `.eml` on disk); reserves the flag for future kinds. |
| `--tag TAG` | A tag recorded in the manifest `labels` for every newly-added message (a flat export carries no provider labels); repeatable (`--tag a --tag b`). Applied only to mail filed this run; archived rows are untouched. |
| `--force` | Ingest anyway when `--mail-account` disagrees with the folder's recipients. |
| `--no-delete` | Leave the source folder untouched (do not move archived `.eml` to `~/.Trash`). |
| `--dry-run` | Parse and report what would be added; write nothing, trash nothing. |
| `--json` | Print the result as JSON instead of text: `mode` (`staged`, `routed`, `single`), `dry_run`, `to_trash`, and that mode's stats. |

The run summary lists each filed `.eml` grouped by its directory (`YYYY/MM/`), so you
can see exactly what landed where, and every second copy whose `Subject` differs.

## Settings

wicket reads its settings from environment variables, the account folders under
`~/.config/wicket/accounts`, and five owner-authored maps at the mail root.
[`defaults.json`](defaults.json) holds wicket's default environment, one variable per
key. `./install` writes it into `~/.claude/settings.json` "env", so a Claude Code session
runs wicket with it:

```bash
./install              # show the settings.json change, ask, then install the wicket command
./install --dry-run    # print what would be written, ask nothing, write nothing
```

`./install` prints one `env.WICKET_X = "..."` line per setting it touches, and never a
setting it leaves alone. A setting it adds (`+`, the key absent today) is written without
asking. A setting it would change (`-` old, `+` new) or remove (`-`) is written only when you
answer y to `Replace <path>? [y/N]`; with no terminal it is not written, and the run says to
run `./install` in one. The additions are written either way. Then it writes
`~/.local/bin/wicket`, the launcher that runs this checkout's CLI from any directory
(`wicket alias create ...`).

A null value is skipped, and a key of that name already in settings.json is kept.
`<KEY>` is replaced with the value of KEY, another key of `defaults.json` (here
`<WICKET_DATA_DIR>`, the mail root), and `~` is expanded, so every path is written absolute
(Claude Code does not expand `~` in "env"). Every key must start `WICKET_`, from
`[project].name`; one that does not refuses the run. Only those keys are set; every other
variable and every hook is kept, and a second run prints "already up to date".
`$CLAUDE_CONFIG_DIR` names another config directory. `./install` was the same file, byte for
byte, as delphi's; the diff and the prompt are wicket's alone until racecar delivers them.

### Environment variables

The variables wicket owns; renaming one is a breaking change to wicket:

| Variable | What | Default | Read at |
|---|---|---|---|
| `WICKET_DATA_DIR` | Where the mail tree lives: the **mail root**. Set it when the tree lives elsewhere (e.g. an encrypted vault that mounts at another path). | `~/.data/mail` | `wicket.env` (`MAIL_ROOT`, bound once at import) |
| `WICKET_ACCOUNT` | The account used when `--mail-account` is omitted. | none; then the only folder in `~/.config/wicket/accounts` holding an `imap.json` | `wicket.env.resolve_account` |
| `WICKET_ACCOUNT_ALIASES` | Where `account-aliases.json` is: any name, any path. | `<mail-root>/account-aliases.json` | `wicket.env.account_aliases_path` |
| `WICKET_DOMAIN_ROUTES` | Where `domain-routes.json` is. | `<mail-root>/domain-routes.json` | `wicket.domains.domain_routes_path` |
| `WICKET_DOMAIN_ALIASES` | Where `domain-aliases.json` is. | `<mail-root>/domain-aliases.json` | `wicket.domains.domain_aliases_path` |
| `WICKET_EXCLUDED_LISTS` | Where `excluded-lists.json` is. | `<mail-root>/excluded-lists.json` | `wicket.exclusions.excluded_lists_path` |
| `WICKET_EXCLUDED_SENDERS` | Reserved for sender exclusion, which is not built. | `<mail-root>/excluded_senders.json` | nothing yet (named in `wicket.env`) |

### Dependencies

Repos: [claude](https://github.com/vishalapte/claude).

**[claude](https://github.com/vishalapte/claude)**, not pinned. It ships
`~/.claude/settings.json`, and wicket's `./install` writes wicket's variables into that file's
"env", so a Claude Code session runs wicket with them. The dependency is implicit, not
breaking: wicket runs with none of them set, each falling back to its default above. claude's
own `install` replaces `settings.json` whole, so run wicket's `./install` after it. wicket
reads these from that file or your shell and never sets them; claude's `settings.json` sets
neither today:

| Variable | What | Default | Read at |
|---|---|---|---|
| `XDG_STATE_HOME` | The state home; wicket's halt and log dirs (`wicket.log` and the account logs) are under `$XDG_STATE_HOME/wicket`. | `~/.local/state` | `wicket.env._state_base` |
| `CLAUDE_CONFIG_DIR` | Claude Code's config directory; `./install` writes its `settings.json`. The same name Claude Code, racecar's and delphi's `./install` read. | `~/.claude` | `install` (`main`) |

### Credentials and accounts

**Secrets** live outside the data tree and are never committed. Each account is a folder,
`~/.config/wicket/accounts/<account>/`, holding `imap.json`, a link to the provider's file
(`ln -s ~/.config/{gmail,fastmail}/<account>/imap.json
~/.config/wicket/accounts/<account>/imap.json`). Make it by hand or with `python -m
wicket.account create <name> --config <folder>`. Every folder there holding an `imap.json`
is an account (`account list`, `halt --all`); a folder without one is none. An account
with no folder is a silent no-op (exit 0) for `fetch` and `catalog`; a folder with no
`imap.json` prompts for the credential, or exits 2 under `--non-interactive`:

```
python -m wicket.account list                                  # each name and its imap.json target
python -m wicket.account create you@gmail.com --config ~/.config/gmail/you@gmail.com
python -m wicket.account delete you@gmail.com                  # imap.json only; the folder stays
```

`create` and `delete` print nothing; the exit code says what happened. `create` makes the
folder and links its `imap.json` to `<folder>/imap.json`, which need not exist yet. The same
link again exits 0; a `--config` folder that does not exist (`--config path does not exist`)
or an `imap.json` naming another target (`link exists with different target (<current
target>)`) exits 2 with the message on stderr, as an error packet under `--json`. `delete`
removes an `imap.json` link, never what it points to; a real `imap.json` file is renamed
`imap_YYYYMMDD_HHMM.json` (the local time of the delete) in the same folder, and exits 2,
moving nothing, when that name is already there. Every verb takes `--json`; `list --json` is
`{"accounts": [[name, target], ...]}`, the target being the link's, or a real file's own
path.

| Path | What | Notes |
|---|---|---|
| `~/.config/wicket/accounts/<account>/imap.json` | Gmail address + app password, through the link | mode `0600`; seeded on first run; never on the command line. Override the base dir with `--state-dir` (the account is then `<dir>/accounts/<account>/`). |

An app password is account-scoped and revocable at
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
that is the kill switch. Full security model:
[`src/wicket/README.md`](src/wicket/README.md).

### The owner maps

**Five owner-authored maps** sit at the mail root. All five are optional, and
none of them is inferred — an entry exists because you wrote it. They answer five
different questions, and keeping them apart is load bearing:

| File | Answers | Example |
|---|---|---|
| `<mail-root>/account-aliases.json` | *Which addresses are **me**?* Maps an address you receive at to the store that owns it. **Exceptions only:** an address that already names a store (`you@work.com`) resolves to itself and must not be listed. A primary may be an account address or a **bucket** name; an alias may be a literal address or a `*@domain` catch-all. | `{"travel": ["hyatt@xcv.org"], "shopping": ["*@shop.you.com"]}` |
| `<mail-root>/domain-routes.json` | *Which counterparties are **not me**, and where does their mail go?* Used only when no mailbox claims the thread. | `{"shopping": ["ikea.com", "llbean.com"]}` |
| `<mail-root>/domain-aliases.json` | *What is this domain **called**?* Folds a marketing subdomain onto its primary when filing, so a store does not fragment into one folder per campaign host. Shared by `fetch` and `ingest`; override with `--alias-file`. | `{"delta.com": ["*.delta.com"]}` |
| `<mail-root>/excluded-lists.json` | *Which **mailing lists** do I never keep?* A list-id (the text in a message's `List-Id` header) under `"*"` is excluded for every account, under an account for that account alone. `fetch` leaves it out of every search; `ingest --staged` and `--src` skip its mail and leave the file where it is; `catalog` still records the whole mailbox, and writes each message's list-id on its row as `list_id` (no key when it has no `List-Id`); `report` leaves out a row whose `list_id` is on the map as it is now, and counts it as excluded. A key that is neither `"*"` nor an account folder holding `imap.json` refuses the run. | `{"*": ["made-of-meat.googlegroups.com"]}` |

> A counterparty must **never** go in `account-aliases.json`. That file means "this
> is me", so listing IKEA there makes every message *from* IKEA look outbound, and
> it files under the recipient's domain instead of `ikea.com`. Same file shape,
> opposite meaning — hence two files.

Domains are folded **before** routes are matched, so `domain-routes.json` lists
only primaries (`delta.com`) and never has to repeat every subdomain the fold map
already knows about.

Three nouns edit the four maps without
hand-editing JSON. **`wicket.alias`** edits the
fold map; **`wicket.store`** edits both maps that pick a store, one flag per file;
**`wicket.exclude`** edits the excluded lists (no `--mail-account` is every account,
`*`):

```
python -m wicket.exclude create --list made-of-meat.googlegroups.com
python -m wicket.exclude create --list news.example.org --mail-account vishal@enine.com
python -m wicket.exclude list
python -m wicket.exclude delete --list made-of-meat.googlegroups.com
```


```
python -m wicket.alias create --primary delta.com --item '*.delta.com'
python -m wicket.alias update --primary delta.com --add delta.net --remove t.delta.com
python -m wicket.alias list
python -m wicket.alias delete --primary delta.com --item delta.net   # one domain
python -m wicket.alias delete --primary delta.com                    # the whole entry

python -m wicket.store create --store travel --from delta.com --to delta@xcv.org
python -m wicket.store delete --store travel --to delta@xcv.org
python -m wicket.store list
```

`--from` (a sender domain) writes `domain-routes.json`, `--to` (one of your
addresses) writes `account-aliases.json`, and both repeat. `store create` and
`store delete` refuse when given neither, and a domain or address already sent to
another store refuses the whole command, so nothing can go to two places. An exact
entry beats a wildcard: `*.delta.com` in one store and `t.delta.com` in another
are both valid.

Every write makes its edits in memory and checks the result with the read
side's own rule (`parse_account_aliases`, `parse_domain_aliases`,
`parse_domain_routes`) before any file is replaced, so a file these write is
always one `load_account_aliases` etc. can read back. Each file is replaced
whole; when a command names two files and the second write fails, the first is
put back. Each file's location can be overridden, any name, any path: see
[Environment variables](#environment-variables).

## Where data lives

All mail wicket writes lives under the **mail root**, `$WICKET_DATA_DIR`, one
directory per store. The only other things it
writes are the **halt dir** and the **log dir**, which hold no mail:

| Path | What |
|---|---|
| `<mail-root>/<store>/manifest/YYYY.jsonl` | The year-sharded manifest, one JSON row per message (identity, observation, and settlement fields), oldest `date` (UTC) first and the newest last; a tie goes in `id` order. Override with `--store-dir`. |
| `<mail-root>/<store>/manifest/.lock` | The store's lock, held by the one process writing it; it names that process's pid. |
| `<mail-root>/<store>/messages/YYYY/MM/<stem>.eml` | Filed messages, one file per message, named by its stem and filed under its own month; never moved. Written only by `ingest`. |
| `<mail-root>/<store>/staging/<thread-id>/<msgid>@<INTERNALDATE>.eml` | What `fetch` got and `ingest --staged` has not yet filed. |
| `<mail-root>/.duplicates/<store>/<label>/YYYY-MM/<msgid>.eml` | Copies of mail the store already holds. Never unlinked; `fetch` counts them as got. |
| `~/.local/state/wicket/halt/<account>` | The account's HALT (`$XDG_STATE_HOME/wicket/halt/`): present means halted. Written and removed only by `wicket.auth`. |
| `~/.local/state/wicket/log/<account>.log` | The account's log: fetch's counts and years, what each ingest filed, and every HALT set and cleared. Never a subject, another address, a path or a secret. |
| `~/.local/state/wicket/log/wicket.log` | Every event of every run, from every command, whether a terminal or cron started it ([Logs](#logs)). Never a subject, a message body or a secret. |

Both dirs are created owner-only (`0700`) by their first write, and each log file `0600`;
neither is a store.

### Logs

Every log line has one shape, the syslog file header and then logfmt `key=value` fields:

```
2026-11-15T08:00:03-05:00 vulcan wicket[41205]: account=you@gmail.com level=info event=fetch.search matched=40 got=23 candidates=17
```

The header is the time to the second with its offset, the host, and `wicket[pid]`. Then
`account=` (left out where none applies), `level=`, and `event=`, which is `<verb>.<step>`:
the verb that ran and the place in the run it reached (`fetch.search`, `ingest.end`,
`halt.set`). A field with no value is left out; a value with a space, `=`, `"` or a control
character is quoted. A refusal is `event=wicket.end outcome=refused exit=<n> reason=<code>`;
a crash is `outcome=crashed` with the error and where in wicket it happened.

`wicket.log` holds every event of every run. `<account>.log` beside it holds the account's
durable subset: fetch's start, years and end, `ingest.end`, and each HALT set and cleared.
A `--dry-run` writes no account log; its events reach `wicket.log` alone, each with
`dry_run=true`. The
same line is in both. The terminal keeps its text: progress, summaries, warnings and refusals
print as they always did, and no logfmt line reaches stderr. The events and their fields are
listed once, in `wicket.env` beside the formatter. wicket never rotates `wicket.log`.

A **store** is either an **account** (a real mailbox, named by its address:
`you@gmail.com`) or a **bucket** (a topical destination that is not a mailbox:
`travel`, `shopping`). Only accounts are swept by `catalog` and `fetch`; a bucket
holds only mail no mailbox owns (see [Where a message goes](#where-a-message-goes)).

**wicket never creates a store.** Creating a destination is always an explicit
owner `mkdir` — so a mistyped `--mail-account` is a hard error instead of a new
directory, and an unclaimed thread is left alone instead of minting a store.

**The mail root must exist, and wicket fails closed if it does not.** The tree is
designed to sit inside an encrypted (Cryptomator) vault; a *locked* vault leaves
its mount point as an ordinary empty directory. If wicket read that as "a fresh,
empty store" it would `mkdir` an account and write your mail **in plaintext**
underneath the vault. So every verb refuses, and creates nothing, when the mail
root is absent.

The manifest is a rebuildable cache: `downloaded` is ground-truthed against the
`.eml` on disk, so the whole
store can be reconstructed from mailbox + disk by re-running the verbs (ADR
[`0002`](docs/adr/0002-unified-message-manifest.md)).

## Using wicket as a library

wicket is meant to be depended on, not copied. The four verbs are the root noun's:
each verb's library (`wicket.root.lib.<verb>`) does the work over IMAP and the
manifest, `wicket.root.api` owns orchestration (resolve the account, its paths, and
credentials, then dispatch), and each face is a thin wrapper on `api` (the CLI
today, an MCP server later). Import a verb from `wicket.root.api`; the package root
is a namespace, not an API surface. Every entry point takes `account=`; the only account folder in
`~/.config/wicket/accounts` is the default. `ingest` is the exception: `account=None` means **route**, not
"guess the default".

```python
from pathlib import Path

from wicket.root.api import catalog
from wicket.root.api import fetch, FetchOptions, held_messages
from wicket.root.api import report, manifest, bucket_rows
from wicket.root.api import ingest, IngestOptions
from wicket.env import resolve_messages_dir

# drive the verbs (each returns a stats dict; non-interactive by default)
catalog(account="you@gmail.com")                    # observe into the manifest
fetch(                                              # get matching .eml into staging/
    account="you@gmail.com",
    options=FetchOptions(domains=["chase.com", "amazon.com"]),  # or query="...",
)                                                   # or neither: every year, until throttled
counts = report(account="you@gmail.com")            # one-screen summary
ingest("you@gmail.com", options=IngestOptions(staged=True))  # file what fetch got
ingest(                                             # file a local .eml export (no IMAP)
    options=IngestOptions(src=Path("~/Downloads/Outlook").expanduser()),
)                                                   # account=None -> route per thread

# a bucket is a view across every store, not a place (travel, shopping)
for account, rows in bucket_rows("travel").items():
    print(account, len(rows))

# read the held mail and manifest (no IMAP, no credentials)
for row, eml in held_messages(domains={"delta.com", "united.com"}):
    itinerary = parse_flight(eml.read_bytes())      # the consumer's own parser

rows = manifest()                  # the whole year-sharded store, {id: row}
messages = resolve_messages_dir("you@gmail.com")  # <store>/messages/YYYY/MM/<stem>.eml
```

`FetchOptions` takes no `dest`: fetch files nothing, and a destination is refused
with a message saying so. Staged mail reaches `held_messages` once
`ingest(..., options=IngestOptions(staged=True))` has filed it.

To depend on wicket from another project, pin a git tag:

```toml
dependencies = ["wicket @ git+https://github.com/vishalapte/wicket.git@main"]
```

## Providers

The IMAP verbs (`catalog`, `fetch`) speak **Gmail** and **Fastmail**. An
account's provider is where its `imap.json` links: into `~/.config/gmail/<login>/`
is Gmail, into `~/.config/fastmail/<login>/` is Fastmail, and anything else (a real
file, a link elsewhere) is refused, exit 2. The account folder's name is the store;
the login is the `email` in `imap.json`, so a Fastmail login can file into a store of
another name:

```
~/.config/wicket/accounts/vishal@enine.com/imap.json -> ~/.config/fastmail/vishalapte@fastmail.com/imap.json
```

| | Gmail | Fastmail |
|---|---|---|
| Host | `imap.gmail.com` | `imap.fastmail.com` (must announce `OBJECTID`, else refused, no HALT) |
| What a whole-mailbox pass walks | `[Gmail]/All Mail` | every folder but Trash, Spam, Scheduled, Memos, Notes and Templates; one message per `EMAILID`, its labels the folders holding it |
| `msgid` / `thread_id` | `X-GM-MSGID` (hex) / `X-GM-THRID` | `EMAILID` / `THREADID` (RFC 8474) |
| Archived file name | `<msgid>.eml` | a lowercase 16-hex digest of the `EMAILID` (it is mixed case) |
| Staged name | `<thread>/<msgid>@<date>.eml` | `fastmail/<thread hex>/<emailid hex>@<date>.eml` |
| `--last N{d,m,y}` | `newer_than:N…` | `SINCE <date>` |
| `--domains`, `--query`, `catalog --mailbox` | Gmail search | refused, exit 2 |

The two providers do not share a search dialect; the portable query grammar that
would reconcile them is specified in
[`docs/adr/0001-portable-query-grammar.md`](docs/adr/0001-portable-query-grammar.md),
with the living key-by-key divergence table in
[`docs/QUERY-FIDELITY.md`](docs/QUERY-FIDELITY.md). Until it is built, Fastmail
takes no `--domains` or `--query`.

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
[`AGENTS.md`](AGENTS.md) (repo-level) and
[`src/wicket/CLAUDE.md`](src/wicket/CLAUDE.md) (package-level). Full usage and the
security model are in the package README:
[`src/wicket/README.md`](src/wicket/README.md).

## License

MIT. See the [`LICENSE`](LICENSE) file.

<!-- BEGIN cli-tree (generated) -->
## CLI

<!-- GENERATED — DO NOT EDIT between the markers. Regenerate:  python -m racecar.surface generate --surface cli --docs --apply -->

```text
python -m wicket   [Pattern 2 (discovery + own CLI)] OK
  python -m wicket.alias   [Pattern 3 (leaf)] OK
    python -m wicket.alias create   [subcommand] OK
    python -m wicket.alias delete   [subcommand] OK
    python -m wicket.alias list   [subcommand] OK
    python -m wicket.alias update   [subcommand] OK
  python -m wicket.store   [Pattern 3 (leaf)] OK
    python -m wicket.store create   [subcommand] OK
    python -m wicket.store delete   [subcommand] OK
    python -m wicket.store list   [subcommand] OK
  python -m wicket.halt   [Pattern 3 (leaf)] OK
    python -m wicket.halt list   [subcommand] OK
    python -m wicket.halt set   [subcommand] OK
    python -m wicket.halt clear   [subcommand] OK
  python -m wicket.account   [Pattern 3 (leaf)] OK
    python -m wicket.account list   [subcommand] OK
    python -m wicket.account create   [subcommand] OK
    python -m wicket.account delete   [subcommand] OK
  python -m wicket.exclude   [Pattern 3 (leaf)] OK
    python -m wicket.exclude create   [subcommand] OK
    python -m wicket.exclude delete   [subcommand] OK
    python -m wicket.exclude list   [subcommand] OK
  python -m wicket catalog   [subcommand] OK
  python -m wicket fetch   [subcommand] OK
  python -m wicket report   [subcommand] OK
  python -m wicket ingest   [subcommand] OK
```
<!-- END cli-tree -->
