---
generator:
  name: racecar-llm-summary
  version: "0.8.0"
target:
  repo: wicket
  sha: 89f3572
  date: 2026-06-21
bundle:
  - WICKET.md

entities:
  - name: ManifestRow
    case: on_disk_managed
    purpose: One JSON Lines record per message in a year shard, keyed by a provider-neutral id, carrying identity, observation, settlement, and derived fields.
    path_pattern: ~/mail/<account>/manifest/<YYYY>.jsonl
    count: one row per message; sharded by UTC year of the message date
    validator: scripts/check_query_fidelity.py covers the query layer only; rows are not separately schema-validated
    notes: Field groups (ADR 0002 §2.2) are identity (id, msgid, thread_id, account), observation (date, from, to, subject, size, labels, deleted), settlement (downloaded, path), derived (domain). A row is progressively enriched, never all-or-none. All timestamps stored UTC ISO-8601.
  - name: EmlArchiveFile
    case: on_disk_managed
    purpose: The raw downloaded .eml for one message, filed by counterparty domain and year-month, the only artifact fetch ever pulls a body for.
    path_pattern: ~/mail/<account>/archive/<domain>/YYYY-MM/<msgid>.eml
    count: one file per downloaded message
    notes: downloaded is ground-truthed by this file's presence on disk; the manifest is a regenerable cache over it.
  - name: CredentialFile
    case: on_disk_managed
    purpose: Per-account IMAP login (email plus 16-char Gmail app password) cached owner-only outside the repo.
    path_pattern: ~/.config/gmail/<account>/imap.json
    count: one per account
    notes: JSON {"email":..., "app_password":...}, mode 0600, dir 0700. Never committed, never on the command line, never logged. Always per-account, no flat legacy fallback.
  - name: DomainAliasFile
    case: on_disk_managed
    lifecycle: realized
    purpose: Owner-authored map of secondary sender domains to a canonical primary, optionally consumed by fetch for filing and search expansion.
    path_pattern: ~/.config/gmail/<account>/domain-aliases.json
    count: zero or one per account (silently absent is empty)
    notes: 'Shape {"primary.com": ["alias1.com", "*.parent.com"]}. Wildcard *.parent.com folds every subdomain into primary. Missing file is silent; malformed file raises.'
  - name: CatalogOptions
    case: none
    purpose: Frozen value object carrying the knobs for one catalog sweep (store_dir, state_dir, mailbox, years, dry_run, threads), independent of which account.
    notes: interactive is deliberately not a field; it is an ambient capability of the calling face. Defined in wicket/catalog/api.py.
  - name: FetchOptions
    case: none
    purpose: Frozen value object carrying the filter and paths for one fetch run (domains XOR query, dest, state_dir, alias_file, limit, threads, dry_run).
    notes: __post_init__ enforces exactly one of domains or query, so a face cannot build an ambiguous request. Defined in wicket/fetch/api.py.
  - name: ThreadContext
    case: none
    purpose: Frozen per-run bundle of read-only parameters (dest, imap_email, domain_aliases, dry_run, store_dir) shared by fetch planning and worker threads.
    notes: Defined in wicket/fetch/lib.py; imap_email is the login email, the "me" identity the filing-domain rule resolves direction against.
  - name: PortableQuery
    case: none
    lifecycle: planned
    purpose: A single provider-neutral query string parsed into an AST and compiled per backend, modeled on Gmail's operators but pinned to deterministic IMAP/JMAP semantics.
    notes: Design-stage only (ADR 0001, docs/QUERY-FIDELITY.md). No parser or compiler exists; fetch today takes a raw Gmail string via --query.
  - name: MailProvider
    case: none
    lifecycle: planned
    purpose: The interface that will hide Gmail-specific X-GM-* IMAP primitives behind a seam so a Fastmail backend can be selected per run.
    notes: Phase 2/3 work; no provider.py exists yet. Gmail extensions are used directly in catalog/lib.py and fetch/lib.py today.

relationships:
  - from: ManifestRow
    to: EmlArchiveFile
    cardinality: "1:1"
    owner_side: ManifestRow
    notes: A downloaded row's path points at exactly one .eml; reconcile sets downloaded to whether that file exists. Not a DB FK.
  - from: ManifestRow
    to: ManifestRow
    cardinality: "1:N"
    notes: Rows sharing thread_id form a thread; the filing domain is computed once from the thread's earliest message and applied to all members.
  - from: CredentialFile
    to: ManifestRow
    cardinality: "1:N"
    owner_side: CredentialFile
    notes: The credential's login email is the mailbox-owner identity used to derive each row's direction (inbound vs outbound) and thus its domain.
  - from: DomainAliasFile
    to: ManifestRow
    cardinality: "1:N"
    notes: Aliases canonicalize the derived domain field and expand domain searches; absent file means identity mapping.

external_surface:
  cli_verbs:
    - verb: wicket-catalog
      module: wicket.catalog.__main__
      args: "--account --store-dir --state-dir --mailbox --years --threads --dry-run --non-interactive"
      behavior: Sweep mailbox headers (never bodies) into the year-sharded manifest; per-year IMAP workers; merges with existing settlement; idempotent.
      exit: "0 ok; 2 ValueError/AuthError/bad --years; 1 imaplib.IMAP4.error"
    - verb: wicket-fetch
      module: wicket.fetch.__main__
      args: "(--domains | --query) --dest --state-dir --account --alias-file --max --threads --dry-run --non-interactive"
      behavior: Download full .eml for matching Gmail threads, file under <domain>/YYYY-MM/<msgid>.eml, settle downloaded/path into the manifest. Exactly one of --domains/--query required.
      exit: "0 ok; 2 ValueError/AuthError; 1 imaplib.IMAP4.error"
    - verb: wicket-report
      module: wicket.report.__main__
      args: "[--senders | --addresses] --account"
      behavior: Read-only summaries over the manifest (no IMAP). No flag prints a one-screen count summary; --senders/--addresses stream one record per line.
      exit: "0 ok; 2 ValueError (unresolved account)"
    - verb: python -m wicket
      module: wicket.__main__
      args: none
      behavior: Pure discovery; prints the three verbs and exits 0. No CLI of its own.
      exit: "0"
  library_exports:
    - name: catalog
      module: wicket.catalog.api
      signature: "catalog(account: str | None = None, *, options: CatalogOptions | None = None, interactive: bool = False) -> dict[str, int]"
      behavior: Resolve account/store/credential paths, seed credentials (gated on interactive), sweep headers into the manifest, return a stats dict.
    - name: CatalogOptions
      module: wicket.catalog.api
      signature: "@dataclass(frozen=True) CatalogOptions(store_dir=None, state_dir=None, mailbox=ALL_MAIL_MAILBOX, years=None, dry_run=False, threads=4)"
      behavior: The run-knob value object passed to catalog().
    - name: fetch
      module: wicket.fetch.api
      signature: "fetch(account: str | None = None, *, options: FetchOptions, interactive: bool = False) -> dict[str, int]"
      behavior: Resolve account/dest/store/creds, compile domains-or-query into a Gmail search, download matching .eml, settle the manifest, return a stats dict.
    - name: FetchOptions
      module: wicket.fetch.api
      signature: "@dataclass(frozen=True) FetchOptions(domains=None, query=None, dest=None, state_dir=None, alias_file=None, limit=None, threads=4, dry_run=False)"
      behavior: The filter/path value object; __post_init__ enforces exactly one of domains/query.
    - name: held_messages
      module: wicket.fetch.api
      signature: "held_messages(account: str | None = None, domains: set[str] | None = None) -> Iterator[tuple[Row, Path]]"
      behavior: Read-only; yield (row, eml_path) for every downloaded message, optionally restricted to a set of filing domains. No IMAP, no credentials.
    - name: report
      module: wicket.report.api
      signature: "report(account: str | None = None) -> dict[str, int]"
      behavior: One-screen counts (messages, downloaded, gone, observed-only, distinct senders/addresses) over the manifest.
    - name: senders
      module: wicket.report.api
      signature: "senders(account: str | None = None) -> list[tuple[str, int]]"
      behavior: Every From address with its message count, descending.
    - name: addresses
      module: wicket.report.api
      signature: "addresses(account: str | None = None) -> list[str]"
      behavior: Every distinct address seen in From or To, sorted.
    - name: manifest
      module: wicket.report.api
      signature: "manifest(account: str | None = None) -> dict[str, Row]"
      behavior: "The whole year-sharded store as {id: row}, for downstream consumers."
    - name: resolve_archive_dir
      module: wicket.config
      signature: "resolve_archive_dir(account: str) -> Path"
      behavior: Per-account .eml archive root (~/mail/<account>/archive); the one home for that path.
    - name: resolve_store_dir
      module: wicket.config
      signature: "resolve_store_dir(account: str) -> Path"
      behavior: Per-account manifest store dir (~/mail/<account>/manifest); the one home for that path.
---

# wicket — Knowledge Package

Snapshot `89f3572`, 2026-06-21. Standalone, stdlib-only, read-only-over-IMAP mail
tooling, MIT licensed. Source of truth is the code under `src/wicket/`; this brief
was written against it, not against prior briefs.

## §1. Map

### §1.1 Purpose

wicket reads a mailbox over IMAP and exposes three verbs over one year-sharded
manifest. **catalog** observes the mailbox (headers only, never bodies) into the
manifest, recording what exists. **fetch** downloads full `.eml` for matching
threads, filed by counterparty domain, recording what is held. **report** reads
the manifest offline and summarizes it (top senders, every address, held-vs-gone
counts), touching no IMAP. The two IMAP verbs `SELECT` the mailbox read-only and
can never send, flag, label, or delete mail; that is a hard architectural
invariant, not a configuration default.

The named user is the repo owner (Vishal): a person who wants a durable, offline,
queryable archive of mail worth keeping (receipts, statements, travel) plus a
clean library other tools can depend on rather than re-implement IMAP. The
user-facing primitives are the three verbs, the manifest, the per-domain `.eml`
archive, and a small set of library functions (`README.md`; `src/wicket/README.md`).
Authentication is a Gmail app password, not OAuth: no Google Cloud project, no
browser consent, no refresh tokens (`auth.py`).

wicket was lifted out of `factotum`, a personal kitchen-sink repo, because more
than one project wants to archive mail; its consumers (factotum, meridian) are
meant to depend on it (`README.md`, "Why it exists").

### §1.2 Modules

| Module | Purpose |
|---|---|
| `wicket/__main__.py` | `python -m wicket`: pure discovery, lists the three verbs, no CLI of its own. |
| `wicket/config.py` | Account/credential/store-path resolution, IMAP host constants, secret writing, account normalization. The env layer. |
| `wicket/auth.py` | IMAP login over SSL with an app password; read-only `SELECT`; credential load/prompt. |
| `wicket/domains.py` | Sender-domain alias canonicalization (exact and `*.parent` wildcard) and alias-file loading. |
| `wicket/message.py` | The provider-neutral message key (normalized Message-ID, else `gmail:<id>`) and field-ownership tuples. |
| `wicket/manifest.py` | The year-sharded JSONL store: read/write/merge shards, settlement-vs-observation merge rules. |
| `wicket/reconcile.py` | Offline idempotent reconcile (downloaded against disk, domain from observation) plus the filing-domain rule and the Gmail domain-query builder. |
| `wicket/catalog/` | The observe verb: `lib` sweeps headers, `api` orchestrates, `__main__` is the CLI face. |
| `wicket/fetch/` | The download verb: `lib` discovers/plans/retrieves/settles, `api` orchestrates and holds `held_messages`, `__main__` is the CLI face. |
| `wicket/report/` | The read verb: `lib` summarizes the store, `api` orchestrates and holds `manifest`, `__main__` is the CLI face. |
| `scripts/` | Repo tooling synced from racecar (`check_*.py`); not importable, never imported by the package. |
| `docs/` | Plans and reference: `docs/adr/` decision records, `docs/QUERY-FIDELITY.md` divergence table. |

### §1.3 Vendors

None as runtime dependencies: `dependencies = []` in `pyproject.toml`, stdlib only
(`imaplib`, `ssl`, `email`, `json`, `concurrent.futures`, `queue`, `threading`).
The one external service is **Gmail's IMAP endpoint** (`imap.gmail.com:993`),
reached with an app password the user generates at
`myaccount.google.com/apppasswords`. Sibling local packages factotum and meridian
are downstream consumers, not dependencies of wicket. Dev tooling (black, isort,
pylint, mypy, pytest, import-linter, etc.) is a PEP 735 dependency group, not a
runtime dependency.

## §2. Implementation

### §2.1 Runtime

Two runtime shapes, no deployed service:

- **CLI faces.** Three console scripts (`wicket-catalog`, `wicket-fetch`,
  `wicket-report`) plus the dotted leaf CLIs (`python -m wicket.catalog`,
  `.fetch`, `.report`) and the discovery entry `python -m wicket`. Each leaf CLI
  is a thin argparse face that builds a frozen options object, calls its sibling
  `api`, maps exceptions to exit codes, and renders a human summary.
- **Library.** Import per vertical: `from wicket.catalog.api import catalog,
  CatalogOptions`, `from wicket.fetch.api import fetch, FetchOptions,
  held_messages`, `from wicket.report.api import report, senders, addresses,
  manifest`. The package root (`wicket/__init__.py`) is a namespace docstring with
  no code; never import the engine from the root.

| Entry point | Kind | Module |
|---|---|---|
| `wicket-catalog` / `python -m wicket.catalog` | CLI face | `wicket.catalog.__main__:main` |
| `wicket-fetch` / `python -m wicket.fetch` | CLI face | `wicket.fetch.__main__:main` |
| `wicket-report` / `python -m wicket.report` | CLI face | `wicket.report.__main__:main` |
| `python -m wicket` | discovery | `wicket.__main__:_print_commands` |
| `catalog` / `fetch` / `report` / `held_messages` / `manifest` | library | `wicket.<verb>.api` |

State lives under two roots, both outside the repo: mail data at
`~/mail/<account>/{manifest,archive}/`, credentials and aliases at
`~/.config/gmail/<account>/`. Everything is account-scoped; a single account under
`~/mail` is auto-discovered so `--account` is optional in that case.

### §2.2 Entities

See frontmatter `entities`. wicket has no database; its persistent shapes are
on-disk artifacts. The `ManifestRow` is the anchor: a JSONL record progressively
enriched across the three field-writers (identity always; observation by catalog;
settlement by fetch; domain derived by reconcile). It is keyed by a
provider-neutral id (`message.message_key`) so the store is portable to a future
Fastmail backend. `EmlArchiveFile` is the ground truth that `downloaded` mirrors;
`CredentialFile` and `DomainAliasFile` are owner-managed config files outside the
tree. The `*Options` and `ThreadContext` entries are `case: none` conceptual value
objects (frozen dataclasses), not stored data. `PortableQuery` and `MailProvider`
are `lifecycle: planned` design primitives with no code yet.

### §2.3 Relationships

No foreign keys; relationships are join-by-key and direction-by-rule over flat
files.

```
CredentialFile (login email = "me")
        |
        | sets direction + owner identity
        v
ManifestRow <--- thread_id groups ---> ManifestRow   (1:N, one thread)
   | id (normalized Message-ID, else gmail:<id>)
   | path
   v
EmlArchiveFile  (<domain>/YYYY-MM/<msgid>.eml)

DomainAliasFile --canonicalizes--> ManifestRow.domain
```

The `domain` field is derived from a thread's earliest message: inbound (From is
not the owner) files under the sender's domain; outbound (From is the owner) files
under the single external recipient's domain, or `null` when several distinct
domains (`reconcile.compute_domain`).

### §2.4 External surface

See frontmatter `external_surface` for the full `cli_verbs` and
`library_exports`. Load-bearing detail:

- **`wicket-catalog`** stats dict keys: `messages`, `shards`, `removed`,
  `parse_skips`, `label_misses`, `boundary_drops`. `--years` restricts to named
  shards (incremental); omitting it is a full sweep that derives the year range
  from the oldest message. `--dry-run` counts and writes nothing.
- **`wicket-fetch`** requires exactly one of `--domains` / `--query` (an argparse
  mutually-exclusive required group, re-asserted by `FetchOptions.__post_init__`).
  `--domains` is compiled to a Gmail `{from:d to:d ...}` expression with alias
  expansion (`reconcile.build_domain_query`); `--query` is a raw Gmail string
  passed through untouched (the escape hatch). Stats keys include `matched`,
  `held`, `candidates`, `pending`, `on_disk`, `downloaded`, `failed`.
- **`wicket-report`** with no flag prints a one-screen summary; `--senders` and
  `--addresses` stream one record per line and reset SIGPIPE so `| head` ends
  quietly.
- **`held_messages`** and **`manifest`** are the read-only library doors for
  downstream consumers (e.g. a travel parser reading airline `.eml`): no IMAP, no
  credentials.

### §2.5 Internal contracts

- **The manifest row schema (ADR 0002).** Producer: `catalog.lib` (identity +
  observation + `deleted`), `fetch.lib` (settlement: `downloaded`, `path`),
  `reconcile` (derived: `domain`). Consumers: every verb's `api`, `report.lib`,
  and downstream packages via `manifest()` / `held_messages()`. One JSONL row per
  message keyed by `id`.
- **The provider-neutral key (`message.message_key`).** Producer: both worker
  modules at row-build time. Consumer: every join across observation and
  settlement, and the store dedup. Normalized Message-ID, else
  `"<provider>:<native_id>"`.
- **The settlement-merge rule (`manifest.merge_catalog` / `merge_settlement`).**
  A catalog rewrite refreshes observation and carries forward `downloaded`/`path`;
  a complete sweep marks an unobserved-but-downloaded row `deleted=True` and drops
  an unobserved-undownloaded row (no tombstone). Producer/consumer: catalog and
  fetch writes into the same shard.
- **The Gmail domain query (`reconcile.build_domain_query`).** Producer:
  `fetch.api` from `--domains`. Consumer: `fetch.lib._search_uids` via
  `X-GM-RAW`. A `{from:d to:d}` OR-expression over the alias-expanded domain set.
- **The filing-domain rule (`reconcile.compute_domain`).** Producer: both
  `reconcile` (offline) and `fetch.lib._plan` (online), reading the same owner
  identity. Consumer: the `domain` field and the `.eml` path's first segment.

### §2.6 Configuration

- `WICKET_ACCOUNT` (env) — default account when `--account` is omitted; falls
  back to the sole directory under `~/mail` (`config.resolve_account`).
- `--account` (all verbs) — scopes manifest store, archive, and credentials;
  Gmail `+tag` aliases normalized.
- `--store-dir` (catalog) — override `~/mail/<account>/manifest`.
- `--dest` (fetch) — override `~/mail/<account>/archive`.
- `--state-dir` (catalog, fetch) — override `~/.config/gmail` base for
  `imap.json` and `domain-aliases.json`.
- `--mailbox` (catalog) — IMAP mailbox to sweep; default `"[Gmail]/All Mail"`.
- `--years` (catalog) — comma-separated INTERNALDATE years for an incremental
  sweep.
- `--alias-file` (fetch) — override the domain-alias JSON path.
- `--max` (fetch) — stop after N new messages (after store dedup).
- `--threads` (catalog, fetch) — concurrent IMAP workers; default 4
  (`config.DEFAULT_THREADS`), stay under Gmail's ~15 connection cap.
- `--dry-run` (catalog, fetch) — preview, write nothing.
- `--non-interactive` (catalog, fetch) — never prompt; fail loudly (non-zero) if
  credentials are missing or rejected. Cron/launchd relies on the loud failure.
- Constants in `config.py`: `IMAP_HOST=imap.gmail.com`, `IMAP_PORT=993`,
  `IMAP_TIMEOUT_SECONDS=60`, `ALL_MAIL_MAILBOX`, `DEFAULT_STATE_DIR`, `MAIL_ROOT`.

### §2.7 Flows

1. **catalog.** `catalog(account, options)` resolves the account and store dir,
   seeds credentials (gated on `interactive`), then `lib.sweep` opens an IMAP
   connection per target year. A full sweep finds the oldest message's UTC year
   and sweeps through the current year; `--years` restricts to named years. Each
   year worker `UID SEARCH`es a widened (one day each side) INTERNALDATE window,
   batch-fetches headers only (`X-GM-MSGID`, `X-GM-THRID`, `RFC822.SIZE`,
   `INTERNALDATE`, `X-GM-LABELS`, From/To/Subject/Message-ID), drops boundary rows
   whose UTC year belongs to the neighbor, and writes the shard via
   `merge_catalog(..., complete=True)`. Idempotent: same mailbox plus same
   settlement yields a byte-identical shard. Failure modes: a label sent as an
   IMAP literal yields `labels: null` (counted as `label_misses`, never guessed);
   an unparseable FETCH response is a `parse_skip`.
2. **fetch.** `fetch(account, options)` resolves paths, loads aliases, compiles
   the search (domains-or-query), seeds credentials (the login email becomes the
   "me" identity). `lib.download` first runs the offline `reconcile` (unless
   dry-run), then discovers on one connection: `X-GM-RAW` search, cheap
   `X-GM-MSGID` fetch, drop msgids the store already records downloaded, header
   fetch for the rest. It plans by thread (domain once per thread, `null`-domain
   threads skipped), splits into on-disk vs pending, then per-month worker threads
   batch-fetch `BODY.PEEK[]` bodies and write each `.eml`. Settlement merges
   `downloaded`/`path` into the year shards. Idempotent across runs (store dedup +
   reconcile); a failed month is retried on re-run.
3. **report.** Each `report`/`senders`/`addresses`/`manifest` call resolves the
   account, loads the whole store (`manifest.load_store`), and computes the answer
   in `report.lib`. No mutation, no IMAP, no credentials. `--senders`/`--addresses`
   stream; the summary prints held-vs-gone breakdowns.

### §2.8 Seams

- **Verb registry.** New sub-packages under `wicket/` must be registered in
  `wicket/__main__.py` `commands()` (explicit, no dynamic discovery) and added to
  the import-linter `layers` contract in the same change. Recent example: the
  `report` verb (`wicket/report/`), added after ADR 0002.
- **Provider seam (planned).** Phase 2 extracts the Gmail `X-GM-*` primitives in
  `catalog/lib.py` and `fetch/lib.py` behind a `MailProvider` interface
  (`provider.py`, not yet present); phase 3 adds Fastmail and `--source`. The
  CLAUDE rule is to not add a second provider's quirks ad hoc before the seam
  lands.
- **Query compiler seam (planned).** ADR 0001 / `docs/QUERY-FIDELITY.md` specify a
  portable grammar parsed to an AST and compiled per backend, drift-checked by
  `scripts/check_query_fidelity.py`. No compiler exists; `--query` is a raw Gmail
  string today.
- **Face seam.** Each verb's `__main__` is a thin face over its `api`; an MCP
  server is named as a future second face in `src/wicket/README.md`.

### §2.9 Design decisions

- **Provider-neutral Message-ID key over `X-GM-MSGID`** (ADR 0002 §1). Rejected
  the Gmail-native id because it is not portable to Fastmail; the normalized RFC
  Message-ID is present on essentially all mail, with a `gmail:<id>` fallback when
  absent.
- **One year-sharded JSONL store, not two stores and not one blob** (ADR 0002
  §2). Two stores buys nothing a `--force` rebuild does not, at the cost of a
  join; a monolithic `manifest.json` rewrites the whole file at six-figure scale.
  Year-sharding keeps a write to the affected year(s).
- **Manifest is a regenerable cache** (ADR 0002 context). `downloaded` is
  ground-truthed by disk and `domain` is derived from observation, so the store is
  rebuildable from mailbox plus disk. That is what makes a single store safe.
- **Two booleans, no bucket label** (ADR 0002 §6). State is `downloaded` and
  `deleted` spoken plainly; only a complete catalog can set `deleted`, and a
  gone-and-never-kept message is the absence of a row, not a tombstone.
- **Borrow Gmail's surface, pin to deterministic semantics** (ADR 0001). The
  portable grammar models Gmail's operators but pins meaning to the IMAP/JMAP side
  on every collision (received-date, substring, labels-not-folders), with `raw()`
  as a provider-bound escape hatch. Rejected provider-native passthrough (not
  portable), lowest-common-denominator (lossy and weak), and JSON filter
  (unreadable on a CLI).
- **One import-linter `layers` contract, no `forbidden` contract**
  (`pyproject.toml`, CLAUDE). The contract gates direction and acyclicity
  (defects); face to api to lib is a reviewed convention, not a wall.
- **`--force` and `--attachments` deferred** (ADR 0002 status banner). Decided in
  the ADR but not built; no flag on any verb, the `--force` relocation worker was
  removed pending reimplementation.

### §2.10 Operational

- **Install.** `python3 -m venv .venv && source .venv/bin/activate && pip install
  -e .` (Python 3.12+). `make install` / `make install-dev` are the racecar
  targets; `make help` lists them.
- **First run.** A first interactive run prompts for the Gmail address and app
  password and caches them 0600 at `~/.config/gmail/<account>/imap.json`.
- **System deps.** None beyond CPython 3.12 stdlib and network access to
  `imap.gmail.com:993`.
- **Concurrency limits.** One IMAP connection per worker; default 4 workers; stay
  under Gmail's ~15-connection cap. imaplib's 1MB line guard is raised to 10MB in
  `catalog/lib.py` so a full-mailbox `UID SEARCH ALL` does not trip it.
- **Scheduling.** Designed to run under cron/launchd with `--non-interactive`,
  which fails loudly on a missing or revoked app password (the surfacing signal).
- **Checks.** `make check` / `make arch`, pre-commit, and
  `.venv/bin/lint-imports` (expects `Contracts: 1 kept, 0 broken.`). No
  healthcheck endpoint or observability hooks; it is a CLI/library.
- **Kill switch.** Revoke the app password at `myaccount.google.com/apppasswords`.

### §2.11 Weirdness

- **Widened year search windows that overcount on purpose.** `catalog.lib`
  searches `SINCE 31-Dec-(y-1) BEFORE 02-Jan-(y+1)` because IMAP compares
  INTERNALDATE with its timezone disregarded, so a UTC-year-boundary message can
  surface a server-date day out of range. The worker then drops rows whose UTC
  year is not `year` (`boundary_drops`). Looks like double-counting; it is the
  mechanism that guarantees each message lands in exactly one shard.
- **`labels: null` is deliberate, not a bug.** When Gmail transmits a label as an
  IMAP literal the FETCH response splits and the label list cannot be recovered;
  the row records `null` (an honest gap) rather than a partial or guessed list.
- **`imaplib._MAXLINE` is mutated at import.** A 155k-message mailbox answers
  `UID SEARCH ALL` with a ~1.2MB single line; imaplib's hard-coded 1MB guard would
  kill discovery. Raising the private module attribute to 10MB is the sanctioned
  workaround for a stdlib limitation with no public knob.
- **`readonly=True` is the unconditional default on every `open_mailbox`,
  including worker threads.** This looks like an over-cautious default but is the
  load-bearing security invariant: the tools must never gain modify capability.
- **fetch may write rows catalog never observed.** `merge_settlement` adds a
  partial settlement-only row when fetch runs before catalog has seen a message;
  ADR 0002 progressive enrichment permits a row that is fetched-but-unobserved.
- **A `null` domain is a state, not an error.** An outbound thread addressed to
  several distinct external domains cannot be auto-filed; that thread is skipped
  by fetch and re-evaluated cheaply next run, and the row simply carries
  `domain = null`.

## §3. Live access

Pure library plus CLI; no deployed service.

### §3.1 Environments

N/A — no deployed instance. Local invocation only: `python -m wicket.<verb>` or
the `wicket-<verb>` console scripts. The one network dependency is Gmail IMAP
(`imap.gmail.com:993`), reached per run with the cached app password.

### §3.2 Auth

N/A — no deployed instance. (For completeness: the upstream Gmail IMAP login is an
app password, 16 chars, account-scoped and revocable; cached as
`{"email":"…","app_password":"…"}` at `~/.config/gmail/<account>/imap.json`, mode
0600. No OAuth, no token refresh.)

### §3.3 Operations

N/A — no deployed instance. The user-callable surface is the CLI verbs and library
exports in §2.4.

### §3.4 Rate limits

N/A — no deployed instance. The only practical limit is Gmail's cap of ~15
simultaneous IMAP connections, respected by keeping `--threads` low (default 4).

### §3.5 Errors

N/A — no deployed instance. CLI exit codes: `0` success; `2` for a resolution or
auth error (`ValueError`, `AuthError`, bad `--years`); `1` for a transport
`imaplib.IMAP4.error`.

### §3.6 SDKs

N/A — no deployed instance. wicket is itself the library; consumers pin a git tag
(`wicket @ git+https://github.com/vishalapte/wicket.git@main`).

## Confidence

**Least confident**

- §2.1 (Runtime): `pyproject.toml` declares `version = "0.1.0"` while the README's
  dependency-pin example uses `@v0.1.0`; whether a `v0.1.0` git tag actually
  exists was not verified. Verify with `git tag -l`.
- §2.4 / §2.7 (Surface, Flows): `catalog.lib.sweep()` returns `"removed": 0`
  unconditionally and contains no stale-shard removal, yet the catalog CLI
  description and `lib` docstring say a full sweep "removes stale shards." Stale
  removal appears documented-but-not-implemented in the read code. Verify against
  `src/wicket/catalog/lib.py` `sweep()` return dict and any shard-deletion path.
- §2.4 / §3.5 (Surface, Errors): `wicket-report` exit codes are inferred from
  `report/__main__.py` `run()` returning only `0`/`2`; it has no IMAP path so the
  `1` code on catalog/fetch does not apply. Confirmed against
  `src/wicket/report/__main__.py` lines 50-73, but absence of any other raised
  code is an inference from the current body.
- §2.2 (Entities): the `DomainAliasFile` default path
  (`~/.config/gmail/<account>/domain-aliases.json`) is derived from `fetch.api`
  joining `resolve_state_dir(...) / ALIASES_FILENAME`; the prose READMEs phrase it
  as `<state-dir>/domain-aliases.json` without the per-account segment. Verify
  against `config.resolve_state_dir` and `fetch.api.fetch`.

**Not in this brief**

- Revenue model, pricing, customer list — unknown — ask user (personal tooling, no
  commercial surface evident in source).
- Roadmap dates / sequencing beyond the phase labels in CLAUDE.md (phases 2-4) —
  unknown — ask user.
- Test coverage level and CI status: a `tests/test_api.py` exists and `.coverage`
  is present in the working tree, but the suite's breadth and pass state were not
  measured — unknown — ask user.
- Whether factotum/meridian have actually been rewired to depend on wicket (phase
  4) — unknown — ask user.
