# ADR 0002: The message manifest: one store, one key, two verbs

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Vishal (owner)
- **Scope:** `wicket`: the on-disk record of the mailbox and the verbs over it.

## Context

wicket records two things about a mailbox, keyed per message: what **exists**
(observation) and what has been **retrieved** to disk (settlement). They share a
key and join naturally.

The manifest is a **regenerable cache**: `downloaded` is ground-truthed by the
`.eml` on disk, and `domain` is a deterministic function of each thread's earliest
message (the sender's domain inbound, the single recipient's outbound), `null`
when unresolvable. Anything in the manifest can be rebuilt from mailbox + disk.
That is what makes a single store safe.

## Decision

### 1. A provider-neutral key

The key is the normalized RFC `Message-ID`: its bare addr-spec (the same grammar
as an email address; angle brackets stripped, lowercased), present on essentially
all mail and portable across providers. When a message has
no usable `Message-ID`, the key is `"<provider>:<native-id>"` (e.g.
`gmail:18f2a3`) so every message has a stable, unique key. Provider-native ids
(`msgid`, `thread_id`) are retained as fields for fast refetch.

### 2. One store, account-scoped, year-sharded

```
~/mail/<account>/manifest/YYYY.jsonl
```

Sibling to the `.eml` archive under one mail root: `~/mail/<account>/archive/`.
One JSON Lines row per message, keyed by `id`, sharded by INTERNALDATE-UTC year.
Year-sharding keeps writes cheap at six-figure scale: a write touches only the
affected year(s).

### 3. A row is progressively enriched, not all-or-none

| Field group | Written by | Present when |
|---|---|---|
| `id`, `msgid`, `thread_id`, `account` | identity | always |
| `date`, `from`, `to`, `subject`, `size`, `labels`, `deleted` | `catalog` (observation) | observed |
| `attachments` (filenames/types/sizes/count) | `--attachments` | enriched |
| `downloaded`, `path` | `fetch` (settlement) | fetched |
| `domain` | reconcile (derived) | derived |

`deleted` and `downloaded` are the two boolean axes (see §6). `domain` is the
canonical domain a thread files under, derived from its **earliest message by
direction**: for inbound mail (the `From` address is not the mailbox owner) it is
the **sender's** domain, with `To` ignored; for outbound mail (`From` is the
owner) it is the **single external recipient's** domain, or `null` when you wrote
to several distinct domains. The owner identity is the `imap.json` login email;
each candidate is alias-canonicalized and must be a well-formed domain (it becomes
a path segment) or the result is `null`.

**All timestamps in a row are stored UTC** (ISO-8601, `+00:00` offset); a
message's shard is the UTC year of its `date`. The mailbox's INTERNALDATE is
normalized to UTC on the way in, so the same mailbox always lands in the same
shard regardless of where it is read.

A shard rewrite refreshes observation and preserves settlement already on the row
(merge, not clobber): same mailbox + same settlement → byte-identical shard.

### 4. Two verbs, over a shared reconcile

- **`catalog`**: observe the mailbox into the manifest's observation fields.
  Default incremental (new messages since the last run).
- **`fetch`**: retrieve the `.eml` for matching threads, write settlement, and
  file each `.eml` under its alias-canonical domain. The only verb that pulls
  bodies. Two verbs suffice: a downloaded `.eml` already contains its attachments, so
  retrieval needs no separate file-downloader, and `--attachments` records their
  metadata.

Every verb first runs the **reconcile**: load shards, reconcile `downloaded`
against the dest tree, recompute `domain`. The reconcile is offline, deterministic, and
idempotent, it cannot make the store worse, so it is always safe before a
mutation. wicket only ever reads the mailbox (`SELECT` is read-only).

### 5. `--force` and `--attachments` are verb-independent

Each flag means the same thing on either verb; `fetch --attachments` ≡
`catalog --attachments`.

- **`--attachments`**: also FETCH `BODYSTRUCTURE` and record an attachment
  summary (filenames, MIME types, sizes, count). `BODYSTRUCTURE` returns the MIME
  tree without downloading bodies, one extra round-trip per message. *More
  fields.*
- **`--force`**: distrust the cache and rebuild every derivable field from
  ground truth: re-observe fully, re-verify each `downloaded` flag against the dest
  `.eml`, recompute the alias-canonical `domain` and relocate each `.eml`
  to match (a byte-identical clash drops the duplicate; a differing clash aborts),
  re-resolve any `id`. *More work.* This rebuilds the store from mailbox + disk;
  corruption is a `--force` rebuild. `catalog --force` performs the relocation
  without downloading bodies.

### 6. Reading a message's state

A message's state is the two booleans, spoken plainly. No bucket label is derived
or stored, the booleans are the state.

- **`downloaded`**: the `.eml` is on disk. Always knowable (check the dest
  tree); never ambiguous.
- **`deleted`**: a *complete* catalog confirmed the message is gone from the
  mailbox. Only a complete catalog sets it; an incremental or `--years` pass
  cannot, so the absence of `deleted` means present-or-unconfirmed, never
  asserted-gone.

The four readings:

- **not-downloaded**: in the mailbox, no local copy; fetch it. If `domain` is
  `null` the thread couldn't be resolved (an outbound message to several distinct
  domains), so it can't be auto-filed; that case *is* `domain is None`, nothing more.
- **downloaded**: held locally and still in the mailbox.
- **downloaded-and-deleted**: held locally, gone from the mailbox: the local
  `.eml` is the only copy.
- **deleted-not-downloaded**: gone from the mailbox, never kept. This is not
  stored: a complete catalog drops such a row rather than keeping a tombstone,
  so the state is the *absence* of a row, not a row that says so.

## Examples

Three rows in `2024.jsonl`, at different enrichment levels:

```json
{"id":"cae9f2@mail.acme.com","msgid":"18f2","thread_id":"1","date":"2024-03-22T14:53:21+00:00","from":"billing@acme.com","to":["you@gmail.com"],"subject":"Invoice","size":84213,"labels":["Receipts"],"attachments":[{"name":"inv.pdf","type":"application/pdf","size":80112}],"deleted":false,"downloaded":true,"domain":"acme.com","path":"acme.com/2024-03/18f2.eml"}
{"id":"19a0bd@mail.globex.com","msgid":"19a0","thread_id":"2","date":"2024-07-01T09:15:00+00:00","from":"news@globex.com","to":["you@gmail.com"],"size":4112,"labels":["Promotions"],"deleted":false,"downloaded":false,"domain":"globex.com"}
{"id":"1b3c0a@mail.emirates.com","msgid":"1b3c","thread_id":"3","deleted":false,"downloaded":true,"domain":"emirates.com","path":"emirates.com/2024-09/1b3c.eml"}
```

Row 1 is fully enriched; row 2 is observed but unfetched; row 3 is fetched but
not yet observed. All valid.

## Consequences

- One inspectable, year-sharded file family, queryable by downstream consumers
  (meridian) and by a human.
- Regenerable via `--force`, so the single store is safe.
- Cheap incremental writes; authoritative refresh (`--force`) and enrichment
  (`--attachments`) on demand, independently, on either verb.
- `catalog` shard rewrites merge-preserve settlement, so observation is a
  function of mailbox + prior settlement, not the mailbox alone.
- Every verb pays one reconcile (offline, per-shard) before acting.

## Alternatives considered

- **Two stores** (observation and settlement in separate files). The manifest is
  a regenerable cache, so isolating settlement buys nothing that `--force` does
  not, at the cost of a second store and a join.
- **One monolithic `manifest.json`.** Rewrites the whole blob on every write at
  six-figure scale; year-sharding is required.
- **A keyed-by-`X-GM-MSGID` manifest.** Gmail-only; the normalized `Message-ID`
  is portable to Fastmail.
