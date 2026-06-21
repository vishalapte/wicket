# wicket

Personal mail tooling, no runtime dependencies (stdlib only). Two verbs that
read a mailbox over **IMAP with an app password** (no OAuth, no provider cloud
project), plus a read-only `report`:

- **`wicket.catalog`**: observe the mailbox (headers only, never bodies) into
  a year-sharded manifest, recording what exists.
- **`wicket.fetch`**: download full `.eml` for matching threads, filed by
  sender domain, recording what is held.
- **`wicket.report`**: read-only summaries over the manifest (top senders, all
  addresses ever seen); no IMAP, no credentials.

Both are **read-only on the mailbox by design**: they `SELECT` it read-only and
never send, delete, or modify mail.

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
  __main__.py        python -m wicket            (lists the verbs)
  auth.py            IMAP login + SELECT (app password)
  config.py          paths, IMAP host/port, mailbox name
  domains.py         sender-domain alias canonicalization
  message.py         provider-neutral key + field ownership
  manifest.py        the year-sharded store (read / write / merge shards)
  relocate.py        safe .eml relocation to canonical domains (fetch --force)
  catalog/
    __main__.py      python -m wicket.catalog    (observe CLI)
    observe.py       worker: headers into year-sharded manifest rows
  fetch/
    __main__.py      python -m wicket.fetch      (.eml download CLI)
    retrieve.py      worker: query into saved .eml per thread, domain-filed
```

## One-time setup (Gmail)

1. Enable 2-Step Verification, then generate an app password at
   https://myaccount.google.com/apppasswords.
2. `make install` from the repo root (editable-installs into `./.venv`).
3. First run prompts for your address + app password and caches them at
   `~/.config/gmail/imap.json` (mode 0600):

   ```
   .venv/bin/python -m wicket.catalog --dry-run
   ```

   Drop `--dry-run` to write the manifest. No browser flow, no consent screen.

Run `python -m wicket.<verb> --help` for each verb's full flag set.

## Security model

The cached app password is standing read access to the mailbox over IMAP.

- IMAP `SELECT` is always read-only; the connection can never send, flag,
  label, or delete mail.
- `imap.json` lives under `~/.config/gmail/` (dir 0700, file 0600), never in the
  repo, never on the command line, covered by `.gitignore`.
- An app password is account-scoped and revocable at
  https://myaccount.google.com/apppasswords, that is the kill switch.
