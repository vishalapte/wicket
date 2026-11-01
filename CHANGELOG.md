---
summary: The release record: every notable change per version, Keep a Changelog / SemVer.
pnode: [README.md]
bearing: record
---

# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## 0.13.0 - 2026-11-01

### Added
- **`wicket report` with no `--mail-account` counts every account.** One row per account (a folder in `~/.config/wicket/accounts`), columns downloaded, excluded, not downloaded and total, then a total row; before, it refused unless exactly one account existed. `$WICKET_ACCOUNT` does not select a summary here: only `--mail-account` does. Each row is sorted by one rule that `--by-year` now reads too: excluded first (marked, or its list on `excluded-lists.json`), then downloaded (an only copy included), then not downloaded unless deleted; a message the server deleted before it was downloaded is in no column. An absent mail root still refuses. `--senders` and `--addresses` still need an account.

## 0.12.0 - 2026-10-30

### Added
- **`wicket fetch --years 2021,2022`: backfill named years only.** The no-filter run walks this year, last year, then every older year, so a backfill of old years first re-lists the recent ones (on Fastmail, one search per folder per year). `--years` walks only the years it names, sorted and each once, as the no-filter run walks a year, and makes no first-year lookup. It is one of the four filters, so it refuses `--domains`, `--query` and `--last`, exit 2; a value that is not a year is refused as `catalog --years` refuses it.

## 0.11.1 - 2026-10-30

### Fixed
- **`catalog --years` refuses a value that is not a year.** Each must be four digits, 1970 or later; anything else (`202`, `x`) exits 2 before any IMAP, where it once reached the server as a bad date, was rejected, and HALTed the account. The years are also sorted and taken once, as the sweep already did.

## 0.11.0 - 2026-10-30

### Added
- **`wicket report --by-year`: every account by year.** One row per account (a folder in `~/.config/wicket/accounts`), one column per year (the shard's), each cell `local/not local`, with a total row and column. Local is a downloaded row; not local is a row neither downloaded nor deleted, so a message the server deleted before it was downloaded counts in neither. A row the owner excluded, or whose `list_id` is on `excluded-lists.json`, counts in neither. A staged body counts as not local until `ingest --staged` files it. Read from the manifests only, no IMAP.

## 0.10.1 - 2026-10-30

### Fixed
- **`report`'s "observed only" no longer counts a message the server deleted before it was downloaded.** That is an update to the catalog, not mail still to get; `messages` still counts it.

## 0.10.0 - 2026-10-30

### Added
- **The manifest records each message's mailing list, and `report` leaves excluded lists out.** `catalog` fetches `List-Id` with the headers it already reads and writes it on the row as `list_id` (lowercased, the bracketed id); `ingest` does the same on a row it creates. A message with no `List-Id` gets no key, never null. The row holds the fact, not the decision: `report` counts a row as excluded when the owner marked it `excluded` or its `list_id` is on `excluded-lists.json` as it is now, so `wicket.exclude delete` counts a list's rows again at once, with no catalog. `fetch` and `ingest` still decide from the map, unchanged. Rows catalog has not seen since this change have no `list_id` until it sweeps their year again (`catalog --full` once). The header marker is now matched anywhere in the reply line, since the longer field list no longer fits the 64 bytes it was looked for in.

## 0.9.0 - 2026-10-29

### Added
- **Mailing lists you never keep: `excluded-lists.json` and `wicket.exclude`.** A fourth owner map at the mail root, `{"*": [list-id], "<account>": [list-id]}`: a list-id (the text in a message's `List-Id` header, `made-of-meat.googlegroups.com`) under `"*"` is excluded for every account, under an account for that account alone. `fetch` appends the provider's `NOT` key to every search (Gmail `NOT X-GM-RAW "list:L"`, Fastmail `NOT HEADER List-Id "<L>"`, the brackets so part of a name never matches another list), so an excluded list is never listed or downloaded; `ingest --staged` and `--src` skip its mail, count it as `excluded_lists` and leave the file where it is; `catalog` still records the whole mailbox. A key that is neither `"*"` nor an account folder holding `imap.json` refuses the run. `python -m wicket.exclude create --list L [--mail-account A]`, `delete`, `list` edit it; no `--mail-account` is every account.

## 0.8.0 - 2026-10-29

### Added
- **Breaking: `catalog` and `fetch` read Fastmail.** An account's provider is where its `imap.json` links: `~/.config/gmail/<login>/` is Gmail, `~/.config/fastmail/<login>/` is Fastmail (`imap.fastmail.com`, which must announce `OBJECTID`), and anything else, a real file included, is refused before any login. The account folder's name is the store, so a Fastmail login files into a store of another name (`accounts/vishal@enine.com/imap.json` links to `fastmail/vishalapte@fastmail.com/`). A Fastmail pass walks every folder but Trash, Spam, Scheduled, Memos, Notes and Templates; a message in two folders is one row, its `labels` both folders. `msgid` and `thread_id` are its `EMAILID` and `THREADID`; its archived file is named by a lowercase digest of the mixed-case `EMAILID`, and its staged body sits under `staging/fastmail/`. `--last` becomes `SINCE <date>`; `--domains`, `--query` and `catalog --mailbox` are refused, exit 2, until ADR 0001's compiler exists. `manifest.staged_name` and `parse_staged` move to `wicket.provider`, and `report` says "gone from mailbox".

### Changed
- **ingest's header threading lives in `wicket.message`** (`assign_threads`, `referenced_ids`, `conversation_key`), beside the message key; behaviour unchanged.
- **Breaking: Gmail's IMAP dialect has one home, `wicket.provider`.** Its host, All Mail folder, `X-GM-*` items, `X-GM-RAW` search, id order and file name are `wicket.provider.GMAIL`'s; `auth.open_mailbox` takes a `provider`, and catalog, fetch and ingest --staged key every mailbox message with `message.mailbox_keys`. Every Gmail command, row, key and file name is byte-identical. `wicket.env.IMAP_HOST`, `IMAP_PORT`, `ALL_MAIL_MAILBOX`, `wicket.auth.APP_PASSWORDS_URL`, `wicket.message.gmail_key` and `GmailRow` are gone.

### Fixed
- **`fetch` reconciles as the account, as `ingest --staged` does, not as the login email.** No change for an account whose folder is named for its login; `ThreadContext.imap_email` is now `ThreadContext.account`.

## 0.7.0 - 2026-10-29

### Added
- **`--json` on `catalog`, `fetch`, `report` and `ingest` prints the result as JSON**, and each verb's lexicon node declares a `--json` command line. `catalog`: `account_linked`, `dry_run`, `store_dir`, and the sweep's `messages`, `shards`, `removed`, `boundary_drops`, `parse_skips` and `label_misses`. `fetch`: `account_linked`, `dry_run`, `filtered`, `account` (as named, or null), the run's `matched`, `got`, `candidates`, `pending`, `linked`, `downloaded` and `failed`, `passes` (`year`, `pass`, the same counts, and `got_total` for a run over every year), and `halted`, `halt`, `halt_file`; a run that halts is refused, so a printed result never has `halted` true. An account with no folder gives `{"account_linked": false, "dry_run": ...}`, exit 0. `report`: `{"view": "summary", "store_dir", "counts"}`, `{"view": "senders", "senders": [{"address", "count"}]}`, `{"view": "addresses", "addresses"}` or `{"view": "bucket", "bucket": {"name", "stores": [{"account", "messages", "held", "domains": [{"domain", "count"}]}], "total"}}`. `ingest`: `mode` (`staged`, `routed` or `single`), `dry_run`, `to_trash`, and that mode's stats as the worker returns them, with `added_by_year` keyed by the year as a string and `top_domains` as `[{"domain", "count"}]`; a routed run lists each store's share in `accounts`. The text views are unchanged.
- **The output of `catalog`, `fetch`, `ingest` and `report` is pinned.** Each render and refusal branch is run through the console script's entry with the worker under the api replaced, and its stdout, stderr and exit code are compared whole, so a move of the code under them shows any byte it changes.

### Changed
- **Breaking: the four verbs' code moves into the root noun, `wicket.root`.** `catalog`, `CatalogOptions`, `fetch`, `FetchOptions`, `held_messages`, `DEST_REFUSED`, `ingest`, `IngestOptions`, `report` and `manifest` are imported from `wicket.root.api`; `report`'s `senders`, `addresses` and `bucket` move there renamed `sender_counts`, `all_addresses` and `bucket_rows`, their behaviour unchanged. The workers are `wicket.root.lib.catalog`, `.fetch`, `.ingest` and `.report`. meridian's `from wicket.fetch.api import held_messages` becomes `from wicket.root.api import held_messages`. `AuthError` and `Halted` are no longer re-exported: import them from `wicket.auth`. `wicket.catalog`, `wicket.fetch`, `wicket.ingest` and `wicket.report` are gone, and no re-export is left behind. `catalog`'s stats gain `store_dir`, the manifest store it swept into.
- **Breaking: a HALT and a store another process holds exit 2**, where `catalog` and `fetch` exited 3 and 75 and `ingest` 75. A `fetch` that did work and then halted, and a routed `ingest` that skipped a busy store, print their summary on stderr with the refusal; the lines are unchanged. The CLI reference's exit table reads `0` done and `2` refused.
- **`fetch` and `catalog` print their progress lines on stderr.** The `[plan]`, `[catalog]`, `[dry-run]`, `[discover]`, `[search]` and `[fetch]` lines leave stdout, which now holds only the run's summary; the bytes are the same. The workers log them through `logging`, and the CLI shows them on stderr. A library caller that configures no logging sees no progress.
- **`python -m wicket` answers a bad invocation the way every noun does.** An unknown verb is named (`unknown verb 'fetc'; did you mean 'fetch'?`), a usage error prints the help before its message, and `ingest` with neither source says `needs --src or --staged`. `-h` lists the nouns under `contained nodes:`. `wicket.__main__.main` takes an optional argv list; `wicket.__main__._VERBS` is gone.

## 0.6.0 - 2026-10-29

### Added
- **One manifest row per Gmail message.** A message that reuses another's Message-ID used to share its row, so one of the two files had no row and fetch could download it again. Rows are now keyed per Gmail message, duplicate copies are recorded as `excluded` rather than kept, and a Gmail copy of a message ingest already filed is linked to it. Ingest no longer treats a row without a file as archived, `fetch`'s held count is right under `--max`, Message-IDs with `urn:` or commas are read in full, and every archived `.eml` is written read-only.
- **One writer per store.** `catalog`, `fetch` and `ingest` hold `<store>/manifest/.lock` while they write a store; a store another process holds exits 75 and names its pid. A routed `ingest --src` skips, and names, a busy store and files the rest. `--dry-run` takes no lock.
- **`python -m wicket.account list|create|delete` keeps the account folders.** `create <name> --config <folder>` makes `accounts/<name>/` and links its `imap.json` to `<folder>/imap.json`, which need not exist yet; the same link again exits 0, and an `imap.json` naming another target (`link exists with different target (<target>)`) or a `--config` folder that does not exist exits 2. `delete <name>` removes an `imap.json` link, never what it points to, and keeps the folder; a real `imap.json` file is renamed `imap_YYYYMMDD_HHMM.json` (the local time of the delete), and a delete that finds that name taken exits 2 and moves nothing; no `imap.json` exits 0. `list` gives each folder holding an `imap.json`, a link counting before its target exists, and what it names (`--json`: `{"accounts": [[name, target], ...]}`). `create` and `delete` print nothing; a refusal's message is on stderr, as an error packet under `--json`. No IMAP, no mail root.
- **A HALT stops every IMAP connection to an account.** The first throttle (a command that is rejected, times out or aborts, a failed month, or a body shorter than its `RFC822.SIZE`, which is never written) touches the empty file `~/.local/state/wicket/halt/<account>` (under `$XDG_STATE_HOME`): its name is the account and its timestamp is when it was set. The first HALT wins; every worker stops before its next month (fetch) or year (catalog), and no connection opens past it. A HALT never expires: `python -m wicket.halt clear` removes it, and a hand-made `touch` halts the same way. Its cause is a line in `~/.local/state/wicket/log/<account>.log`, never a path or a password.
- **`python -m wicket.halt list|set|clear` operates an account's HALT, and logs every set and clear.** `set --mail-account X | --all` halts one account, or every account (each folder in `~/.config/wicket/accounts` holding an `imap.json`); `clear --mail-account X | --all` clears one, or every account; `list` shows each halted account, when it was halted (the file's timestamp) and its age (`--json`: `account`, `halted`, `age_seconds`). `--mail-account` and `--all` are exclusive and one is required; `--mail-account` is normalized as text only. The noun writes only `~/.local/state/wicket` and reads only that and, for `--all`, the account folders: no mail root, alias file or credential. Setting an account already halted keeps the HALT and says so. `clear --min N` clears a HALT only when its age in whole seconds is over N x 60, and reports a younger one as not halted; N is a whole number, 0 or more, and anything else exits 2. The new `auth.clear_halt` logs `HALT cleared` and then removes the file, and `auth.list_halts` reads every HALT; with `auth.write_halt` they are the only code that touches the halt dir. Text by default, `--json` on every verb. No verb opens an IMAP connection.
- **`fetch` with no `--domains`, `--query` or `--last` gets everything, until Gmail throttles.** It passes this year, last year, then every older year from All Mail's first, oldest first, each year in one pass: every message in it not yet got, month by month, so no year is skipped and no done marker is kept. `--max` now bounds the whole run. The run appends counts and years (never subjects, addresses or paths) to `~/.local/state/wicket/log/<account>.log`, and `fetch.api.fetch` returns a `passes` list with its stats. A filtered `fetch` runs one pass, as before.
- **`ingest --staged` files what `fetch` got.** It holds the store's lock, reconciles first, keys each message as catalog did, writes the row and then moves the body from `<store>/staging/` into `archive/`, so a crash in between is repaired by the next run. A body the store already archives under another name is set aside under `<mail root>/.duplicates/<store>/`, the one home for duplicates; a staged copy that would land on a different file is refused and kept; a file in `staging/` that is not a staged name is reported and left. `--domain D` files every staged message without an archived thread under `D`.
- **Notes to self file under `self/`, and mail you sent to several domains under `sent/`, in every account.** Filing now has one home, `wicket.filing`, and one rank, read from the thread's opener (its earliest row in the store): an archived member's folder, `--domain`, self, sent, the counterparty domain, the fallback, `_unfiled`. Each filed row records the rule that placed it as `filed` (`rule`, `forced`, `self`, `sent`, `fallback`, `unfiled`); a reply joining an archived thread copies its `filed`, and a catalog merge keeps it.
- **ingest skips, and counts, a message whose row is `excluded`.** It used to archive it.
- **`wicket fetch --last AGE`** gets mail newer than AGE, N then `d`, `m` or `y` (`2d`, `3m`, `1y`): it runs the Gmail search `newer_than:AGE` as the one filtered pass `--query` runs. It is mutually exclusive with `--domains` and `--query`; any other AGE (`2h`, `2w`, `0d`, `2x`) exits 2.

### Changed
- **Breaking: `wicket config` is replaced by two nouns, `python -m wicket.alias` and `python -m wicket.store`.** `wicket.alias` edits the fold map (`domain-aliases.json`); `wicket.store` edits both maps that pick a store, `--from` a sender domain (`domain-routes.json`) and `--to` one of your addresses (`account-aliases.json`). Every edit is checked with the readers' own rules before a file is written, so a domain or address can no longer be sent to two stores, a file the writers save always loads, and a command that names two files writes both or neither. `python -m wicket` lists the nouns beside its four verbs.
- **Breaking: an account is a folder, `~/.config/wicket/accounts/<account>/`, holding `imap.json`, a link to the provider's file** (`ln -s ~/.config/gmail/<account>/imap.json ~/.config/wicket/accounts/<account>/imap.json`). `fetch` and `catalog` read the credential through it, where they read `~/.config/gmail/<account>/imap.json`, and never create the folder: an account with no folder prints nothing and exits 0. A folder without `imap.json` prompts for the credential, or exits 2 under `--non-interactive`. `--state-dir DIR` names the base, so the account is `DIR/accounts/<account>/`. `env.resolve_state_dir` raises `env.NotLinked` where it created the folder.
- **Breaking: with neither `--mail-account` nor `$WICKET_ACCOUNT`, the account is the only folder in `~/.config/wicket/accounts` holding an `imap.json`**, where it was the only store under the mail root. `--mail-account` always works, and `$WICKET_ACCOUNT` still stands in for it. No such folder, or several, exits 2, saying how many there are and to pass `--mail-account`. Every verb that takes `--mail-account` resolves it this way, `report` included. `env.discover_account` is gone.
- **Breaking: a HALT exits 3.** `fetch` and `catalog` exit 3, printing the cause (or, past a HALT already there, when it was set), the HALT file and `python -m wicket.halt clear --mail-account <account>`, where a rejected app password exited 2. `fetch.api.fetch` returns `halted`, `halt` and `halt_file` with its stats; `catalog.api` and `fetch.api` re-export `Halted`. A rejected IMAP command in `catalog` now HALTs where it was counted as unparseable.
- **Breaking: `fetch` files nothing.** It gets bodies into `<mail-root>/<account>/staging/<thread-id>/<msgid>@<INTERNALDATE>.eml`, writes no `downloaded` or `path`, and no longer drops a thread it cannot give a domain; run `wicket ingest --staged` to file them. Until then staged mail is not in `report` or `held_messages`. A message staged, filed, excluded or set aside as a duplicate counts as got and is never downloaded again. `fetch --target` and `FetchOptions.dest` are refused with a message naming the change. fetch's stats say `got` where they said `held`, and lose `on_disk`.
- **Breaking: the domain rule moves from `wicket.reconcile` to `wicket.filing`** (`compute_domain`, `build_domain_query`), and the router from `wicket.ingest.lib` (`route_threads`, `AccountFor`). No re-export is left behind.
- **ingest's filing reads only archived rows as an anchor, and the thread's opener.** A local `.eml` whose row was only observed no longer inherits that row's derived domain, and a thread's folder no longer comes from the first later message that resolves.

### Changed
- **racecar tooling moves to `.racecar/`.** The synced checkers leave `scripts/`, telemetry is removed, `CLAUDE.md` becomes a pointer to `AGENTS.md`, and the CLI gains racecar's standard error packet, output schema and JSON renderer. `fetch --help` shows `--max N`.

## 0.5.0 - 2026-09-04

### Changed
- **Breaking: `example_outputs()` renamed to `example_output()`.** The optional
  CLI-docs contract (`racecar`'s `arch-python/CLI.md`) used the wrong plural — the
  plural of the mass noun "output" is "output", not "outputs". No deprecation
  window: the contract is optional and best-effort (`gen_cli_docs.py` reads it
  with `getattr(..., None)`), so a leaf still on the old name simply stops being
  picked up rather than erroring, and wicket's one call site is fixed in this same
  change. `scripts/gen_cli_docs.py` (wicket's local copy of the generator) is
  updated to read the new name.

## 0.4.0 - 2026-09-03

### Changed
- **`--account` renamed to `--mail-account` on `catalog`, `fetch`, `report`, and
  `ingest`.** The old name was overloaded: on `catalog`/`fetch`/`report` it scopes
  which of your own stores to operate on, but on `ingest` it is a destination
  override, not a scope — same flag name, two different roles. One name now
  covers both, with the role carried by which verb it's on. `$WICKET_ACCOUNT`
  (the env-var fallback) is unchanged.
- **`ingest --tags a,b,c` (comma-separated) replaced by a repeatable `--tag`**
  (`--tag a --tag b`), matching the repeatable-flag convention `wicket config`
  already uses (`--item`, `--add`, `--remove`).
- **`fetch --dest` renamed to `--target`.** `--dest` named the root directory
  downloaded `.eml` is filed under; `--target` is the more precise word for a
  transformation's destination, and it's what racecar's own CLI canon reserves
  for this concept.

## 0.3.0 - 2026-09-03

### Added
- **Per-thread routing in `ingest`.** Omit `--account` and each *thread* is routed
  to the store it belongs to, so one mixed folder lands in the right places. The
  thread is the unit, never the message: a conversation you were addressed on in
  some replies and cc'd on in others would otherwise split across two stores. The
  **sender counts** as a participant — mail you sent is addressed entirely to
  counterparties, so a recipients-only rule strands every outbound thread.
- **Buckets.** A store is now an *account* (a real mailbox) or a *bucket* (a
  topical destination that is no mailbox: `travel`, `shopping`). **The mailbox
  always wins a message's physical home**; a bucket is the home only for mail no
  mailbox owns. A manifest is the observation record *of a mailbox*, so relocating
  a mailbox-owned message into a bucket would only mean the next `catalog`
  re-observed it and wrote it back — the same message in two stores forever.
- **`wicket-report --bucket NAME`**: the view that replaces relocation. Reads
  across *every* store and reports what a bucket claims and where it lives.
- **Three owner-authored maps at the mail root** (all optional, none inferred):
  `account-aliases.json` (addresses that ARE you — exceptions only; supports
  bucket destinations and `*@domain` catch-alls), `domain-routes.json`
  (counterparties that are NOT you, and where their mail goes), and
  `domain-aliases.json` (subdomain folding). A counterparty listed as an *alias*
  would make the filing rule treat it as *you*, so the maps stay separate.
- **`--force` and an account-mismatch guard on `ingest`.** `--account` is a
  *destination, not a filter*: it does not select which messages are ingested.
  Naming the wrong one silently filed an entire folder into the wrong store, so a
  run whose folder is plainly addressed elsewhere is now refused.
- **`$WICKET_MAIL_ROOT`** overrides the mail root, so the tree can follow an
  encrypted vault to whatever path it mounts at.
- **`ingest` reports the already-archived share.** A re-export whose messages
  are already in the store is skipped, and its source files are still trashed
  (the archive is the record), so `7 read / 4 added / 7 trashed` read like
  loss. The run summary now names the gap: `; 3 already archived`.
- Tests for `ingest`, which previously shipped with none.
- **`wicket config`**: CRUD over the three owner-authored maps instead of
  hand-editing their JSON. `wicket config account aliases {list,create,update,delete}`,
  `wicket config domain aliases {...}`, `wicket config domain routes {...}`. `config`
  is a genuine noun (not a fifth action), so it takes its own nested subparsers —
  `--primary`, repeatable `--item`, `--add`/`--remove` — rather than flags on the
  root; every write validates with the same regexes the read side already
  validates with, so a row this writes is guaranteed readable back.
- **Per-map path overrides**: `$WICKET_ACCOUNT_ALIASES_FILE`,
  `$WICKET_DOMAIN_ALIASES_FILE`, `$WICKET_DOMAIN_ROUTES_FILE` each name that one
  map's file directly (any name, any location), the same escape hatch
  `$WICKET_MAIL_ROOT` gives the mail tree itself.

### Changed
- **`wicket/config.py` renamed to `wicket/env.py`.** Freed the `config` name for
  the new `wicket.config` verb package above — CLI-facing and internal package
  names now agree, rather than a verb called `config` living somewhere else.
  `from wicket.config import ...` (the shared env-resolution helpers:
  `resolve_account`, `MAIL_ROOT`, `Identities`, etc.) is now `from wicket.env
  import ...`. **Breaking** for any library consumer, pre-1.0.
- **Resynced to current racecar** (`8272ef6`, from `6bcc927`): 37 more synced
  check scripts refreshed; `check_cli_commands.py` now statically audits that
  every `add_parser(...)` name is a literal, which the CLI restructuring below
  had not yet met (fixed in the same pass — four `add_parser()` calls unrolled
  from a loop into literals).
- **CLI restructured: one `wicket` console script, verbs as subcommands.**
  `wicket-catalog` / `wicket-fetch` / `wicket-report` / `wicket-ingest` and their
  equivalent `python -m wicket.<verb>` modules are gone; the four verbs are now
  argparse subcommands of one Pattern-2 root — `wicket <verb> ...` and
  `python -m wicket <verb> ...` are the same dispatch. `wicket.catalog`,
  `wicket.fetch`, `wicket.report`, `wicket.ingest` no longer carry a
  `__main__.py` (each holds a non-entry `cli.py` instead), so `python -m
  wicket.catalog` is now refused by the interpreter itself rather than running.
  Reason: a dotted `wicket.<verb>` path reads as a noun/sub-noun, and none of
  the four are things you address — they are actions, so they belong in the
  subcommand slot, not the module path. **Breaking**, pre-1.0.
- **Upgraded to current racecar** (`6bcc927`, from `56fa87a`): 53 synced check
  scripts and `racecar.mk` refreshed, the pre-commit set conformed to
  `templates/classic` (secret scan, commit-message budget, changelog headings,
  content-blindness), formatter pins moved to canon, and the two orphaned
  checkers a rename and a retired rule left behind were removed. `pylint_pytest`
  left `[tool.pylint.MAIN].load-plugins`: it cannot survive the canonical
  parallel library pass, and `racecar.mk` already loads it for the one pass that
  needs it.
- **The gate now grades everything the repo commits**, not just `src/`: `tests/`
  and `scripts/` are linted and typechecked, which is what surfaced the findings
  fixed in this release.
- **Every tracked doc declares its place in the doc graph** (`pnode`
  frontmatter), and two docs stopped citing a checker that does not exist.
- **CLI usage telemetry** is recorded per invocation, local-only and gitignored.
- **`docs/summary/WICKET.md` is now a two-file bundle** — a summary plus
  `WICKET-DOSSIER.md` — stamped with the version it describes and held complete
  by an inventory. It no longer publishes row or file counts read off the mail
  root: those describe the records, not the program.

- **The mail root moved from `~/mail` to `~/.delphi/mail`** (see
  `$WICKET_MAIL_ROOT`).
- **`ingest` no longer leaves the source folder untouched.** Once a message is
  durably archived (filed now, or already in the store), its `.eml` is **moved to
  `~/.Trash`** — the archive is the record, the source folder is a volatile inbox.
  `--no-delete` opts out; `--dry-run` moves nothing; a message whose thread could
  not be routed always keeps its file. Nothing is ever unlinked.
- **`ingest` now folds subdomains when filing**, using `domain-aliases.json`. It
  had been passing an empty map, so `fetch` folded (`t.delta.com` → `delta.com`)
  and `ingest` did not.
- **The fold map has one home**, `<mail-root>/domain-aliases.json`, shared by
  `fetch` and `ingest`. It was per-account under the credentials dir, which a
  bucket (no mailbox, no credentials) can never have.
- **wicket never creates a store.** A destination — the mail root, an account, a
  bucket — is made by an explicit owner `mkdir`. A mistyped `--account` is now a
  hard error instead of a new directory.

### Fixed
- **Fail closed when the mail root is absent.** The tree is designed to sit in an
  encrypted (Cryptomator) vault; a *locked* vault leaves its mount point as an
  ordinary empty directory. wicket read that as "a fresh, empty store" and would
  `mkdir` an account and write mail **in plaintext** underneath the vault. Every
  verb now refuses, and creates nothing.
- **The filing rule knows all of your addresses.** It compared against a single
  account address, so mail to a burner or forwarding address of yours looked like
  mail to a *counterparty*, and filed under your own alias domain or not at all.
- **`.git` is no longer treated as an account.** `known_accounts` listed every
  directory under the mail root, and the store is often a git repo.

## 0.2.0 - 2026-07-02

### Added
- **`wicket ingest`** (`wicket-ingest` / `python -m wicket.ingest`): a fourth
  verb that files a flat local folder of `.eml` (a desktop-client drag-export)
  into the same year-sharded manifest + archive, for a mailbox wicket cannot
  reach over IMAP (e.g. Microsoft 365 with basic-auth IMAP disabled). Offline —
  no IMAP, no credentials. **Additive and non-destructive:** a message already
  archived (by its portable `Message-ID`) is left untouched, only new keys are
  filed, and nothing is ever deleted, so pruning the source folder cannot prune
  the archive. Idempotent. Threads are reconstructed from headers (augmented by
  the Outlook `Thread-Index`) and each message files under its counterparty
  domain via `reconcile.compute_domain`. Registered in the root `commands()` and
  the `[tool.importlinter]` layers contract (now four verbs).
- Per-verb api functions: `catalog`, `fetch`, and `report` (plus `senders` /
  `addresses`) drive the verbs as a library, not only over the CLI. Each verb
  co-locates a thin CLI face over an `api` orchestration module over its worker;
  the face -> api -> lib routing is the convention, reviewed not walled (the
  `layers` contract gates direction and acyclicity).
  Import each from its vertical (`wicket.catalog.api`, etc.);
  the package root holds no code (no aggregator re-exports), so it never shadows
  a submodule. `catalog` / `fetch` take a frozen options object
  (`CatalogOptions` / `FetchOptions`); `FetchOptions` owns the "exactly one of
  domains/query" invariant.

### Changed
- `catalog` now sweeps **incrementally by default**: only from the latest year
  already in the manifest through the current year, since the past does not
  change (matches ADR 0002 §4). `--full` re-sweeps every year from the oldest
  message; a first run (empty manifest) is full automatically. Old-year
  deletions are detected only under `--full`.
- Credentials are now per-account: `imap.json` lives at
  `<state-dir>/<account>/imap.json` (previously a single flat
  `<state-dir>/imap.json`). This is a breaking path change; move an existing
  credential file under its account directory, or re-seed it on first run.

### Documentation
- Root `README.md` refreshed: added a full **CLI reference** (every
  `wicket-catalog` / `wicket-fetch` / `wicket-report` flag, verified against
  `--help`) and explicit **"Where config lives"** and **"Where data lives"**
  sections. Config and secrets under `~/.config/gmail/<account>/` (`imap.json`,
  optional `domain-aliases.json`); data under `~/mail/<account>/`
  (`manifest/YYYY.jsonl`, `archive/<domain>/YYYY-MM/`). The security model stays
  in `src/wicket/README.md`; the root points to it rather than restating it.
- Root `README.md` now documents all four verbs: `wicket-ingest` added to the
  command table, the CLI reference (every flag, verified against `--help`), the
  getting-started flow, the library example, and the Providers section (the
  offline path when a mailbox can't be reached over IMAP).

## 0.1.0 - 2026-06-21

### Added
- Initial `wicket` package, extracted from `factotum`: read-only mail tooling
  over IMAP (app password, no OAuth) with two verbs, `catalog` (observe the
  mailbox into a year-sharded manifest) and `fetch` (download `.eml`, filed under
  each thread's counterparty domain), keyed by a provider-neutral id, with each
  message's state held as `deleted` / `downloaded` booleans.
- `wicket.report`: a read-only third sub-command over the manifest (`--senders`
  ranks From addresses by message count; `--addresses` lists every address ever
  seen in From/To). No IMAP, no credentials; streams to stdout.
- Public read API (`held_messages`, `manifest`) for consumers (meridian,
  factotum) to read held `.eml` and the manifest without touching the verbs.
- Design records under `docs/adr/`: the portable query grammar across Gmail and
  Fastmail (with `docs/QUERY-FIDELITY.md`), and the message manifest (one store,
  one key, two verbs, the directional filing domain).
