# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

## [0.1.0]

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
