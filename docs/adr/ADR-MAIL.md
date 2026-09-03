---
summary: Decision (shared with delphi): a single mail store in which a referenced message is immutable-locked rather than copied into a second store.
pnode: [../../README.md]
bearing: doctrine
---

# ADR-MAIL: Single-store mail — referenced messages are immutable-locked

- Status: Accepted; implemented in delphi (`delphi.mail.pin`).
- Date: 2026-07-15 (supersedes the 2026-07-12 two-store symlink/immutable-lock draft)
- Deciders: Vishal
- **Shared ADR.** This file is mirrored in both repos — `delphi/docs/adr/ADR-MAIL.md`
  and `wicket/docs/adr/ADR-MAIL.md`. It is the contract between them; edits must be
  applied to both copies. (This revision is applied to both copies, in sync as of
  2026-07-15.)

## Context

There is **one** mail store: `~/.delphi/mail`. delphi files tracked `.eml` and wicket
harvests, both into the same tree on one layout —
`<account>/archive/<counterparty-domain>/<YYYY-MM>/<sha1(message-key)[:16]>.eml`.
Because the filename is the content-derived hash stem, a tracked and a harvested copy
of one message land on the **same path**: there is nothing to dedup. (meridian is
flights/hotels; the email copies it makes are its own and are NOT filed here.)

The earlier draft of this ADR solved a *two-store* problem — delphi kept its own archive
at `~/.delphi/private/mail` holding relative symlinks into wicket's `~/.delphi/mail`,
with an immutable-lock pin and a manifest read-contract for dedup. That problem no
longer exists: one store, one layout, one physical copy by construction. The
symlink/second-archive machinery is retired.

What remains is the durability requirement that motivated the pin: **any message
referenced anywhere must not be deletable.**

## Decision

A message in `~/.delphi/mail` is undeletable **iff** something references it.
Enforcement is the macOS user-immutable flag (`chflags uchg`) set **on the file
itself** — no second store, no symlinks. `delphi.mail.pin` walks every `mail:`
reference in the corpus, resolves each to its file, and locks it.

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

1. **One physical `.eml` per message, by construction.** One store, content-hash
   filename — a tracked and a harvested copy coincide on the same path. No dedup step,
   no symlinks.
2. **The immutable flag is the pin, and it is self-enforcing.** `chflags uchg` on a
   referenced file makes deletion fail at the OS. Verified on APFS: `rm` of a `uchg`
   file returns "Operation not permitted."
3. **Durability is decoupled from reference churn.** `pin` only ever adds locks;
   releasing is a deliberate `--unpin`. A reference removed by mistake never silently
   unprotects its mail.

## Mechanics

**pin (`delphi.mail.pin`):**
1. Scan `DATA_ROOT/**/*.md` frontmatter for `mail:` msgids — the reference set.
2. `resolve` each (glob the store by the `sha1(message-key)[:16]` stem) → file or miss.
3. **Hit:** `chflags uchg` (idempotent). **Miss:** record as dangling.
4. `--check`: no writes; nonzero if any hit is unlocked or any reference is dangling.
   `--dry-run`: preview. `--unpin MSGID`: `chflags nouchg` the given id(s) — the only
   unlock path.

**resolve(msgid):** unchanged — normalizes the `Message-ID`, hashes to the 16-hex
stem, globs the one store. No symlink indirection.

**wicket side:** before deleting a candidate, prune must `lstat` it and **skip any file
with `UF_IMMUTABLE` set** (`st_flags & stat.UF_IMMUTABLE`). Without it the OS still
blocks deletion (`EPERM`), but prune errors per file; the guard makes it skip cleanly.
wicket writes `.eml` write-once, so a locked file never blocks a legitimate rewrite.

## Cryptomator / filesystem-capability caveat

Cryptomator serves an unlocked vault through a userspace backend (FUSE-T or WebDAV),
not APFS; BSD file flags are not implemented there, so `chflags uchg` errors or silently
no-ops (the pin does not take). In the single-store model there is no "owned copy"
fallback to substitute — instead the failure is **surfaced, not hidden**: `pin` re-reads
the flag and `pin --check` reports the file as UNLOCKED, so a vault that can't hold the
lock shows up as a gate failure rather than a false guarantee. On plain `~/.delphi`
(APFS) the lock is real; a vault substitutes encryption-at-rest for the OS lock.

## Edge cases

- **Missing / non-unique Message-ID:** an `.eml` with no `Message-ID` has no stable join
  key, so a `mail:` entry can only reference what `resolve` can find. Duplicate
  Message-IDs → first store hit wins.
- **Flag portability:** `uchg` is an APFS/HFS+ flag. Time Machine preserves it; `rsync`
  needs `-X`/`--fileflags`; naive copies drop it. Backups of `~/.delphi` should preserve
  flags (moot inside a vault).
- **Owner-clearable:** `uchg` (user-immutable), not `schg` (system-immutable,
  root-only), so delphi releases it without privilege.

## Cross-repo touchpoints

- **delphi** (`delphi.mail`): `pin` (walk refs → lock; `--check`/`--dry-run`/`--unpin`);
  `resolve` (Message-ID → path by hash stem); `ingest` (file tracked `.eml` into the
  shared store, same layout as the harvest).
- **wicket:** prune skips `UF_IMMUTABLE` files (one `lstat` guard).
- **No `import` either way.** delphi and wicket couple only through (a) the shared store
  and its layout and (b) the `uchg` flag convention.

## Supersedes

The 2026-07-12 draft (two stores + relative symlinks + a manifest read-contract for
dedup). It was never implemented; consolidating to one store at `~/.delphi/mail` removed
the duplication it was designed to prevent. The immutable-flag pin — the durable part —
is retained and now applies directly to the single stored file.

## Alternatives considered (still rejected)

- **Manifest pin bit** — delphi writes a "pinned" flag into wicket's manifest and
  wicket's prune reads it. Rejected: a shared read/write manifest contract plus a
  merge-on-re-harvest rule. The `uchg` flag gives the same durability with no write
  coupling.
- **Resolve with no lock** — references become fragile to wicket pruning / re-harvest.

## Consequences

- One physical copy per message by construction; no symlink layer.
- Durable references, OS-enforced, with no cross-repo code coupling.
- macOS-specific (`chflags`); inside a capability-limited vault the pin cannot take and
  surfaces as a `pin --check` failure rather than a silent gap.
- wicket keeps the one-line prune guard.
