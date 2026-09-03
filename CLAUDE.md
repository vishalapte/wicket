# CLAUDE.md: wicket repo

Repo-level agent rules. Human overview is in `README.md`; do not duplicate it.
Package-level rules live in `src/wicket/CLAUDE.md` and auto-load when working
in that subtree, this file is repo-scope only and must not restate them.

Bound by the racecar standards. Resolver: `$HOME/.meridian/README.md`: read it
first to find which component applies; do not load component files
speculatively.

## Orientation

wicket is a standalone, stdlib-only library providing **four verbs** over one
year-sharded manifest: `catalog` (observe the mailbox into the manifest) and
`fetch` (download `.eml`), both read-only on the mailbox over IMAP; `report`
(manifest summaries and bucket views, no IMAP); and `ingest` (file a local `.eml`
export offline, routed per thread, for a mailbox IMAP cannot reach). A fifth
subcommand, `config`, is a noun rather than a verb — CRUD over the three
owner-authored maps — and takes its own nested subparsers accordingly. It was
lifted out of `factotum`; its consumers (factotum, meridian) depend on it. The
provider story is mid-build: Gmail works today, Fastmail is planned behind a `--source`
interface.

Data lives under the **mail root** (`~/.delphi/mail`, `$WICKET_MAIL_ROOT`), one
directory per **store**. A store is an **account** (a real mailbox) or a **bucket**
(a topical destination that is no mailbox: `travel`, `shopping`). The mailbox
always wins a message's physical home; a bucket is the home only for mail no
mailbox owns, and topical grouping of mailbox-owned mail is a *view*
(`report --bucket`), never a relocation — see the README's "Where a message goes".

- `src/wicket/**`: package code. Rules: `src/wicket/CLAUDE.md`.
- `scripts/`: repo tooling synced from racecar (`check_*.py`, `clean_files.sh`).
  Not importable; never import it from the package.
- `docs/`: plans and reference. `docs/adr/` holds decision records;
  `docs/QUERY-FIDELITY.md` is the portable-query divergence table.

## Architecture

- **src layout.** Code is under `src/wicket/`, import-only when installed.
  Never reintroduce a flat top-level package.
- **Layering is the contract.** `[tool.importlinter]` in `pyproject.toml` is the
  source of truth: one `layers` contract, `wicket.__main__` (root face) →
  per-verb `cli` (verb face) → per-verb `api` → per-verb worker → `reconcile` →
  shared (`auth`/`domains`/`message`/`manifest`) → `env`, downward only
  (acyclic). The four verbs are independent siblings (no cross-imports). The
  face → api → lib routing (a face reaches the engine through its api, not the
  worker/shared directly) is the recommended **convention**, reviewed not
  walled: the contract gates defects (cycles, direction); it does not forbid
  a defensible choice. Any package-structure change edits the contract in the
  **same change**; `lint-imports` must report `Contracts: 1 kept, 0 broken.`
- Adding a new verb ⇒ give it a `cli.py` (no `__main__.py`), register it in
  the root `__main__.py`'s `subcommands()` list and `_VERBS` dict, and add its
  layer to `[tool.importlinter]` (explicit registration; no dynamic discovery).

## Conventions

- **Do not commit.** `git commit` is blocked at the tool level; the owner
  commits manually.
- **No secrets in the repo.** The IMAP app password lives outside the tree under
  the per-source state dir (mode `0600`). Never write it into the repo, log it, or
  accept it as a CLI argument.
- **Read-only IMAP is a hard invariant.** `SELECT` is always read-only; these
  tools must never gain send/flag/modify/delete capability. This is about the
  *mailbox*. `ingest` does move an ingested `.eml` out of its **source folder** to
  `~/.Trash` once the message is durably archived (`--no-delete` opts out) — a
  local folder is not a mailbox, and it is a move, never an unlink.
- **Never create a store.** A destination (the mail root, an account, a bucket) is
  made by an explicit owner `mkdir`, never inferred by a verb. A mistyped
  `--mail-account` must be a hard error, and an absent mail root must **fail closed**:
  it means a locked vault, and writing there would leave mail in plaintext under
  the mount point.
- **An archived message is never deleted.** Additivity is what the whole ingest
  design rests on; `--dry-run` must write nothing and trash nothing.
- Generated artifacts stay gitignored (`.venv/`, `__pycache__/`, `*.egg-info/`,
  `.import_linter_cache`, `.mypy_cache`).

## Open work

- Phase 2: extract the Gmail-specific primitives behind a `MailProvider`
  interface (`provider.py`), no behavior change.
- Phase 3: add the Fastmail (IMAP) backend, `--source {gmail|fastmail}`, and the
  portable query compiler per `docs/adr/0001-portable-query-grammar.md`.
- Phase 4: rewire factotum and meridian to depend on wicket; delete the moved
  code from factotum.

## Observe at repo scope

```
make help                              # available targets
.venv/bin/lint-imports                 # → Contracts: 1 kept, 0 broken.
.venv/bin/python -m wicket             # subcommand help (catalog/fetch/report/ingest)
wicket ingest --src ...                # installed console script, same dispatch
```
