# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
