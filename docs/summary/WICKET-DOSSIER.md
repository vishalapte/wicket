---
role: dossier
generator:
  name: racecar-llm-summary
  version: "0.99.3"
target:
  repo: wicket
  date: 2026-09-04
  version: "0.10.0"
bundle:
  - WICKET.md
  - WICKET-DOSSIER.md
external_paths:
  - imap.json                            # the credential file, outside the tree under the state dir
  - myaccount.google.com/apppasswords    # where a Gmail app password is minted

entities:
  - name: Store
    case: on_disk_managed
    purpose: One directory under the mail root holding everything wicket knows about one destination — its manifest and its .eml archive.
    plane: control
    notes: >-
      A Store is either an Account or a Bucket. This distinction is the spine of
      the system. wicket NEVER creates one: a store exists because the owner ran
      mkdir. See §2.11.
  - name: Account
    case: none
    purpose: A Store that is a real mailbox, named by its address (you@gmail.com). The only kind catalog and fetch can sweep.
    parent: Store
  - name: Bucket
    case: none
    purpose: A Store that is NOT a mailbox — a topical destination (travel, shopping) with no IMAP behind it, no credentials, and nothing to sweep.
    parent: Store
    notes: >-
      A Bucket is the PHYSICAL home only for mail no mailbox owns. Grouping
      mailbox-owned mail by topic is a view (report --bucket), never a
      relocation. §2.9 explains why the alternative is unstable.
  - name: Message
    case: on_disk_managed
    purpose: One row in a manifest shard — the unit of record. Carries identity, what was observed, and what was settled.
    plane: data
    notes: >-
      Keyed by `id`, a provider-neutral message_key. Fields observed today are
      id, msgid, thread_id, date, from, to, subject, size, labels, deleted,
      downloaded, domain, path. `deleted` and `downloaded` are the two booleans
      the whole design turns on (ADR 0002).
  - name: ManifestShard
    case: content_tree
    purpose: One year of Messages for one Store, as JSON Lines. The write unit.
    plane: data
    validator: wicket.manifest.read_shard / write_shard (deterministic serialization)
  - name: EmlFile
    case: content_tree
    purpose: The downloaded RFC822 body of a Message, filed by counterparty domain and month. The durable artifact; the manifest is a rebuildable cache over it.
    plane: data
  - name: Thread
    case: none
    purpose: A conversation — the unit of both filing and routing. Reconstructed from headers, never from subject matching.
    notes: >-
      Connected components over Message-ID <-> References/In-Reply-To, augmented
      by the Outlook Thread-Index conversation id. A Thread has exactly one
      thread_id and one folder, which is why routing must not split it.
  - name: FilingDomain
    case: none
    purpose: The counterparty's domain — the folder a Thread files under. Derived, never stored as truth (the .eml path is the truth).
    notes: >-
      Direction decides the counterparty: for inbound mail it is the sender's
      domain; for outbound, the single external recipient's. Folded to a primary
      through DomainAliases.
  - name: Identities
    case: none
    purpose: Every address that is *you*. A membership test, not a set — a catch-all (*@shop.you.com) cannot be enumerated.
    notes: >-
      Without this the filing rule mistakes your OTHER addresses for
      counterparties and files a thread under your own burner domain, or not at
      all. Owner-authored: an address is yours because you listed it.
  - name: AccountAliases
    case: on_disk_managed
    purpose: Owner-authored map of addresses that ARE you to the Store that owns them. Exceptions only.
    plane: control
    validator: wicket.env.load_account_aliases
  - name: DomainRoutes
    case: on_disk_managed
    purpose: Owner-authored map of counterparties that are NOT you to the Store their mail belongs in. Consulted only when no mailbox claims the thread.
    plane: control
    validator: wicket.domains.load_domain_routes
  - name: DomainAliases
    case: on_disk_managed
    purpose: Owner-authored subdomain folding — what a domain is CALLED (t.delta.com -> delta.com). Shared by fetch and ingest.
    plane: control
    validator: wicket.domains.load_domain_aliases
  - name: Credentials
    case: on_disk_managed
    purpose: The IMAP app password for one Account. Lives outside the mail tree, never in the repo, never on a command line.
    plane: control
    notes: mode `0600`, dir `0700`. An app password is account-scoped and revocable — that is the kill switch.

relationships:
  - from: Store
    to: Message
    cardinality: "1:N"
    owner_side: Store
    notes: A Message belongs to exactly one Store. Two Stores holding the same message is the failure mode §2.9 exists to prevent.
  - from: Store
    to: ManifestShard
    cardinality: "1:N"
    owner_side: Store
    notes: One shard per year. A write touches only the affected year(s).
  - from: ManifestShard
    to: Message
    cardinality: "1:N"
    owner_side: ManifestShard
  - from: Message
    to: EmlFile
    cardinality: "1:1"
    owner_side: Message
    notes: Only when downloaded=true. The row's `path` points at it, and the file's first path segment IS the FilingDomain.
  - from: Thread
    to: Message
    cardinality: "1:N"
    owner_side: Thread
  - from: Store
    to: Thread
    cardinality: "1:N"
    owner_side: Thread
    notes: Routing assigns a whole Thread to exactly one Store; a Store holds many Threads. Per-message routing would tear a conversation across two stores.
  - from: FilingDomain
    to: Thread
    cardinality: "1:N"
    owner_side: Thread
    notes: Exactly one folder per Thread, chosen by wicket.filing's rank from its opener (earliest row in the store), so a reply never lands in a different folder than its root; one folder holds many Threads. Each filed row records the rank that placed it as `filed`.
  - from: Store
    to: AccountAliases
    cardinality: "1:N"
    owner_side: Store
    notes: Many alias entries resolve to one Store, which must ALREADY exist. An alias never creates a store.
  - from: Store
    to: DomainRoutes
    cardinality: "1:N"
    owner_side: Store
    notes: Same rule — a route to a Bucket you never created resolves to nothing, and the mail stays unrouted.
  - from: Account
    to: Credentials
    cardinality: "1:1"
    owner_side: Account
    notes: A Bucket has none, by construction. That is why per-account was the wrong home for the fold map.

external_surface:
  cli_verbs:
    - verb: python -m wicket
      module: wicket.__main__
      args: none
      behavior: The root noun's command line, in the form every noun uses (NounParser, one _cmd_<verb> per verb, --json on each). No args lists the nouns and the four verbs below and exits 0.
      exit: "0"
    - verb: wicket catalog
      module: wicket.__main__
      args: "--mail-account --store-dir --state-dir --mailbox --years --full --threads --dry-run --non-interactive"
      behavior: Sweep mailbox headers (never bodies) into the year-sharded manifest, recording what EXISTS. Read-only IMAP. Incremental by default; idempotent.
      exit: "0 ok, 2 on auth/config error"
    - verb: wicket fetch
      module: wicket.__main__
      args: "--domains | --query (at most one); --alias-file --max --mail-account --state-dir --threads --dry-run --non-interactive; --target is refused"
      behavior: Get full .eml for matching messages into <store>/staging/<thread>/<msgid>@<INTERNALDATE>.eml, filing nothing; ingest --staged files them. Read-only IMAP.
      exit: "0 ok, 2 on auth/config error"
    - verb: wicket report
      module: wicket.__main__
      args: "--senders | --addresses | --bucket NAME (mutually exclusive); --mail-account"
      behavior: Read-only summaries over the manifest. No IMAP, no credentials. --bucket reads across EVERY store and reports what a bucket claims and where it physically lives.
      exit: "0 ok, 2 on config error"
    - verb: wicket ingest
      module: wicket.__main__
      args: "--src | --staged (exactly one); --mail-account --source {local} --domain --tag --force --no-delete --dry-run"
      behavior: The only verb that files. --staged files what fetch got for one account (duplicates to <mail root>/.duplicates/<store>/). --src files local .eml (a folder, or one file); omit --mail-account to route each thread to the store it belongs to. No IMAP. Additive. Archived sources move to ~/.Trash unless --no-delete. A row marked excluded is skipped and counted.
      exit: "0 ok, 2 on mismatch / absent root / no .eml"
    - verb: python -m wicket.alias
      module: wicket.alias.__main__
      args: "{list,create,update,delete} [--primary --item --add --remove] [--json]; --item, --add, --remove repeat"
      behavior: Edit domain-aliases.json, which domains are really another. No IMAP. Applies the edit in memory, checks it with domains.parse_domain_aliases, then replaces the file. Text output, JSON under --json.
      exit: "0 ok, 2 on a refused edit / absent root"
    - verb: python -m wicket.store
      module: wicket.store.__main__
      args: "{list,create,delete} [--store --from --to] [--json]; --from and --to repeat; create/delete need --store and one of --from/--to"
      behavior: Edit which store mail goes to. --from a sender domain writes domain-routes.json, --to one of your addresses writes account-aliases.json, both or neither. No IMAP. A domain or address already sent to another store refuses the whole command.
      exit: "0 ok, 2 on a refused edit / absent root"
    - verb: python -m wicket.halt
      module: wicket.halt.__main__
      args: "{list,set,clear} [--mail-account | --all (exactly one, set/clear)] [--json]"
      behavior: List, set or clear an account's HALT through wicket.auth (write_halt, clear_halt), the one door, which logs every set and clear to log/<account>.log. A HALT is an empty, touched file that never expires (name = account, timestamp = when); set keeps a HALT in force; clear removes it; list reads names and timestamps. No IMAP.
      exit: "0 ok, 2 on a refused request / a name that is no address"
    - verb: python -m wicket.account
      module: wicket.account.__main__
      args: "{list,create <name> --config <folder>,delete <name>} [--json]"
      behavior: An account is a folder ~/.config/wicket/accounts/<name>/ holding imap.json, a link to the provider's file; every folder there holding an imap.json is the one list halt --all reads, and fetch and catalog resolve the named one. create makes the folder and links imap.json to <config>/imap.json (the same link again is a no-op); delete removes the imap.json link, or renames a real imap.json to imap_YYYYMMDD_HHMM.json, and keeps the folder. create and delete print nothing. No IMAP, no mail root.
      exit: "0 ok or no-op, 2 when --config does not exist, imap.json has another target, or delete's new name is taken"
    - verb: python -m wicket.exclude
      module: wicket.exclude.__main__
      args: "{list,create --list L,delete --list L} [--mail-account A] [--json]"
      behavior: Edit excluded-lists.json at the mail root, the mailing lists never fetched or filed. No --mail-account is "*", every account; --mail-account A is that account alone, and must be a folder holding imap.json. fetch leaves each list out of every search, ingest skips its mail, catalog records everything with each row's list_id, and report leaves out a row whose list_id is on the map. No IMAP.
      exit: "0 ok, 2 on a key that is no account folder, a malformed list-id, or an absent root"
  library_exports:
    - name: catalog
      module: wicket.root.api
      signature: "catalog(account: str | None = None, *, options: CatalogOptions) -> dict"
      behavior: Observe a mailbox into its manifest; the stats name the store dir it swept into.
    - name: fetch
      module: wicket.root.api
      signature: "fetch(account: str | None = None, *, options: FetchOptions) -> dict"
      behavior: Get matching .eml into the store's staging dir. FetchOptions enforces at-most-one-of domains/query, and refuses dest, at construction.
    - name: held_messages
      module: wicket.root.api
      signature: "held_messages(account=None, *, domains: set[str] | None = None) -> Iterator[tuple[Row, Path]]"
      behavior: The consumer-facing read — every downloaded message and the path to its .eml. No IMAP, no credentials. This is what factotum and meridian actually want.
    - name: ingest
      module: wicket.root.api
      signature: "ingest(account: str | None = None, *, options: IngestOptions) -> dict"
      behavior: File staged mail (IngestOptions(staged=True)) or a local .eml export. For a local export, account=None means ROUTE, not "guess the default" — the one place that argument does not mean what it means elsewhere.
    - name: bucket
      module: wicket.root.api
      signature: "bucket(name: str) -> dict[str, list[tuple[str, Row]]]"
      behavior: Every message ANY store holds that a bucket claims, keyed by store. The view that replaces relocation.
  scripts:
    - name: 'install_system_deps.sh'
      path: 'scripts/install_system_deps.sh'
      purpose: 'Install system dependencies that cannot be pip-installed (called by make system-deps)'

# The losslessness invariant: for every kind below, the members this brief
# declares and the members the tree yields must be the SAME SET. Where a kind
# names no source, it says so and is warned on every run.
inventory:
  - kind: cli-verb
    describes: One user-callable command wicket installs or exposes as `python -m`.
    members_from: external_surface.cli_verbs[].verb
    sources:
      - match: pattern
        glob: src/wicket/__main__.py
        pattern: '^\s*\("(?P<id>\w+)",'
        id_format: 'wicket {id}'
      - match: path
        glob: src/wicket/__main__.py
        id_format: python -m wicket
      - match: path
        glob: src/wicket/*/__main__.py
        id_format: "python -m wicket.{parent}"
  - kind: verb-package
    describes: One package under src/wicket/ with its own api -- the root noun (the four root verbs' api) and the alias, store, halt, account and exclude nouns.
    members: [account, alias, exclude, halt, root, store]
    sources:
      - match: path
        glob: src/wicket/*/api.py
        id_format: "{parent}"
  - kind: adr
    describes: One dated architecture decision record under docs/adr/.
    members:
      - docs/adr/0001-portable-query-grammar.md
      - docs/adr/0002-unified-message-manifest.md
      - docs/adr/ADR-MAIL.md
    sources:
      - match: path
        glob: docs/adr/*.md
  - kind: delivered-script
    describes: One repo-tooling script under scripts/, synced verbatim from racecar and never imported by the package.
    members_from: external_surface.scripts[].path
    sources:
      - match: path
        glob: scripts/**/*.py
      - match: path
        glob: scripts/**/*.sh
      - match: path
        glob: scripts/**/*.json
  - kind: entity
    describes: One class-level domain shape the system reasons about, stored or conceptual.
    members_from: entities[].name
    derivation: none — nothing in the tree enumerates the domain classes; they are read out of the code and the ADRs by hand.
  - kind: library-export
    describes: One callable a consumer (factotum, meridian) imports from a verb's api module.
    members_from: external_surface.library_exports[].name
    derivation: none — each verb's api.py declares its own one-line __all__ and nothing aggregates them, so a line-anchored extractor yields at most one name per module.
---

# wicket — Dossier

The enumerating half of the bundle: every set the repo declares about itself,
plus the implementation detail behind it. Read [`WICKET.md`](WICKET.md) first —
it is the entry point and carries the synthesis. Nothing is restated between
them.

## §1. Map

### §1.1 Purpose

wicket archives a mailbox to local `.eml` files, filed by counterparty domain and queryable offline, and exposes the result as a library so other tools can read your mail without re-implementing IMAP. It is **read-only on the mailbox by design**: `SELECT` is always read-only, and the tools can never send, flag, modify, or delete a message. The worst thing wicket can do to a mailbox is download from it.

The user is a single technical owner archiving their own mail. There is no multi-tenancy, no server, and no auth beyond an IMAP app password. Pure Python standard library, zero runtime dependencies, no OAuth, no cloud project.

The primitives a user actually handles: a **Store** (a directory of mail — an *account* or a *bucket*), a **Message** (a manifest row), an **EmlFile** (the durable body), a **Thread** (the unit of filing and routing), and three small **owner-authored maps** that say who you are, who your counterparties are, and what domains are called.

It was lifted out of `factotum` (a personal kitchen-sink repo) because more than one project wanted to fetch and archive mail. Its consumers — factotum, meridian — are meant to depend on it rather than copy it.

### §1.2 Modules

| Module | Purpose |
|---|---|
| `wicket/root/` | The root noun: each root verb's api and request function (`root/api.py`), its worker (`root/lib/<verb>.py`), its result type (`root/lib/results.py`) and its text (`root/lib/renderer/plaintext.py`). `catalog` is here: observe the mailbox (headers only) into the manifest, recording **what exists** (IMAP). So is `fetch`: get full `.eml` for matching messages into the store's `staging/`, filing nothing (IMAP). So is `report`: read-only summaries over the manifest (top senders, every address, a bucket across every store; no IMAP). So is `ingest`, the only filer: staged mail (`--staged`), or a local `.eml` drag-export routed per thread (no IMAP). |
| `wicket/env.py` | The environment layer: the mail root, the destination rules (who is *me*, which stores exist), the alias-file rule. Imports nothing above itself. |
| `wicket/filing.py` | The one home for where mail is filed: the domain, thread, self and sent rules, the rank, `filed`, and the router. |
| `wicket/reconcile.py` | The offline `downloaded`/`domain` reconcile. |
| `wicket/domains.py` | Subdomain folding and counterparty routing — the two domain maps. |
| `wicket/manifest.py` | The year-sharded store: read, write, and merge shards, deterministically. |
| `wicket/message.py` | The provider-neutral `message_key`, the per-mailbox `mailbox_key`, field ownership, and a local export's header threads. |
| `wicket/provider.py` | Each mail provider's host, folder, FETCH items, search, id order and file name (`GMAIL`). |
| `wicket/auth.py` | IMAP login and read-only `SELECT`; app-password load/prompt. |

Each root verb runs `root/lib/<verb>.py` (the worker) → `root/api.py` (orchestration and its request function) → the root `__main__.py` (the CLI face, one `_cmd_<verb>`).

### §1.3 Vendors

None, in the paid sense. wicket has **zero runtime dependencies** (`dependencies = []` in `pyproject.toml`) and speaks to Gmail's (`imap.gmail.com:993`) or Fastmail's (`imap.fastmail.com:993`) IMAP endpoint with an app password over stdlib `imaplib`, the provider chosen by where an account's `imap.json` links. No SaaS, no cloud platform, no billing relationship, no provider cloud project.

Sibling local packages: factotum and meridian are *consumers* of wicket, not dependencies of it. delphi reads wicket's manifest as a data contract and never `import`s wicket (`docs/adr/ADR-MAIL.md`).

## §2. Implementation

### §2.1 Runtime

One runtime: a **CLI, and the library beneath it**. There is no server, no daemon, and no scheduled job that wicket owns (the owner may drive it from cron or launchd, which is what `--non-interactive` exists for).

| Entry point | Form |
|---|---|
| `wicket` | one console script (`[project.scripts]`); `wicket <verb> ...` |
| `python -m wicket <verb>` | equivalent module invocation — the same root `main()` runs both |
| `python -m wicket` | no args: prints subcommand help and exits 0 |
| `from wicket.root.api import ...` | the library surface; the package root is a namespace, not an API |

**State lives entirely on disk**, in two places that never mix. The **mail root** (`~/.delphi/mail`, override `$WICKET_MAIL_ROOT`) holds every Store and the four maps. `~/.config/wicket/accounts/<account>/imap.json`, a link to the provider's file, holds credentials. Secrets are never inside the data tree, never in the repo, and never on a command line.

### §2.2 Entities

See frontmatter `entities`. The narrative gloss that YAML cannot carry:

**A Store is an Account or a Bucket, and that split is the spine of the design.** An Account is a real mailbox: `catalog` sweeps it, `fetch` downloads from it, it has credentials. A Bucket (`travel`, `shopping`) is a topical destination with no mailbox behind it — nothing to sweep, nothing to authenticate. Both are just directories with a `manifest/` and an `archive/`, so every verb that reads a store works on either without knowing which it has.

**The manifest is a cache; the `.eml` is the truth.** `downloaded` is ground-truthed against what is actually on disk, and a downloaded row's filing domain is read back off its committed path rather than recomputed. That is what makes the store rebuildable from mailbox plus disk, and it is also why re-folding an already-archived message is a *migration* and not a `reconcile` (§2.11).

**Identities is a test, not a list.** It answers "is this address me?", and it has to be a predicate: a catch-all (`*@shop.you.com`) has infinitely many members and cannot be enumerated.

### §2.3 Relationships

See frontmatter `relationships`.

```
            AccountAliases                 DomainRoutes
            (who is me)                    (who is NOT me)
                   \                          /
                    \                        /
                     v                      v
                  +--------------------------------+
                  |             Store              |
                  |     Account    |    Bucket     |
                  +----+----------------------+----+
                       | 1:N                  | 1:N
                       v                      v
                 ManifestShard             EmlFile
                 (YYYY.jsonl)        (<domain>/<YYYY-MM>/)
                       | 1:N                  ^
                       v                      | 1:1 (when downloaded)
                    Message ------------------+
                       | N:1
                       v
                    Thread  --- N:1 --->  FilingDomain
                       |                        ^
                       +--- routes to ONE Store |
                                                |
                                          DomainAliases
                                       (what it is called)

  Account --1:1--> Credentials      (a Bucket has none, by construction)
```

### §2.4 External surface

See frontmatter `external_surface`. Two kinds: `cli_verbs` (5) and `library_exports` (5). No HTTP, no MCP, no webhooks, no signals — wicket is a library and a CLI, not a service.

The load-bearing detail:

**`wicket ingest --mail-account` is a destination, not a filter.** It names the store to write *into*; it does **not** select which messages are ingested. Naming the wrong one silently files an entire folder into the wrong store. This cost real data (§2.9), so a run whose folder is plainly addressed to a *different* known account is now refused, with a message naming the account that actually owns the mail. `--force` overrides.

**`wicket ingest` moves your source files.** Once a message is durably archived (filed by this run, or already in the store), its `.eml` is **moved to `~/.Trash`**. `--no-delete` opts out. A message whose thread could not be routed always keeps its file. Nothing is ever unlinked, and `--dry-run` moves nothing.

**`wicket report --bucket NAME` ignores `--mail-account` on purpose:** a bucket is a question about *every* store.

### §2.5 Internal contracts

- **The manifest row** (`wicket.manifest.Row`). Producer: `catalog` (observation), `ingest` (settlement, and observation for a message catalog never saw); `fetch` writes only a link (a Gmail msgid onto a row ingest filed). Consumer: `report`, `reconcile`, and **delphi**, which reads it as a data contract to map Message-ID → archive path and never imports wicket (`docs/adr/ADR-MAIL.md`). This is the one cross-repo wire shape: renaming a row field breaks a sibling repo silently. A row the owner marks `excluded` (`"duplicate of <row id>"`) is a Gmail copy fetch never downloads, ingest skips and counts, and the report views skip; a catalog carries it forward. `filed` records which filing rule placed a row (`rule`, `forced`, `self`, `sent`, `fallback`, `unfiled`); a catalog carries it too.
- **`message_key`** (`wicket.message`). The provider-neutral identity: a normalized RFC `Message-ID`, or `<provider>:<native-id>` when a message has none. Producer: all three writing verbs. This is what makes a Gmail-harvested message and an Outlook-exported message the *same* message. A row is keyed per Gmail message (`mailbox_key`): the first message with a Message-ID keeps the bare key, and a later, different message reusing it gets `<key>|gmail:<gmail id>`, so a reader must not assume one row per Message-ID.
- **`AccountFor`** (`wicket.filing`). A `Callable[[str], str | None]` resolving an address to the store that owns it. The api binds it from `env.account_of` plus the stores that exist, so the worker reads no config file and lists no directory of its own. The counterparty router has the same shape.
- **`Target`** (`wicket.root.lib.ingest`). Where an ingest writes and who the owner is there: store dir, archive dir, account, Identities, DomainAliases. The five travel together because the filing rule cannot say who the counterparty is without knowing who *you* are.
- **The `uchg` immutable flag** (designed, not built — `ADR-MAIL.md`). The pin delphi sets on a wicket-owned `.eml` it references; wicket's prune must skip `UF_IMMUTABLE` files. A filesystem flag rather than a manifest bit, so neither repo writes the other's state.

### §2.6 Configuration

| Name | Effect |
|---|---|
| `$WICKET_MAIL_ROOT` | Where the mail tree lives. Default `~/.delphi/mail`. Exists so the tree can follow an encrypted vault to whatever path it mounts at. |
| `$WICKET_ACCOUNT` | Default account when `--mail-account` is omitted. Falls back to the only folder in `~/.config/wicket/accounts` holding an `imap.json`; none, or several, refuses (exit 2). |
| `<mail-root>/account-aliases.json` | Addresses that **are you** → the store that owns them. Exceptions only: an address that already names a store resolves to itself. A primary may be an account address or a bucket name; an alias may be a literal address or a `*@domain` catch-all. |
| `<mail-root>/domain-routes.json` | Counterparties that are **not you** → where their mail goes. Used only when no mailbox claims the thread. |
| `<mail-root>/domain-aliases.json` | Subdomain **folding** (`*.delta.com` → `delta.com`). Shared by `fetch` and `ingest`; `--alias-file` overrides. |
| `<mail-root>/excluded-lists.json` | Mailing lists never fetched or filed: `"*"` for every account, an account key for that one. `fetch` leaves them out of every search, `ingest` skips their mail; `catalog` records everything, each row's `list_id` too, and `report` leaves out a row whose `list_id` is on the map. Edited by `wicket.exclude`. |
| `~/.config/wicket/accounts/<account>/imap.json` | Address and app password, through the account's link. File `0600`. Seeded on first run; `--state-dir` overrides the base; no account folder is a silent no-op. |
| `~/.local/state/wicket/halt/<account>` | The account's HALT (`$XDG_STATE_HOME`); present means halted. Only `wicket.auth` writes or removes it. |
| `~/.local/state/wicket/log/<account>.log` | fetch's counts and years, and every HALT set and cleared. Never a subject, another address, a path or a secret. |
| `DEFAULT_THREADS = 4` | Worker pool per verb. One worker is one IMAP connection; Gmail caps simultaneous connections near 15, so stay well under. |

All four maps are optional, and **nothing in them is inferred** — an entry exists because the owner wrote it.

### §2.7 Flows

1. **catalog.** Resolve the account and its store → load credentials (prompt unless `--non-interactive`) → open the mailbox read-only → per year, in parallel workers each with its own IMAP connection, fetch headers and build rows → merge into the year shard: an observed message is added or updated, and a message the sweep *should* have seen but did not is marked `deleted` (only when the sweep was complete for that year). **Idempotent**: re-running with nothing new changes nothing. **Failure**: a rejected app password exits non-zero and loudly — there is no silent fallback, because cron depends on the noise.

2. **fetch.** Resolve account, store, staging dir, duplicates dir and the fold map → build the search (from `--domains`, expanded through the fold map, or a raw `--query`) → reconcile offline first, ground-truthing `downloaded` against disk → drop every message already **got** (filed, staged, excluded, or set aside as a duplicate) → download the rest into `<store>/staging/<thread>/<msgid>@<INTERNALDATE>.eml`, filing nothing and settling no row. **Idempotent**: a got message is skipped by the dedup, not re-downloaded. **With no filter** it runs that pass year by year (this year, last year, then the rest oldest first), repeating a year while the got count grows, until Gmail throttles. **Failure**: the first throttle (a rejected or timed-out command, a failed month, a short body) writes the account's HALT, `~/.local/state/wicket/halt/<account>`, logs it in `log/<account>.log`, stops every worker, and exits 2; no IMAP connection opens past a HALT, and a store another process holds exits 2. `ingest --staged` files what fetch got: lock, reconcile, key each body as catalog did, write the row, then move the body into `archive/` by `wicket.filing`'s rank; a body archived under another name goes to `<mail root>/.duplicates/<store>/`.

3. **ingest.** Parse every `.eml` in `--src` — a folder (non-recursive) or a single file — and dedup by `message_key` → reconstruct threads from headers → **route each thread**: the mailbox that took part wins; failing that, a counterparty route; failing that, the thread is *unrouted* and left entirely alone → per destination, place each thread by `wicket.filing`'s rank (archived anchor, `--domain`, self, sent, counterparty domain, fallback, `_unfiled`) read from its opener, skip messages already archived or `excluded`, write the new bodies, and union the new rows into the year shards → move the source `.eml` of every durably-archived message to `~/.Trash`. **Idempotent**: a second run adds 0. The run summary counts the already-archived share separately (`; N already archived`), because a run that trashes every source file while adding fewer rows than it read is otherwise indistinguishable from one that lost mail. **Failures**: an absent mail root fails closed (§2.11); a `--mail-account` that disagrees with the source is refused; a source with no `.eml` errors.

4. **report --bucket.** Load the two domain maps → walk every store → fold each sender's domain → keep the rows whose folded domain routes to the named bucket → group by store. Read-only, no IMAP.

### §2.8 Seams

- **Provider seam.** Each provider's host, all-mail folder (or the folders a pass walks), native id, thread and labels items, search, id order, file name, staged name and required capability live in `wicket.provider` (`GMAIL`, `FASTMAIL`); `auth`, the workers and the api ask the provider and never test its name. An account's provider is where its `imap.json` links (`~/.config/gmail/` or `~/.config/fastmail/`); anything else is refused. Fastmail walks every folder but Trash, Spam and its four non-mail folders, one message per `EMAILID`, names a file by a lowercase digest of it, and takes no `--domains`, `--query` or `catalog --mailbox` until ADR 0001's compiler exists.
- **Export-profile seam.** `ingest --source {local}` is a one-value enum today, reserving the flag for other export shapes (mbox, an Apple Mail package) without a breaking change.
- **Resolver seam (live).** `root/lib/ingest.py` takes `AccountFor` callables rather than reading config itself, so routing policy can change entirely in the api with no worker change. Recent example: the counterparty router was added as a second resolver argument to `route_threads` (`src/wicket/filing.py`) without touching the filing code.
- **Verb registration.** A new verb gets a `cli.py` (no `__main__.py`) and is added to the root `__main__.py`'s `subcommands()` list and `_VERBS` dict. Explicit registration, no dynamic discovery.

### §2.9 Design decisions

- **A unified manifest, not two stores** (ADR 0002). One row per message carrying both observation (`deleted`) and settlement (`downloaded`), year-sharded so a write touches only the affected years. Rejected: separate "seen" and "held" indexes, which drift apart.
- **The `.eml` is the truth, the manifest is a cache** (ADR 0002). `downloaded` is verified against disk, and a downloaded row's domain comes from its committed path, so the store rebuilds from mailbox plus disk. Consequence: `reconcile` deliberately will not move an archived file.
- **The mailbox wins a message's physical home; buckets are views** (2026-07-13). A manifest is the observation record *of a mailbox*. If a topical rule could pull a Gmail-owned message into `shopping`, the next `catalog` would re-observe it in Gmail and write it back — the same message in two stores forever, recreated by every run. So a bucket is the physical home only for mail no mailbox owns, and `report --bucket` answers the topical question as a view. The counterparty map originally outranked the mailbox; it was inverted for exactly this reason.
- **Routing is per thread, never per message** (2026-07-13). A conversation you were addressed on in some replies and cc'd on in others would otherwise split across two stores, and a thread has one `thread_id` and one folder by construction.
- **The sender counts as a participant** (2026-07-13). Routing on recipients alone stranded every outbound thread, because mail *you sent* is addressed entirely to counterparties. Found in real data: 16 unrouted messages became 8.
- **Three maps, not one** (2026-07-13). "This address is me" and "this counterparty's mail goes here" have the same file shape and opposite meanings. Listing IKEA in the identity map makes every message *from* IKEA look outbound, so it files under the recipient's domain instead of `ikea.com`. Separate files, separate loaders.
- **`--mail-account` is a destination, not a filter, and the guard exists because that cost data** (2026-07-13). Ingesting one folder twice under two accounts filed 124 messages into two stores. Nothing was lost (ingest is additive), but the recovery was a hand-written script. A folder plainly addressed to another known account is now refused.
- **Never create a store; fail closed on an absent mail root** (2026-07-13). The tree is designed to live in an encrypted (Cryptomator) vault, and a *locked* vault leaves its mount point as an ordinary empty directory. wicket read that as "a fresh, empty store" and would `mkdir` an account and write mail **in plaintext underneath the vault**. Every verb now refuses and creates nothing; creating a destination is always an explicit owner `mkdir`.
- **Trash, never unlink** (2026-07-13). The archive is the record and the source folder is a volatile inbox, so an ingested `.eml` is *moved* to `~/.Trash` once durably archived. The destructive default still has an undo.
- **App password, not OAuth** (initial). No consent screen, no cloud project, no refresh dance. The cost is a standing credential; the mitigations are that it is account-scoped and revocable in one click, and that `SELECT` is read-only, so it can only ever read.
- **Rejected: symlinking bucket views into the account archives.** Attractive (one physical copy, browsable in Finder), and already the pattern delphi uses. Deferred because Cryptomator serves the vault over FUSE-T or WebDAV, which do not implement BSD file flags and where symlink support is backend-dependent — the view would be unreliable exactly where the data is going to live (`ADR-MAIL.md`).

### §2.10 Operational

- **Install:** `python3 -m venv .venv && pip install -e .` (Python 3.12+). `make install-dev` adds the dev tools and pre-commit hooks. No system dependencies, no services, no build step.
- **Gate:** `make check` (isort, black, pylint, pytest) plus `lint-imports`, which must report `Contracts: 1 kept, 0 broken.` The import-linter contract in `pyproject.toml` is the source of truth for layering: face → api → worker → `reconcile` → shared → `env`, downward only.
- **First run** prompts for an address and app password and caches it `0600`. `--non-interactive` never prompts and **fails loudly** — cron and launchd depend on that noise to surface a revoked password. Do not add a silent fallback.
- **Scheduling** is the owner's (cron/launchd). wicket ships no scheduler, no healthcheck, no metrics, and no logging config; observability is each verb's stdout summary and its exit code.
- **Backups are not wicket's problem, and currently do not exist.** The mail tree is deliberately never committed to git, is not in delphi's S3 corpus backup, and this machine has no Time Machine destination. Encryption (planned) is not a backup. Anything destructive should be preceded by an explicit copy.

### §2.11 Weirdness

**An empty mail root is an error, not an empty store.** Every other tool treats "directory missing" as "nothing here yet, let me create it". wicket refuses and exits 2. The tree is designed to live in an encrypted vault, and a *locked* vault is indistinguishable from an empty directory — writing there would leave your mail in plaintext underneath the mount point, invisible to the vault forever. Fail closed.

**`--mail-account` means the opposite of what you expect on `ingest`.** Everywhere else in wicket it selects *which mailbox's data* you are working with. On `ingest` it selects *where the output goes* and says nothing about the input. Omitting it is the safe, routing behavior; passing it is the sharp edge. It cost 124 misfiled messages before the guard existed.

**`report --bucket` ignores `--mail-account`.** Not a bug: a bucket is a question about every store at once, so scoping it to one account would answer a question nobody asked.

**A dotted directory is not an account.** `known_accounts` skips anything beginning with `.`, because the mail tree is often a git repo and `.git` was briefly a candidate destination for your mail.

**`reconcile` refuses to move files, and that is the point.** It re-derives `domain` only for rows that are *not* downloaded; a downloaded row keeps whatever its committed `.eml` path says. So extending the fold map does not silently relocate archived mail — that requires an explicit migration. It looks like reconcile failing to do its job; it is protecting a reference something else may already hold.

**The manifest can contain messages the mailbox no longer has.** `deleted: true` with `downloaded: true` is the entire point of the archive: wicket holds the only copy. Do not "clean up" rows for messages Gmail has forgotten.

## §3. Live access

**Case: library with a network dependency.** wicket publishes no service. It *consumes* one — Google's IMAP endpoint — and the contract below is that upstream's, not wicket's.

### §3.1 Environments

| Env | Base URL | Region | Access | Credentials source |
|---|---|---|---|---|
| local | n/a — library | n/a | `python -m wicket <verb>` (or the `wicket <verb>` console script), or `from wicket.root.api import ...` | none needed for `report` or `ingest` |
| Gmail IMAP (upstream) | `imap.gmail.com:993` (implicit TLS) | Google-managed | stdlib `imaplib.IMAP4_SSL`, 60s timeout | `~/.config/wicket/accounts/<account>/imap.json` (`0600`) |
| Fastmail IMAP (upstream) | `imap.fastmail.com:993` (implicit TLS) | Fastmail-managed | stdlib `imaplib.IMAP4_SSL`, 60s timeout; must announce `OBJECTID` | `~/.config/wicket/accounts/<account>/imap.json`, a link into `~/.config/fastmail/<login>/` |

### §3.2 Auth

An **app password**, not OAuth: no browser flow, no consent screen, no cloud project, no refresh. Generated at `myaccount.google.com/apppasswords` (requires 2-Step Verification), it is a 16-character standing credential cached at `~/.config/wicket/accounts/<account>/imap.json` mode `0600`, never passed as a CLI argument and never logged.

```json
{ "email": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx" }
```

Lifetime: until revoked. Scope: that Google account's mail over IMAP. **Kill switch:** revoke it in one click at the same URL. The blast radius is bounded further by the hard invariant that `SELECT` is always read-only — the credential can only ever *read*.

### §3.3 Operations

The upstream operations wicket actually calls, all read-only:

- **`SELECT` (readonly=True)** — open a mailbox. `wicket.auth.open_mailbox` defaults it read-only for every caller, including worker threads. **Never flip this.**
- **`SEARCH` / `X-GM-RAW`** — find candidate messages. Gmail's raw search dialect is what `--query` passes through and what `--domains` compiles into.
- **`FETCH` (headers)** — `catalog`: envelope, `INTERNALDATE`, `X-GM-MSGID`, `X-GM-THRID`, `X-GM-LABELS`. Bodies are never fetched here.
- **`FETCH` (RFC822)** — `fetch`: the full body, exactly once per message, then written to disk.

There is **no send, no STORE, no flag, no expunge, and no move** anywhere in the codebase, and there must never be.

### §3.4 Rate limits

Google does not publish a documented IMAP request limit, but **caps simultaneous IMAP connections around 15**. wicket's pool is `DEFAULT_THREADS = 4` (one connection per worker, because `imaplib` is not thread-safe across commands), deliberately well under. Raising `--threads` toward the cap risks `[ALERT] Too many simultaneous connections`. There is no retry-with-backoff layer today: a failed run is simply re-run, and re-runs are idempotent, which is the whole mitigation.

### §3.5 Errors

| Code / message | Meaning | Recommended client action | Origin |
|---|---|---|---|
| `AuthError` | App password missing, rejected, or revoked | Re-seed `imap.json`, or regenerate the app password. Under `--non-interactive` this exits non-zero and loudly — intentional, for cron. | upstream |
| exit 2, `mail root ... does not exist` | The mail root is absent — most likely a locked encrypted vault | Unlock the vault, or set `$WICKET_MAIL_ROOT`. **Do not** create the directory to make the error go away. | library |
| exit 2, `unknown account` | The account has no store and is not in the alias map (probably a typo) | Fix the typo, add an alias, or `mkdir` the store deliberately. | library |
| exit 2, `account mismatch` | `--mail-account` disagrees with who the folder is addressed to | Re-run with the account it names, or pass `--force` if you meant it. | library |
| `[ALERT] Too many simultaneous connections` | Above Gmail's ~15-connection cap | Lower `--threads`. | upstream |

### §3.6 SDKs

None — neither this library nor the upstream publishes one for this use. wicket *is* the client, and it depends on nothing but `imaplib` from the standard library. Consumers depend on wicket by git tag:

```toml
dependencies = ["wicket @ git+https://github.com/vishalapte/wicket.git@main"]
```

## Confidence

**Least confident**

- §2.2 (Entities): two inventory kinds are **declared-only** and covered by no extractor — the domain entities and the library exports. Nothing in the tree enumerates the domain classes, and each verb declares its exports on one line that a line-anchored extractor cannot decompose, so both member lists are the author's claim rather than a checked set. The losslessness invariant does not cover them.
- §2.2 (Entities): every store-shaped entity now declares its `plane` and carries neither `path_pattern` nor `count`. An earlier revision of this brief published row and file counts read off the owner's own mail root; those are facts about the records, not about the program, and they are gone rather than updated (PRINCIPLES.md R-08).
- §3.4 (Rate limits): the "~15 simultaneous IMAP connections" cap is asserted in wicket's own code comments (`src/wicket/env.py`, near `DEFAULT_THREADS`), not from a Google document I read. The "no backoff layer" claim comes from reading the workers; verify with `grep -rn "retry\|backoff\|sleep" src/wicket/`.
- §2.9 (Design decisions): every decision dated 2026-07-13 was made in a single working session and is **uncommitted**. The rationale is faithful, but no ADR records it — the bucket/routing model in particular deserves one before it hardens. Verify against `git status` and the `## 0.3.0` block in `CHANGELOG.md`.
- §2.5 (Internal contracts): the `uchg` immutable-flag pin is specified in `docs/adr/ADR-MAIL.md` but **not implemented** on either side — wicket's prune guard does not exist, and wicket has no prune verb at all. Treat it as designed, not built.

**Not in this brief**

- Roadmap intent beyond the recorded phases (the provider seam, Fastmail, rewiring factotum and meridian) — *unknown — ask user*.
- Whether factotum and meridian have actually been rewired to depend on wicket, or still carry copies of the moved code — *unknown — ask user*.
- Any backup, retention, or disaster-recovery policy for the mail tree. There is currently no backup of any kind, and whether that is intentional beyond the "private, machine-only" decision is *unknown — ask user*.
- Bus factor, oncall, or operational ownership beyond "the owner runs it by hand" — *unknown — ask user*.
