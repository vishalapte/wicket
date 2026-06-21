# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
