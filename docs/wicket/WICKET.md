---
generator:
  name: racecar-llm-summary
  version: "0.8.0"
target:
  repo: wicket
  sha: "0000000"
  date: 2026-06-20
bundle:
  - WICKET.md

entities:
  - name: Row
    case: on_disk_managed
    lifecycle: realized
    purpose: One message's record in the manifest, carrying its identity, last-observed headers, download settlement, and derived domain.
    path_pattern: ~/mail/<account>/manifest/<YYYY>.jsonl   # one JSON Lines shard per UTC year, one row per message
    count: "one row per message, sharded by UTC year"
    validator: "wicket.manifest.read_shard / write_shard (byte-identical idempotent)"
    notes: >
      Fields are owned in three groups (message.py): identity (id, msgid, thread_id,
      account), observation set by catalog (date, from, to, subject, size, labels,
      deleted), settlement set by fetch (downloaded, path), derived by reconcile (domain).
      Keyed by `id` = normalized RFC Message-ID, else `gmail:<msgid>` fallback.
  - name: EmlArchiveFile
    case: content_tree
    lifecycle: realized
    purpose: The downloaded raw RFC822 message on disk, filed under its single external sender domain.
    path_pattern: ~/mail/<account>/archive/<domain>/<YYYY-MM>/<msgid>.eml
    count: "the downloaded subset of the manifest; some are retained after leaving the mailbox (gone-but-held)"
    validator: "wicket.reconcile.reconcile (verifies row.downloaded against disk)"
  - name: Domain
    case: none
    lifecycle: realized
    purpose: The canonical domain a thread files under; None only when it can't be resolved (an outbound message to several distinct domains), never stored as a status.
    notes: Derived from the thread's earliest message by direction (inbound = the sender's domain, outbound = the single external recipient's), alias-canonicalized; for a downloaded row it is the committed `.eml` path's first segment.
  - name: DomainAlias
    case: none
    lifecycle: realized
    purpose: A canonicalization rule folding a subdomain or alias domain to its parent (exact `alias -> parent` or wildcard `*.parent.com -> parent.com`) so mail files under one domain.
    notes: Loaded from an owner-authored domain-aliases.json via load_domain_aliases (domains.py), validated against the domain/wildcard grammar; a config mapping, not a persisted record.

relationships:
  - from: Row
    to: EmlArchiveFile
    cardinality: "1:1"
    owner_side: Row
    notes: A row with downloaded=true points to exactly one .eml via its `path`; reconcile re-derives the link from disk.
  - from: Domain
    to: Row
    cardinality: "1:N"
    owner_side: Domain
    notes: Many rows share one domain; the domain is derived per row, never stored as its own record.
  - from: DomainAlias
    to: Domain
    cardinality: "M:N"
    notes: Alias rules rewrite candidate domains during domain derivation; many aliases feed many domains.

external_surface:
  cli_verbs:
    - verb: python -m wicket
      module: wicket.__main__
      args: "(none)"
      behavior: Pure-discovery root; lists the three verbs and exits.
      exit: "0"
    - verb: wicket-catalog  (python -m wicket.catalog)
      module: wicket.catalog.__main__
      args: "--account --store-dir --state-dir --mailbox --years --threads --dry-run --non-interactive"
      behavior: Sweep mailbox headers (never bodies) into the year-sharded manifest; merges with settlement; a full sweep marks vanished messages deleted. Read-only on the mailbox.
      exit: "0"
    - verb: wicket-fetch  (python -m wicket.fetch)
      module: wicket.fetch.__main__
      args: "--account --domains --query --dest --alias-file --state-dir --threads --max --dry-run --non-interactive"
      behavior: Reconcile, then download full .eml for matching threads, filing each under its sender domain and recording settlement. Skips messages already on disk.
      exit: "0"
    - verb: wicket-report  (python -m wicket.report)
      module: wicket.report.__main__
      args: "--account [--senders | --addresses]  (default: summary)"
      behavior: Read-only reports over the manifest (top senders, all addresses, or a summary). No IMAP, no credentials; streams to stdout under default SIGPIPE.
      exit: "0"
  library_exports:
    - name: held_messages
      module: wicket.api
      signature: "held_messages(account: str | None = None, domains: set[str] | None = None) -> Iterator[tuple[Row, Path]]"
      behavior: Yield (row, eml_path) for every downloaded message, optionally restricted to a set of filing domains. Caller reads/parses the .eml.
    - name: manifest
      module: wicket.api
      signature: "manifest(account: str | None = None) -> dict[str, Row]"
      behavior: The whole manifest for an account, keyed by provider-neutral id.
    - name: resolve_archive_dir
      module: wicket.config
      signature: "resolve_archive_dir(account: str) -> Path"
      behavior: Path of an account's .eml archive root (~/mail/<account>/archive).
    - name: resolve_store_dir
      module: wicket.config
      signature: "resolve_store_dir(account: str) -> Path"
      behavior: Path of an account's manifest store (~/mail/<account>/manifest).
---

# wicket: Knowledge Package

Portable, read-only mail tooling. Version 0.1.0 (2026-06-20).
A recipient can drag this single file into their own LLM and interview it about
what wicket is, how the manifest and archive work, the security model, and how a
consumer (meridian, factotum) reads held mail as a library.

## §1. Map

### §1.1 Purpose

wicket reads a mailbox over IMAP and turns it into two durable local artifacts:
a **year-sharded manifest** (one JSON row per message, headers only) recording
what exists, and an **archive** of full `.eml` files filed by sender domain
recording what is held. It exists because more than one personal project
(factotum, meridian) needs to fetch and archive mail; wicket is that layer
extracted into a standalone, stdlib-only library so consumers depend on it
rather than copy it.

The audience is the author and a small set of sibling tools, not a multi-tenant
service. The user-facing primitives are three CLI verbs, `catalog` (observe),
`fetch` (download), `report` (summarize), over one store, plus a small
read-side library API (`held_messages`, `manifest`). A message's state is two
booleans on its row: `deleted` (gone from the mailbox) and `downloaded` (`.eml`
on disk). Every verb is **read-only on the mailbox by design**: it `SELECT`s the
mailbox read-only and can never send, flag, label, or delete mail. Gmail is the
only provider today; Fastmail is planned behind a provider interface.

### §1.2 Modules

| Module | Purpose |
| --- | --- |
| `wicket.catalog` | Verb: sweep mailbox headers into the manifest (`observe.py` worker). |
| `wicket.fetch` | Verb: download `.eml` for matching threads, filed by domain (`retrieve.py` worker). |
| `wicket.report` | Verb: read-only reports over the manifest (`summary.py` worker). No IMAP. |
| `wicket.reconcile` | Offline reconcile: verify `downloaded` against disk, derive `domain`; also home of the domain rule. |
| `wicket.manifest` | The year-sharded store primitives: shard read/write (atomic, 0600), `merge_catalog`, `merge_settlement`. |
| `wicket.message` | Provider-neutral key (normalized Message-ID + fallback) and the field-ownership tuples. |
| `wicket.auth` | IMAP login over a verifying TLS context using an app password; read-only `SELECT`. |
| `wicket.domains` | Domain validation and alias canonicalization (exact + wildcard). |
| `wicket.config` | Paths, env var, IMAP constants, account discovery, secret writing. |
| `wicket.api` | Public library surface (`held_messages`, `manifest`), re-exported from the package root. |
| `wicket.relocate` | Move committed `.eml` when a domain changes (the `--force` relocate path). |

### §1.3 Vendors

No paid SaaS and no cloud platform. The only external system is **Gmail's IMAP
endpoint** (`imap.gmail.com:993`), reached with the Python standard library
(`imaplib`, `ssl`) and a Gmail app password, no OAuth, no Google Cloud project,
no runtime third-party dependencies. The sibling local consumer is **meridian**,
which declares wicket as a git dependency (`git+https://github.com/vishalapte/wicket.git@v0.1.0`).

## §2. Implementation

### §2.1 Runtime

A single runtime: a **Python ≥3.12 CLI / library**, no service or daemon. Three
console scripts (`wicket-catalog`, `wicket-fetch`, `wicket-report`) plus the
`python -m wicket[.verb]` module form, and the importable `wicket` package for
library consumers. State lives entirely on the local filesystem:

| Entry point | Kind | State it touches |
| --- | --- | --- |
| `python -m wicket` | discovery root | none (lists verbs) |
| `wicket-catalog` | leaf CLI | reads IMAP; writes `~/mail/<account>/manifest/<YYYY>.jsonl` |
| `wicket-fetch` | leaf CLI | reads IMAP; writes manifest + `~/mail/<account>/archive/**` |
| `wicket-report` | leaf CLI | reads manifest only |
| `import wicket` | library | reads manifest + archive (`held_messages`, `manifest`) |

Credentials are read from `~/.config/gmail/imap.json` (0600). No process stays
resident; each invocation opens at most a handful of IMAP connections and exits.

### §2.2 Entities

Four shapes, none database-backed (see frontmatter `entities`). **Row** is the
on-disk manifest record, one JSON object per line in a per-UTC-year shard;
**EmlArchiveFile** is the content tree of downloaded messages under
`<domain>/<YYYY-MM>/<msgid>.eml`. **Domain** and **DomainAlias** are conceptual
primitives the system reasons about but never stores as their own records: a
domain is *derived* directionally (`None` only when a thread can't be resolved),
and an alias is a config rule. The manifest is deliberately a **regenerable cache**: `downloaded` is
re-derivable from disk and `domain` is re-derivable from observation, which is
what makes a single combined store safe (§2.11).

### §2.3 Relationships

```
DomainAlias ──(rewrites candidate domains)──► Domain ──1:N──► Row ──1:1──► EmlArchiveFile
                                                                (downloaded rows only)
```

A downloaded `Row` owns exactly one `EmlArchiveFile` via its `path`; many rows
share one derived `Domain`; alias rules feed domain derivation many-to-many. No
foreign keys exist, all links are by string (`id`, `path`, domain) and are
re-established offline by `reconcile`.

### §2.4 External surface

Three CLI verbs and four library exports (full list in frontmatter
`external_surface`). Load-bearing detail:

- **`wicket-catalog`** is the only writer that may set `deleted`. A full sweep
  (no `--years`) replaces every shard and marks rows absent from the mailbox as
  `deleted`; a `--years` sweep touches only those shards. `--dry-run` reports
  per-year counts and writes nothing. `--threads` opens one IMAP connection per
  year worker (default 4; stay well under Gmail's ~15 connection cap).
- **`wicket-fetch`** runs `reconcile` first, plans the pending downloads for the
  requested domains, fetches `RFC822`, writes each `.eml`, then records
  settlement. Re-running skips anything already on disk (idempotent).
- **`held_messages(domains=…)`** is the primary consumer entry: it yields
  `(row, eml_path)` for downloaded messages, optionally filtered to a set of
  sender domains, e.g. meridian passes the airline domains.

### §2.5 Internal contracts

- **Manifest Row JSON**: the wire shape between verbs. Producer: `catalog.observe`
  (identity + observation), `fetch.retrieve` (settlement), `reconcile` (derived
  `domain`). Consumers: `report.summary`, `api`. Field ownership is fixed in
  `message.py` tuples (`IDENTITY_/OBSERVATION_/SETTLEMENT_/DERIVED_FIELDS`).
- **`.eml` path convention** `<domain>/<YYYY-MM>/<msgid>.eml`: producer
  `fetch._plan`; consumers `reconcile` (re-derives domain from the path's first
  segment) and `api.held_messages` (joins archive dir + path).
- **Provider-neutral key** (`message.message_key`), the normalized RFC
  Message-ID, else `"<provider>:<native_id>"`. This is the manifest dict key and
  the cross-store/cross-provider join; it replaces Gmail's `X-GM-MSGID`.

### §2.6 Configuration

- `WICKET_ACCOUNT`: selects the account scoping the store; if unset, verbs and
  the library default to the sole account directory under `~/mail`.
- `~/.config/gmail/imap.json` (0600, dir 0700), IMAP credentials,
  `{"email": …, "app_password": …}`. Never committed, never logged, never a CLI arg.
- `~/mail/<account>/manifest/` and `~/mail/<account>/archive/`: the two store roots.
- `IMAP_HOST = imap.gmail.com`, `IMAP_PORT = 993`, `ALL_MAIL_MAILBOX = "[Gmail]/All Mail"`,
  `IMAP_TIMEOUT_SECONDS = 60` (config.py).

### §2.7 Flows

1. **catalog (observe).** Login → `SELECT` All Mail read-only → per requested
   year `SEARCH` by `INTERNALDATE` → `FETCH` headers only → `merge_catalog` into
   each shard → atomic write. Idempotent: re-running yields a byte-identical
   shard. A complete sweep additionally marks vanished messages `deleted`;
   boundary-overlap messages are deduped.
2. **fetch (settle).** `reconcile` → plan pending downloads via a domain query →
   `FETCH RFC822` → write `<domain>/<YYYY-MM>/<msgid>.eml` → `merge_settlement`
   (sets `downloaded`, `path`). Threads it can't resolve (`domain is None`) are
   skipped and re-evaluated next run. Failure mid-run leaves the manifest
   consistent; a re-run resumes from disk.
3. **reconcile (offline).** For every shard, set `downloaded` to whatever disk
   says and derive `domain` from the earliest observed message (or, for a
   downloaded row, from the committed path). Deterministic and idempotent; no IMAP.
4. **report (offline).** Stream top senders (`--senders`), all addresses
   (`--addresses`), or a summary from the manifest. No credentials.
5. **library read.** `held_messages` / `manifest` load the store and (for held
   mail) join the archive dir; the consumer parses the `.eml` itself.

### §2.8 Seams

- **Provider interface (planned).** A `--source {gmail|fastmail}` selector behind
  which Fastmail support will sit; today only Gmail's IMAP/`X-GM-*` dialect is
  implemented. The portable query grammar that will reconcile the two dialects is
  specified in `docs/adr/0001-portable-query-grammar.md` with the divergence
  table in `docs/QUERY-FIDELITY.md`.
- **Layered import contract.** `pyproject.toml [tool.importlinter]` pins the
  acyclic layering (verbs → workers → reconcile → shared → config); architectural
  change must update it in the same commit (`lint-imports` → "1 kept, 0 broken").
- **Library API.** `wicket.api` is the stable read-side seam new consumers plug
  into; recent example: meridian's `flights.email.held.held_airline_emls`.

### §2.9 Design decisions

- **One store, one key, two verbs** (`docs/adr/0002-unified-message-manifest.md`,
  Accepted). Replaced three separate artifact files with a single year-sharded
  manifest keyed by a provider-neutral id.
- **Directional filing domain** (`docs/adr/0002-unified-message-manifest.md` §3,
  Accepted). Each thread files under its counterparty's domain: the sender for
  inbound mail, the single external recipient for outbound, keyed off the
  thread's earliest message.
- **Two boolean axes, not a status enum.** `deleted` and `downloaded` are
  independent booleans; the four meaningful combinations are spoken plainly
  (downloaded, downloaded-and-deleted, deleted-not-downloaded, not-downloaded)
  rather than named with a coined bucket term.
- **`domain is None` is the unresolvable case** (an outbound message you sent to
  several distinct domains): not an error and not a stored state. Derived, never
  cached.
- **Bare addr-spec id, UTC dates, `msgid` not `gmail_msgid`.** Identity stored
  without `<>` brackets; all manifest dates in UTC; key names provider-neutral.
- **Portable query grammar** across Gmail and Fastmail
  (`docs/adr/0001-portable-query-grammar.md`, Accepted), keeping a living
  key-by-key divergence table where the same key means different things.
- **Read-only `SELECT` always.** `auth.open_mailbox` defaults `readonly=True`;
  the tool never acquires write/flag/delete capability.
- **Security fixes at extraction.** Domain derivation rejects non-domain header
  values before they can become a path segment (path-traversal guard,
  `reconcile.compute_domain`); IMAP uses a verifying TLS context
  (`ssl.create_default_context()`).

### §2.10 Operational

- **Install (dev):** `python3 -m venv .venv && make install-dev` (editable
  install + PEP 735 dev group + pre-commit). Runtime has **zero third-party
  dependencies**; consumers install via `pip install -e .` or the git URL.
- **Credentials seed:** first interactive run prompts for the Gmail address and
  app password and writes `~/.config/gmail/imap.json` (0600). `--non-interactive`
  (cron/launchd) fails loudly instead of prompting.
- **Verification gate:** `make check` (fmt-check, lint, typecheck, test, arch);
  `scripts/check_docs.py` for doc coherence. mypy is `--strict`; the package
  ships `py.typed` so consumers type-check against it.
- **No scheduled jobs, healthchecks, or observability hooks** in-repo; scheduling
  is the consumer's concern (factotum ships launchd glue).

### §2.11 Weirdness

- **The manifest is a regenerable cache.** It looks like a database of record but
  isn't: `downloaded` is re-derivable from disk and `domain` from observation, so
  losing it costs only a re-sweep. This is precisely why a single combined store
  is safe rather than risky.
- **"Gone but held" rows** carry `deleted=true` *and* `downloaded=true`: the
  message left the mailbox but its `.eml` is retained. Looks contradictory; it is
  the archival point of the tool.
- **`domain is None` is normal output**, not a failure: it marks a thread fetch
  can't auto-file (outbound to several distinct domains), and it skips it.
- **`id` may be `gmail:<msgid>`** when a message has no usable RFC `Message-ID`;
  the fallback key looks provider-specific but guarantees every message a stable key.
- **`report` and the library auto-detect the account** when exactly one exists
  under `~/mail`, so commands run with no `--account`; with two accounts they
  refuse rather than guess.

## §3. Live access

Case: **library with a network dependency.** wicket publishes no service of its
own; its only network surface is the **Gmail IMAP** server it reads. The
contract below is the upstream's, plus the local invocation entry point.

### §3.1 Environments

| Env | Base URL | Region | Access | Credentials source |
| --- | --- | --- | --- | --- |
| local | n/a, library | local | `python -m wicket.catalog` / `wicket-fetch` | `~/.config/gmail/imap.json` |
| upstream (Gmail) | `imap.gmail.com:993` (IMAP over TLS) | Google global | IMAP `LOGIN` | Gmail app password (2-Step Verification required) |

### §3.2 Auth

Gmail app-password auth over an implicit-TLS IMAP connection. The connection
uses a verifying TLS context (`ssl.create_default_context()`: certificate chain
+ hostname checked). After `LOGIN`, the mailbox is opened with `SELECT …
readonly`. The 16-character app password is generated once at
`myaccount.google.com/apppasswords` and stored locally; it does not expire on a
schedule but is revoked when the Google password changes or the app password is
removed. Redacted credential file:

```json
{ "email": "you@example.com", "app_password": "xxxx xxxx xxxx xxxx" }
```

### §3.3 Operations

The IMAP verbs wicket issues (no other endpoints):

- `LOGIN <email> <app_password>`: authenticate.
- `SELECT "[Gmail]/All Mail" (readonly)`: open the mailbox without write access.
- `SEARCH`: by `INTERNALDATE` year (catalog) or by a `{from:… to:…}` domain
  expression (fetch).
- `FETCH`: headers only for catalog (observation); `RFC822` for fetch (the
  `.eml` body written to the archive).
- `LOGOUT`: close.

### §3.4 Rate limits

Gmail caps **simultaneous IMAP connections** at roughly 15 per account; wicket
opens one connection per year worker and defaults `--threads 4` to stay well
under it. There is no request-rate idempotency key; safety comes from the
operations being read-only and the local merge being idempotent.

### §3.5 Errors

| Condition | Meaning | Client action | Origin |
| --- | --- | --- | --- |
| `AuthError: IMAP login rejected` | Bad/revoked app password or changed Google password | Regenerate the app password; update `imap.json` | upstream |
| `AuthError: could not reach imap.gmail.com` | Network/TLS failure opening the connection | Retry; check connectivity / TLS | upstream |
| `AuthError: could not select mailbox` | `SELECT` returned non-OK (mailbox name/permission) | Verify the `--mailbox` name | upstream |
| `AuthError: missing 'email' or 'app_password'` | Credential file malformed | Fix `~/.config/gmail/imap.json` shape | library |
| `ValueError: no account` | No/ambiguous account under `~/mail` | Pass `--account` / `WICKET_ACCOUNT` | library |

### §3.6 SDKs

`none, neither this library nor the upstream publishes one for this use`. wicket
speaks IMAP directly through the Python standard library (`imaplib`); there is no
wrapper SDK to point at.

## Confidence

**Least confident**

- §2.7 (Flows): the boundary-overlap dedup (a message whose INTERNALDATE straddles
  a year-shard boundary) is noted but its exact tie-break was not traced this pass.
  Verify in `catalog/observe.py`.
- §2.9 / §2.5 (`--attachments`, `--force`): ADR 0002 specifies an `--attachments`
  enrichment flag and a `--force` rebuild, but whether both are wired in the
  current CLI was not verified this pass. Verify with `python -m wicket.fetch --help`.
- §2.8 (Seams) / §1.2: `wicket.relocate` is summarized as the `--force` domain-change
  path from its role, not a close read this pass. Verify against `src/wicket/relocate.py`.

**Not in this brief**

- Fastmail provider implementation, documented as planned (ADR 0001) but not
  built; `unknown (ask user)` for timeline.
- Pricing, users, revenue, roadmap beyond the two ADRs: `unknown (ask user)`
  (this is a personal tool with no commercial surface).
