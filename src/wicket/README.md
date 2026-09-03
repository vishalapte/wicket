---
summary: The package's own orientation: the five verbs, the layering contract, and how the modules divide the work.
pnode: [../../README.md]
bearing: record
---

# wicket

Personal mail tooling, no runtime dependencies (stdlib only). Four verbs: two
read a mailbox over **IMAP with an app password** (no OAuth, no provider cloud
project); the other two never touch a mailbox at all. A fifth, `config`, is not
a verb but a noun (the three owner-authored maps), so it takes its own nested
subparsers rather than sitting flat beside the four:

- **`wicket catalog`**: observe the mailbox (headers only, never bodies) into
  a year-sharded manifest, recording what exists.
- **`wicket fetch`**: download full `.eml` for matching threads, filed by
  sender domain, recording what is held.
- **`wicket report`**: read-only summaries over the manifest (top senders, all
  addresses ever seen, a bucket across every store); no IMAP, no credentials.
- **`wicket ingest`**: file local `.eml` — a folder (a desktop-client
  drag-export) or a single message — into the manifest + archive, routing each
  thread to the store it belongs to. Offline, for a mailbox IMAP cannot reach. Additive: an archived
  message is never deleted.
- **`wicket config <account|domain> <aliases|routes> <verb>`**: CRUD over
  `account-aliases.json` / `domain-aliases.json` / `domain-routes.json` instead
  of hand-editing them; no IMAP.

The two IMAP verbs are **read-only on the mailbox by design**: they `SELECT` it
read-only and never send, delete, or modify mail. `ingest` reads no mailbox; it
does move an ingested `.eml` out of its *source folder* to `~/.Trash` once the
message is durably archived (`--no-delete` opts out) — a move, never an unlink.

## Provider status

Today every mail tool speaks **Gmail** over IMAP (Gmail's `X-GM-RAW`,
`X-GM-MSGID`, `X-GM-THRID`, `X-GM-LABELS` extensions). **Fastmail** support is
planned behind a `MailProvider` interface, selected per run with
`--source {gmail|fastmail}`. See `../../docs/adr/0001-portable-query-grammar.md`
for the cross-provider query design and `../../docs/QUERY-FIDELITY.md` for the
key-by-key divergence table.

## Layout

```
src/wicket/
  __main__.py        python -m wicket <verb>  (Pattern 2: discovery + own CLI,
                      four verb subcommands plus one noun subcommand, config)
  auth.py            IMAP login + SELECT (app password)
  env.py             account / credential / store path resolution, IMAP host
  domains.py         sender-domain alias canonicalization
  message.py         provider-neutral key + field ownership
  manifest.py        the year-sharded store (read / write / merge shards)
  reconcile.py       offline downloaded/domain reconcile + the filing-domain rule
  catalog/  fetch/  report/  ingest/  config/   each a vertical, lib -> api -> face:
    cli.py           the cli face (argparse, add_help=False; folded into the
                      root's subparser via parents=[...] — not a CLI entry on
                      its own, no __main__.py here)
    api.py           orchestration: resolve account/paths/creds, then dispatch
    lib.py           the worker (engine); faces reach it only through api
```

Data lives under the **mail root** (`~/.delphi/mail`, or `$WICKET_MAIL_ROOT`), one
directory per **store** — an *account* (a real mailbox) or a *bucket* (a topical
destination that is no mailbox). wicket never creates a store, and **fails closed**
when the mail root is absent: that means a locked (Cryptomator) vault, and writing
to its mount point would leave mail in plaintext underneath it.

## One-time setup (Gmail)

1. Enable 2-Step Verification, then generate an app password at
   https://myaccount.google.com/apppasswords.
2. `make install` from the repo root (editable-installs into `./.venv`).
3. First run prompts for your address + app password and caches them at
   `~/.config/gmail/<account>/imap.json` (mode `0600`):

   ```
   .venv/bin/python -m wicket catalog --dry-run
   ```

   Drop `--dry-run` to write the manifest. No browser flow, no consent screen.

Run `wicket <verb> --help` (or `python -m wicket <verb> --help`) for each
verb's full flag set.

## Security model

The cached app password is standing read access to the mailbox over IMAP.

- IMAP `SELECT` is always read-only; the connection can never send, flag,
  label, or delete mail.
- `imap.json` lives under `~/.config/gmail/<account>/` (dir `0700`, file `0600`),
  never in the repo, never on the command line, covered by `.gitignore`.
- An app password is account-scoped and revocable at
  https://myaccount.google.com/apppasswords, that is the kill switch.
- The mail tree is designed to sit inside an encrypted vault. Every verb refuses,
  and creates nothing, when the mail root does not exist — a locked vault must
  never be mistaken for an empty store (`env.require_mail_root`).
