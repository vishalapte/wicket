# ADR 0001: A portable query grammar across Gmail and Fastmail

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Vishal (owner)
- **Scope:** `wicket`: the `--query` the `--source {gmail|fastmail}` verbs take.

## Context

`wicket`'s tools take a `--query` that selects which messages to act on.
The two backends speak different search dialects, and they are not equally
capable. We had to decide what one portable query string means, and what
happens where the two providers disagree.

There are really four search surfaces, not two:

| Surface | What it is | Used by |
|---|---|---|
| **Gmail search** (`X-GM-RAW`) | Gmail's full consumer search language | gmail backend |
| **IMAP `SEARCH`** (RFC 3501) | Standard IMAP key set | fastmail backend (now) |
| **JMAP `FilterCondition`** | Structured typed filter object | fastmail backend (future) |
| Fastmail web operators | UI-only sugar | not used |

### Which dialect is actually better

"Better" splits into two axes that point in opposite directions.

**Breadth: how much you can express.** Gmail wins decisively. It has
`from/to/cc/bcc/subject`, full-text relevance, `has:attachment`,
`filename:`, relative dates (`newer_than:2d`), `larger/smaller`, `label:`,
`category:`, `is:`, `in:`, proximity (`AROUND`), `list:`, `rfc822msgid:`,
and boolean grouping. JMAP sits in the middle (native `hasAttachment`,
header/body/text, `before/after` with real RFC3339 timestamps, `inMailbox`,
`hasKeyword`) but lacks proximity, categories, and relevance. IMAP `SEARCH`
is the floor: a fixed key set, **no attachment predicate, no filename, no
relative dates, day-granular only.**

**Precision: whether you can predict what a query matches.** The order
inverts. JMAP is the most legible (typed conditions, unambiguous timestamp
boundaries). IMAP `SEARCH` is crude but unambiguous (raw substring, whole-day
boundaries). Gmail is the *least* predictable: token matching, contact-name
expansion, relevance ranking, and an opaque internal "date" mean
`from:acme` and `after:2024/01/01` do not always match what the literal text
implies.

So Gmail has the richest grammar and the least trustworthy one. By a
deterministic-over-heuristic standard, that fuzziness is a defect, not a
feature.

## Decision

Provide **one portable query grammar**, parsed into a small AST and compiled
per backend. Two rules resolve the tension above:

1. **Borrow surface area from Gmail.** The portable key set is modeled on
   Gmail's operators, because Gmail is the superset of things worth
   expressing. Portable keys:
   `from to cc bcc subject body after before newer-than older-than`
   `sent-after sent-before larger smaller has:attachment filename`
   `is:{unread,read,starred,flagged} label folder in` plus `OR`, `-`/`NOT`,
   `( )`.

2. **Pin semantics to the deterministic (IMAP/JMAP) meaning** wherever the
   two providers disagree. Substring, not token. INTERNALDATE (received),
   not Gmail's fuzzy date. `label` and `folder` are distinct axes, never
   silently cross-mapped. This buys Gmail's expressiveness with Fastmail's
   legibility.

3. **A raw escape hatch** carries anything the portable grammar cannot
   express: `raw("…")` injects provider-native text untouched and is bound
   to a single `--source`; running it against the other provider is an error,
   not a silent no-op.

The collision table is maintained mechanically in `docs/QUERY-FIDELITY.md`
(the living spec, drift-checked by `scripts/check_query_fidelity.py`); this
ADR is the dated record of *why*.

## Examples

### A clean, everyday query

Portable:

```
from:billing@acme.com has:attachment newer-than:30d -subject:reminder
```

Compiles to (run on 2026-06-20):

```text
gmail   :  from:billing@acme.com has:attachment newer_than:30d -subject:reminder
fastmail:  FROM "billing@acme.com" HEADER Content-Type "multipart/mixed" \
           SINCE 21-May-2026 NOT SUBJECT "reminder"
```

Note what already differs under the hood: `has:attachment` is exact on Gmail
and a `multipart/mixed` **heuristic** on Fastmail-IMAP; `newer-than:30d` is
resolved to a concrete day-granular `SINCE` boundary.

### A date query (collision #1: received vs sent)

Portable `after:2024-01-01` →

```text
gmail   :  after:2024/01/01
fastmail:  SINCE 01-Jan-2024          # INTERNALDATE = received
```

`after` is pinned to the *received* axis on both sides. To search the
sender's claimed send time instead, use the separate `sent-after:` key, which
compiles to IMAP `SENTSINCE`. The compiler warns when `after`/`before` is used
against `--source fastmail`, because boundary inclusivity and day granularity
can shift a match by one day relative to Gmail.

### An address query (collision #2: address vs substring)

Portable `from:acme.com` →

```text
gmail   :  from:acme.com                 # parsed address + contact/name expansion
fastmail:  FROM "acme.com"               # raw substring over the From header
```

Same key, different match model. On Fastmail this also matches `acme.com`
sitting inside a display name and does no contact expansion.

### A tag query (collision #5: tag axis vs location axis)

Portable `label:Receipts` →

```text
gmail   :  label:Receipts                # Gmail label (a tag)
fastmail:  ERROR, label: is Gmail-only; use folder:Receipts for a Fastmail mailbox
```

Labels (many-per-message tags) and folders (one-per-message locations) are
different data models. We refuse to cross-map them.

### A provider-only operator (the raw escape)

Portable `raw("category:promotions")` →

```text
gmail   :  category:promotions
fastmail:  ERROR, raw() clause is bound to --source gmail
```

## Consequences

**Good.**
- One query string works across both providers for the common case.
- Where they cannot agree, the divergence is named at compile time, not
  discovered as a silent wrong-result.
- Gmail's power operators remain reachable via `raw(...)` without polluting
  the portable grammar.
- Pinning to deterministic semantics means a portable query's meaning is
  inspectable and stable, which is the property we actually trust.

**Costs.**
- We maintain a query parser plus two `compile_query` implementations, and a
  third (JMAP) later. The drift-check script makes the cost mechanical, not
  vigilance-based.
- The portable grammar is deliberately *narrower* than Gmail's. Some Gmail
  reach (`AROUND`, `category:`, relevance ranking) is only available raw and
  only on Gmail.
- `has:attachment` is lossy on Fastmail-IMAP until the JMAP backend lands;
  output marks it as approximate so a purge decision is never made on a
  heuristic match alone.

## Alternatives considered

- **Provider-native passthrough** (`--query` is a raw Gmail string for gmail,
  raw IMAP for fastmail). Simplest, ships fastest. Rejected: not portable, the
  same flag means two unrelated things, and the owner wants one grammar.
- **Lowest common denominator** (only keys both providers support natively).
  Rejected: drops Gmail's reach entirely and still hits the date/address
  semantic collisions, so it is lossy *and* weak.
- **Structured JSON filter** (a JMAP-style typed object as the portable
  surface). Most precise, but verbose and unreadable on a CLI; a query string
  is what a human types. Rejected for the user-facing surface; the AST it
  parses into is effectively this, kept internal.
