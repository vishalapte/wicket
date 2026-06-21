# Query fidelity: portable keys → Gmail / Fastmail

The living, key-by-key spec for wicket's portable query grammar. The decision
and rationale are in [`adr/0001-portable-query-grammar.md`](adr/0001-portable-query-grammar.md);
this file is the table the compiler implements and that
`scripts/check_query_fidelity.py` will drift-check (every portable key must have
a row here *and* a compile branch in both backends).

Status: **spec only**: no compiler exists yet; Gmail is queried with a raw
`--query` string, and the portable layer lands with the Fastmail backend.

## Portable key set

```
from to cc bcc subject body
after before newer-than older-than sent-after sent-before
larger smaller has:attachment filename
is:{unread,read,starred,flagged} label folder in
OR  -/NOT  ( )   raw("<provider-native>")
```

Semantics rule: **borrow surface area from Gmail, pin meaning to the
deterministic (IMAP/JMAP) side wherever the two disagree.**

## Clean keys (same meaning on both)

| Portable | Gmail (`X-GM-RAW`) | Fastmail (IMAP `SEARCH`) |
|---|---|---|
| `cc:x` / `bcc:x` | `cc:x` / `bcc:x` | `CC "x"` / `BCC "x"` |
| `body:x` | `"x"` | `BODY "x"` |
| `larger:5M` / `smaller:N` | `larger:5M` | `LARGER 5242880` / `SMALLER N` |
| `is:unread` / `is:read` | `is:unread` / `is:read` | `UNSEEN` / `SEEN` |
| `is:starred` / `is:flagged` | `is:starred` | `FLAGGED` |
| `sent-after:D` / `sent-before:D` | `after:D` / `before:D` | `SENTSINCE D` / `SENTBEFORE D` |
| `OR` / `-` / `( )` | infix `OR`, `-x`, `(…)` | `OR a b`, `NOT a`, juxtaposition = AND |

## Collisions (same key, DIFFERENT meaning): must warn at compile time

| # | Portable | Gmail meaning | Fastmail (IMAP) meaning | Resolution |
|---|---|---|---|---|
| 1 | `after:` / `before:` / `newer-than:` / `older-than:` | received-date, fuzzy | `SINCE`/`BEFORE` on INTERNALDATE; day-granular; boundary off-by-one risk | pin to INTERNALDATE (received); expose `sent-*` for the Date-header axis; warn on fastmail |
| 2 | `from:` / `to:` | parsed address + display-name + contact expansion | `FROM`/`TO` raw case-insensitive **substring** over the header | pin to substring; warn that contact/name expansion is gmail-only |
| 3 | `subject:` | word/token match | `SUBJECT` literal substring | pin to substring |
| 4 | `has:attachment` | exact (real MIME attachment, excludes inline) | no native predicate; heuristic `HEADER Content-Type "multipart/mixed"` | mark lossy on fastmail-IMAP; exact again under future JMAP `hasAttachment` |
| 5 | `label:` vs `folder:` | label = many-per-message tag, searched in place | folder = one-per-message location, = mailbox `SELECT` | do **not** cross-map; `label:` gmail-only, `folder:` fastmail-only; using the wrong one is a hard error |
| 6 | `in:` | search scope (`anywhere`/`spam`/`trash`) | mailbox selection | same divergence as #5; resolve by mailbox, warn |

## No port (provider-only; reachable only via `raw(...)`)

- **Gmail-only:** `category:`, `is:important`, `AROUND`, `list:`, `deliveredto:`,
  `rfc822msgid:`, `filename:`.
- **Fastmail/IMAP-only:** `KEYWORD`, `TEXT`, arbitrary `HEADER <field> <value>`.

A `raw("…")` clause injects provider-native text untouched and is bound to a
single `--source`; running it against the other provider is a hard error, never
a silent no-op.

## Worked example

Portable: `from:billing@acme.com has:attachment newer-than:30d -subject:reminder`
(compiled on 2026-06-20):

```text
gmail   :  from:billing@acme.com has:attachment newer_than:30d -subject:reminder
fastmail:  FROM "billing@acme.com" HEADER Content-Type "multipart/mixed" \
           SINCE 21-May-2026 NOT SUBJECT "reminder"
```

Differences in flight: `from:` is address-aware on Gmail and a raw substring on
Fastmail (#2); `has:attachment` is exact on Gmail and a heuristic on Fastmail
(#4); `newer-than:30d` resolves to a day-granular `SINCE` boundary (#1).
