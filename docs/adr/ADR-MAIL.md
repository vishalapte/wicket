---
summary: Decision (shared with delphi): a single mail store in which a referenced message is immutable-locked rather than copied into a second store.
pnode: [../../README.md]
bearing: doctrine
---

# ADR-MAIL: Single-store mail — referenced messages are immutable-locked

- Status: Accepted; implemented in delphi (`mail pin`), moved 2026-11-01 to the claude
  repo's `/enine-mail` tool, with delphi listing the references (`delphi refs`). Store
  moved to `$WICKET_DATA_DIR` 2026-11-03; delphi's private root renamed from
  `$DELPHI_ROOT` to `$DELPHI_DATA_DIR` 2026-11-08. Amended 2026-11-14: a message's file
  lives under `messages/` and never moves; a folder is a query over the manifest.
- Date: 2026-07-15 (supersedes the 2026-07-12 two-store symlink/immutable-lock draft)
- Deciders: Vishal
- **Shared ADR.** This file is mirrored in both repos — `delphi/docs/adr/ADR-MAIL.md`
  and `wicket/docs/adr/ADR-MAIL.md`. It is the contract between them; edits must be
  applied to both copies. (This revision is applied to both copies, in sync as of
  2026-11-14.)

> **Amended 2026-11-14 (`messages/`).** Filing moved files: every refile, dedup and
> delete rehomed `.eml` under `archive/<folder>/`, and each pass left a backup tree
> beside the store. A message's file now has one home that is fixed when it is filed
> and never changes, and a folder is a query over the manifest.
>
> - **Layout.** `<account>/messages/<YYYY>/<MM>/<stem>.eml`. `YYYY/MM` is the UTC
>   year and month of `MESSAGE_DATE`: the `Date:` header for mail received, the sent
>   date for mail sent (for mail the owner sent, that is its own `Date:` header). It is
>   not the manifest row's `date`, which is the provider's INTERNALDATE. Because
>   `MESSAGE_DATE` travels inside the message, every copy of a message in one account
>   lands on one path.
> - **RFC 5322 is canon for `MESSAGE_DATE`.** `Date:` is the origination date: required
>   exactly once (§3.6), the moment the sender marked the message "complete and ready to
>   enter the mail delivery system" (§3.6.1), in §3.3's format. A message whose `Date:`
>   is missing or unreadable does not conform; for it alone, `MESSAGE_DATE` falls back to
>   the received time, the topmost `Received:` header (a trace field, §3.6.7), else the
>   provider's INTERNALDATE. The fallback sets the month only; the stem never uses it.
>   An unreadable `Date:` contributes `""` to the stem, like a missing one. Measured 2026-11-14 over 236,903 filed messages: 53 need the fallback
>   (48 received, 5 sent); 27 received messages fall in a different month from
>   INTERNALDATE, and none sent.
> - **`<stem>` is derived from the message, not the provider.** It is
>   `sha1(message-id + "\n" + date + "\n" + from)[:16]`, where `message-id` is the
>   normalized `Message-ID` (ADR 0002 §1: brackets stripped, lower case), `date` is the
>   `Date:` header in UTC (`YYYY-MM-DDTHH:MM:SSZ`), and `from` is the `From:` address in
>   lower case; a missing header contributes an empty string. Two copies of one message
>   in one account, from any provider or filed by hand, get one stem, so the message is
>   stored once in that account. **Duplication exists only within an account.** The same
>   message in two accounts is not a duplicate: each account keeps its own copy, and
>   choosing a primary account is not wicket's job. Measured 2026-11-14 over the store's
>   236,923 files: 1,943 stems are shared by 3,910 files, 1,929 of them across accounts
>   (kept) and 14 within one account (stored once); all 78 files in `.duplicates` match a
>   live stem; 86 files lack at least one of the three headers. One stem joins two
>   different messages (a sender reused a `Message-ID` within the same second); a stem
>   that already exists is not filed again, so such a message is not stored.
>   **Filing reports every stem match whose `Subject` differs** rather than skipping it
>   silently. A different `Subject` usually means a copy rewritten in transit (a
>   catch-all or list tag; RFC 5322 §3.6.4 makes it the same message), and rarely a
>   reused `Message-ID`; the owner reads the report. Measured: 4 such matches, 3
>   rewritten copies and 1 reused `Message-ID`. The 14 within-account copies already
>   stored move to `<mail root>/.duplicates/<account>/` and are never deleted. This
>   replaces the per-provider stems (Gmail's message id, a digest of Fastmail's id).
> - **No folders.** A message has one file under `messages/` and no other path.
>   `archive/` is retired; a folder view is a query over the manifest's `from`, `to`,
>   `cc`, `bcc` and `attachments` (ADR 0002 §3). A refile moves nothing.
> - **The row stores `stem`, not `path`.** The stem never changes, and a row's file is
>   found by its stem under the account's `messages/`. `path` is dropped (ADR 0002).
> - **`resolve` finds the one file under `messages/`.**
> - **Locks are unchanged.** Only referenced mail is locked, and the lock goes on the
>   file under `messages/`.
> - **Deleting a message marks its row `excluded`, then removes its file.** The row is
>   never dropped. (No delete verb is built yet; this is the rule it will follow.) There
>   is no separate
>   deleted list; the manifest stays year-sharded. Marking first means an interrupted
>   delete leaves an `excluded` row with its file still present, which is harmless.
> - **`deleted` is observed; `excluded` is decided** (ADR 0002 §6). `deleted`: a complete
>   catalog found the message gone from the mailbox. `excluded`: the owner decided the
>   message is not kept in the local store; its value is the reason. Fetch never
>   downloads an `excluded` row and catalog carries the mark forward.
> - **A missing file is read against the manifest:**
>
>   | Manifest row | File | Means |
>   |---|---|---|
>   | present, `excluded` | missing | deleted on purpose |
>   | present, not `excluded` | missing | lost: a defect; report it and fetch it again |
>   | absent | missing | nothing to read |
>
>   A file removed by hand (`rm`) leaves its row unmarked, so it reads as lost and is
>   fetched again while the mailbox still holds it. A locked file cannot be deleted.
>
> §Context, §Decision, §Mechanics and §Consequences below are amended in place.

## Context

There is **one** mail store: `$WICKET_DATA_DIR` (default `~/.data/mail`). delphi files
tracked `.eml` and wicket harvests, both into the same tree on one layout —
`<account>/messages/<YYYY>/<MM>/<stem>.eml` (amended 2026-11-14). The aim is that a tracked and a harvested copy of one message
land on the **same path**, so there is nothing to dedup. (meridian is flights/hotels;
the email copies it makes are its own and are NOT filed here.)

The earlier draft of this ADR solved a *two-store* problem — delphi kept its own archive
at `<private root>/private/mail` holding relative symlinks into wicket's `<private root>/mail`,
with an immutable-lock pin and a manifest read-contract for dedup. That problem no
longer exists: one store, one layout, one physical copy by construction. The
second-archive machinery is retired.

What remains is the durability requirement that motivated the pin: **any message
referenced anywhere must not be deletable.**

## Decision

A message in `$WICKET_DATA_DIR` is undeletable **iff** something references it.
Enforcement is the macOS user-immutable flag (`chflags uchg`) set **on the file
itself** under `messages/` — no second store. `delphi refs` lists every `mail:`
reference in the corpus; `/enine-mail pin` resolves each to its file and locks it.

- **Referenced ⇒ locked.** `pin` sets `UF_IMMUTABLE` on every `.eml` a `mail:` entry
  (in `people/`, `events/`, any entity markdown under `DATA_ROOT`) resolves to.
  Idempotent.
- **Add-only.** Removing a reference does **not** auto-unlock. Unlock is explicit and
  separate: `pin --unpin MSGID` (`chflags nouchg`). Locked-once stays locked until a
  human clears it.
- **Dangling is a hard failure.** A `mail:` msgid that resolves to no file cannot be
  protected; `pin --check` exits nonzero on it (and on any referenced-but-unlocked
  file). `--check` writes nothing; it is the gate.
- **The flag is the whole cross-tool signal.** `chflags uchg` means any deleter —
  wicket's prune, a stray `rm` — gets `EPERM`. No manifest bit, no write-contract, no
  coordination.

### Principles

1. **One physical `.eml` per message, by construction.** One store, one file under
   `messages/` at a path that never changes. No dedup step, no second copy.
2. **The immutable flag is the pin, and it is self-enforcing.** `chflags uchg` on a
   referenced file makes deletion fail at the OS. Verified on APFS: `rm` of a `uchg`
   file returns "Operation not permitted."
3. **Durability is decoupled from reference churn.** `pin` only ever adds locks;
   releasing is a deliberate `--unpin`. A reference removed by mistake never silently
   unprotects its mail.

## Mechanics

**pin (`delphi refs` | `/enine-mail pin`):**
1. `delphi refs` scans `DATA_ROOT/**/*.md` frontmatter for `mail:` msgids — the reference set.
2. `resolve` each (manifest row → `stem` → file under `messages/`) → file or miss.
3. **Hit:** `chflags uchg` (idempotent). **Miss:** record as dangling.
4. `--check`: no writes; nonzero if any hit is unlocked or any reference is dangling.
   `--dry-run`: preview. `--unpin MSGID`: `chflags nouchg` the given id(s) — the only
   unlock path.

**resolve(msgid):** normalizes the `Message-ID`, finds its manifest row (the row `id`;
a reused one is `<key>|<provider>:<native-id>`), and returns the file named by the row's
`stem` under `messages/` (amended 2026-11-14).

**wicket side:** before deleting a candidate, prune must `lstat` it and **skip any file
with `UF_IMMUTABLE` set** (`st_flags & stat.UF_IMMUTABLE`). Without it the OS still
blocks deletion (`EPERM`), but prune errors per file; the guard makes it skip cleanly.
wicket writes `.eml` write-once, so a locked file never blocks a legitimate rewrite.
Prune deletes the file under `messages/`.

## Cryptomator / filesystem-capability caveat

Cryptomator serves an unlocked vault through a userspace backend (FUSE-T or WebDAV),
not APFS; BSD file flags are not implemented there, so `chflags uchg` errors or silently
no-ops (the pin does not take). In the single-store model there is no "owned copy"
fallback to substitute — instead the failure is **surfaced, not hidden**: `pin` re-reads
the flag and `pin --check` reports the file as UNLOCKED, so a vault that can't hold the
lock shows up as a gate failure rather than a false guarantee. On plain APFS (the store
at `$WICKET_DATA_DIR`) the lock is real; a vault substitutes encryption-at-rest for the
OS lock.

## Edge cases

- **Missing / non-unique Message-ID:** an `.eml` with no `Message-ID` has no stable join
  key, so a `mail:` entry can only reference what `resolve` can find. Duplicate
  Message-IDs → first store hit wins.
- **Flag portability:** `uchg` is an APFS/HFS+ flag. Time Machine preserves it; `rsync`
  needs `-X`/`--fileflags`; naive copies drop it. Backups of `$WICKET_DATA_DIR` should preserve
  flags (moot inside a vault).
- **Owner-clearable:** `uchg` (user-immutable), not `schg` (system-immutable,
  root-only), so delphi releases it without privilege.

## Cross-repo touchpoints

- **delphi** (`delphi refs`): walks the corpus and prints every referenced Message-ID.
- **claude** (`/enine-mail`, `~/.claude/skills/enine-mail/mail.py`): `pin` (the given
  Message-IDs → lock; `--check`/`--apply`/`--unpin`); `resolve` (Message-ID → path);
  `ingest` (file tracked `.eml` into the shared store, same layout as the harvest). The
  delphi SessionEnd hook pipes `delphi refs` into `mail.py pin --apply`, sending only
  the ids not pinned at an earlier quit (the list it last pinned is kept in
  `$XDG_STATE_HOME/delphi/pinned-refs.txt`); `mail.py pin --check` over the full
  `delphi refs` list is the audit. Until 2026-11-01 these were delphi's `delphi.mail`
  verbs.
- **wicket:** prune skips `UF_IMMUTABLE` files (one `lstat` guard).
- **No `import` either way.** delphi and wicket couple only through (a) the shared store
  and its layout and (b) the `uchg` flag convention.

## Supersedes

The 2026-07-12 draft (two stores + relative symlinks + a manifest read-contract for
dedup). It was never implemented; consolidating to one store (then `<private root>/mail`,
now `$WICKET_DATA_DIR`) removed
the duplication it was designed to prevent. The immutable-flag pin — the durable part —
is retained and now applies directly to the single stored file.

## Alternatives considered (still rejected)

- **Manifest pin bit** — delphi writes a "pinned" flag into wicket's manifest and
  wicket's prune reads it. Rejected: a shared read/write manifest contract plus a
  merge-on-re-harvest rule. The `uchg` flag gives the same durability with no write
  coupling.
- **Resolve with no lock** — references become fragile to wicket pruning / re-harvest.

## Consequences

- One physical copy per message by construction, at a path that never changes; a folder
  is a query over the manifest, so a refile moves no file (amended 2026-11-14).
- Durable references, OS-enforced, with no cross-repo code coupling.
- macOS-specific (`chflags`); inside a capability-limited vault the pin cannot take and
  surfaces as a `pin --check` failure rather than a silent gap.
- wicket keeps the one-line prune guard.
