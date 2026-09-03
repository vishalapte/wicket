# CLAUDE.md: wicket package

Agent rules for modifying this package. Human usage lives in `README.md`; do not
duplicate it here. Bound by the racecar standards (resolver:
`$HOME/.meridian/README.md`). For any structural change read
`arch-coherence/PYTHON.md` first; do not restate it here.

## Architecture invariants (do not break)

- **src layout.** Code is under `src/wicket/`, import-only when installed.
  Never reintroduce a flat top-level package.
- **Module roles** (racecar §1/§3):
  - `wicket/__main__.py`: Pattern 2, discovery plus its own CLI.
    catalog/fetch/report/ingest are argparse **subcommands** on this one node
    (`python -m wicket <verb> ...`), never separate `wicket.<verb>` dotted CLI
    entries — a dotted path reads as a noun/sub-noun, and none of these four are
    things you address, they are actions. `config` sits alongside them but is
    not a fifth action — it is a genuine **noun** (the three owner-authored
    maps), so it takes its own nested subparsers rather than flags on this node
    (see `wicket/config/cli.py`). `parser()` builds the root parser and folds
    each verb's own `cli.parser()` in via `parents=[...]`; `main()` dispatches
    to the resolved verb's `cli.dispatch(args)`.
  - `wicket/{catalog,fetch,report,ingest,config}/cli.py`: the thin CLI face per
    verb —
    a `parser()` factory (`add_help=False`; it is always used as a `parents=`
    component of the root's subparser, never standalone) and a `dispatch(args)`
    that calls into its sibling `api.py`. **Not a CLI entry**: no `__main__.py`
    lives in these packages, on purpose — `python -m wicket.catalog` is refused
    by the interpreter itself (`'wicket.catalog' is a package and cannot be
    directly executed`), and `python -m wicket catalog` is the only way in. By
    convention a face reaches the engine through its sibling api, importing only
    `api`, `env` (env-layer constants/defaults), and stdlib; it maps the
    api's exceptions to exit codes and renders the human summary. Reaching past
    api into the worker/shared is a convention-deviation caught in review, not a
    build error.
  - `wicket/{catalog,fetch,report,ingest,config}/api.py`: the orchestration home
    for each verb (lib seam). Resolves the account (`env.resolve_account`), the
    per-account paths and credentials, seeds credentials (gated on
    `interactive`), dispatches into the sibling worker, and returns the worker's
    stats dict. `catalog.api` and `fetch.api` re-export `AuthError` (they import
    `auth`) so the face can map it without importing `auth`. `report.api` is
    read-only (no IMAP, no credentials). The read helpers `held_messages`
    (`fetch.api`) and `manifest` (`report.api`) live here too. `config.api` is
    the odd one out — no account to resolve — it instead resolves which of the
    three maps a request names (`RESOURCES`), validates `--primary`/items
    against that map's own rules, then calls the generic engine in `config.lib`.
  - `auth.py`, `env.py`, `domains.py`, `message.py`, `manifest.py` (package
    root): shared. `env.DEFAULT_THREADS` is the one home for the worker-pool
    size; `domains.canonical_domain` for subdomain folding; `message.message_key`
    for the provider-neutral key; `manifest` for the year-sharded store (ADR 0002).
    `env` also owns the **destination rules**: `MAIL_ROOT` (+ `$WICKET_MAIL_ROOT`),
    `require_mail_root` (fail closed — an absent root is a locked vault, not an
    empty store), `known_accounts` (stores that exist; skips dotted dirs, so a
    `.git` in the tree is not an account), `account_of` / `Identities` (who is
    *me*), and `flatten_aliases` (the one home for the alias-file rule, which
    `domains` reuses for its two maps).
  - **Three owner-authored maps at the mail root**, each answering a different
    question, and never conflated: `account-aliases.json` (addresses that ARE me;
    read by `env`), `domain-routes.json` (counterparties that are NOT me, and
    where their mail goes; read by `domains`), `domain-aliases.json` (what a
    domain is CALLED; read by `domains`). A counterparty in the alias map would
    make the filing rule treat it as *me* and file its mail under the
    recipient's domain — that is the bug the split prevents. `wicket config`
    is how you WRITE to any of the three; `env`/`domains` only ever read them.
  - `catalog/lib.py`, `fetch/lib.py`, `ingest/lib.py`, `config/lib.py`: per-verb
    worker modules. **No argparse, no `if __name__ == "__main__"`, no CLI.**
    Strictly non-interactive; the IMAP workers parallelize with a per-worker
    connection (imaplib is not thread-safe across commands). `ingest/lib.py`
    touches no IMAP at all: it parses `.eml` on disk, threads them, routes each
    thread, and files. It takes its resolvers (`AccountFor`) and maps from the
    api as arguments, so the worker reads no config file and no directory
    listing of its own. `config/lib.py` is generic on purpose: one
    `{primary: [items]}` CRUD engine serves all three maps, since they share
    that shape; the per-map validation lives in `config/api.py`, not here.
- **Import direction** is checked by `[tool.importlinter]` in `pyproject.toml`:
  one `layers` contract, `wicket.__main__` (root face) → per-verb `cli` (verb
  face) → per-verb `api` → per-verb worker → `reconcile` → shared
  (`auth`/`domains`/`message`/`manifest`) → `env`, downward only (acyclic).
  The five verbs are independent siblings (no cross-imports). The face → api →
  lib routing is convention, not a wall: the contract gates direction and
  acyclicity (defects); a face that bypasses its api is a reviewed
  convention-deviation, for the owner to judge. Top-level imports only; no
  lazy/in-function imports; no upward imports.
- The import-linter contract is **source of truth**. Any package-structure
  change requires editing `[tool.importlinter]` in the **same change**, then
  `lint-imports` must report `Contracts: 1 kept, 0 broken.`
- Adding a new verb ⇒ give it a `cli.py` (no `__main__.py`), register it in the
  root `__main__.py`'s `subcommands()` list and `_VERBS` dict, and add its
  layer to `[tool.importlinter]` (explicit registration; no dynamic discovery).

## Security invariants (hard rules)

- IMAP `SELECT` is always read-only (`auth.open_mailbox(..., readonly=True)` is
  the default for every caller, including all worker threads). **Never flip it.**
  These tools must never gain send/flag/modify/delete capability.
- The app-password file lives outside the repo under the state dir (dir `0700`,
  file `0600`). Never write it into the repo, log its contents, accept it as a CLI
  argument, or print it.
- Keep `--non-interactive` failing **loudly** (non-zero exit). Cron/launchd
  relies on that to surface a revoked app password. Do not add a silent fallback.

## Provider work in progress

Gmail is the only backend today; its `X-GM-*` IMAP extensions are used directly
in the worker modules. Phase 2 extracts these behind a `MailProvider` interface
(`provider.py`); phase 3 adds the Fastmail backend and the `--source` flag. Do
not add a second provider's quirks ad hoc before the interface lands, that is
what the seam is for.

## Verify after any change

```
.venv/bin/python -m py_compile src/wicket/**/*.py
.venv/bin/lint-imports                 # → Contracts: 1 kept, 0 broken.
.venv/bin/python -m wicket             # → subcommand help (no args prints and exits 0)
.venv/bin/python -m wicket catalog --help
.venv/bin/python -m wicket fetch --help
.venv/bin/python -m wicket report --senders --account you@gmail.com | head
.venv/bin/python -m wicket config account aliases list
wicket ingest --src ~/Downloads/export   # installed console script, same dispatch
```
