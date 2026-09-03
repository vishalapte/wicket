---
summary: The release record: every notable change per version, Keep a Changelog / SemVer.
pnode: [README.md]
bearing: record
---

# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
