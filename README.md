# wicket

Portable mail tooling. A small, stdlib-only Python package that reads a mailbox
over IMAP (app password, no OAuth) through two verbs over one year-sharded
manifest, plus a read-only `report`:

- **`wicket.catalog`**: observe the mailbox (headers only) into the manifest,
  recording what exists.
- **`wicket.fetch`**: download full `.eml` for matching threads, filed by
  sender domain, recording what is held.
- **`wicket.report`**: read-only summaries over the manifest (top senders, all
  addresses ever seen). No IMAP.

A message's state is two booleans on its manifest row, `deleted` and
`downloaded`. The verbs are **read-only on the mailbox by design**: they
`SELECT` it read-only and can never send, flag, label, or delete mail.

## Why this exists

The mail layer started inside `factotum` (a personal kitchen-sink repo) but is
general-purpose: more than one project wants to fetch and archive mail. wicket
is that layer extracted into a standalone library so its consumers (factotum,
meridian) depend on it rather than copy it.

## Providers

Today every tool speaks **Gmail** over IMAP (Gmail's `X-GM-*` extensions).
**Fastmail** support is planned behind a provider interface, selected per run
with `--source {gmail|fastmail}`. The two providers do not share a search
dialect; the portable query grammar that reconciles them is specified in
[`docs/adr/0001-portable-query-grammar.md`](docs/adr/0001-portable-query-grammar.md),
with the living key-by-key divergence table in
[`docs/QUERY-FIDELITY.md`](docs/QUERY-FIDELITY.md).

## Quickstart

```bash
python3 -m venv .venv
make install-dev          # editable install + dev tools + pre-commit hooks
python -m wicket        # list the verbs
python -m wicket.catalog --help
```

Full usage, setup, and the security model live in the package README:
[`src/wicket/README.md`](src/wicket/README.md).

## Use as a library

wicket is meant to be depended on, not copied (meridian, factotum). Install it
into the consumer's environment and read the manifest directly:

```bash
pip install -e /path/to/wicket      # editable, tracks changes
```

```python
from wicket import held_messages, manifest, resolve_archive_dir

# every downloaded .eml filed under an airline domain, with its manifest row
for row, eml in held_messages(domains={"delta.com", "united.com", "emirates.com"}):
    itinerary = parse_flight(eml.read_bytes())   # meridian's own parser

rows = manifest()                       # the whole year-sharded store, {id: row}
archive = resolve_archive_dir("you@gmail.com")
```

`held_messages` and `manifest` default the account to the sole one under
`~/mail`. The verbs stay CLIs (`python -m wicket.{catalog,fetch,report}`); the
library surface is the read side.

## Conventions

This repo follows the racecar standards. Agent-facing rules live in
[`CLAUDE.md`](CLAUDE.md) (repo-level) and
[`src/wicket/CLAUDE.md`](src/wicket/CLAUDE.md) (package-level). `make help`
lists every target; `make check` is the verification gate.
