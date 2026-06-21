# CLAUDE.md: wicket package

Agent rules for modifying this package. Human usage lives in `README.md`; do not
duplicate it here. Bound by the racecar standards (resolver:
`$HOME/.meridian/README.md`). For any structural change read
`arch-coherence/PYTHON.md` first; do not restate it here.

## Architecture invariants (do not break)

- **src layout.** Code is under `src/wicket/`, import-only when installed.
  Never reintroduce a flat top-level package.
- **Module roles** (racecar §1/§3):
  - `wicket/__main__.py`: Pattern 1, pure discovery. `commands()` +
    `_print_commands()`. Lists sub-packages; no CLI of its own.
  - `wicket/{catalog,fetch,report}/__main__.py`: Pattern 3, thin CLI faces.
    Each owns its argparse via a `parser()` factory that `main()` calls, then
    dispatches into its sibling `api.py`. By convention a face reaches the engine
    through its sibling api, importing only `api`, `config` (env-layer
    constants/defaults), and stdlib; it maps the api's exceptions to exit codes
    and renders the human summary. Reaching past api into the worker/shared is a
    convention-deviation caught in review, not a build error.
  - `wicket/{catalog,fetch,report}/api.py`: the orchestration home for each
    verb (lib seam). Resolves the account (`config.resolve_account`), the
    per-account paths and credentials, seeds credentials (gated on
    `interactive`), dispatches into the sibling worker, and returns the worker's
    stats dict. `catalog.api` and `fetch.api` re-export `AuthError` (they import
    `auth`) so the face can map it without importing `auth`. `report.api` is
    read-only (no IMAP, no credentials). The read helpers `held_messages`
    (`fetch.api`) and `manifest` (`report.api`) live here too.
  - `auth.py`, `config.py`, `domains.py`, `message.py`, `manifest.py` (package
    root): shared. `config.DEFAULT_THREADS` is the one home for the worker-pool
    size; `domains.canonical_domain` for alias canonicalization;
    `message.message_key` for the provider-neutral key; `manifest` for the
    year-sharded store (ADR 0002).
  - `catalog/lib.py`, `fetch/lib.py`: per-verb worker modules. **No
    argparse, no `if __name__ == "__main__"`, no CLI.** Strictly non-interactive,
    parallelize with a per-worker IMAP connection (imaplib is not thread-safe
    across commands).
- **Import direction** is checked by `[tool.importlinter]` in `pyproject.toml`:
  one `layers` contract, per-verb `__main__` (face) → per-verb `api` → per-verb
  worker → `reconcile` → shared (`auth`/`domains`/`message`/`manifest`) →
  `config`, downward only (acyclic). The three verbs are independent siblings
  (no cross-imports). The face → api → lib routing is convention, not a wall:
  the contract gates direction and acyclicity (defects); a face that bypasses
  its api is a reviewed convention-deviation, for the owner to judge. Top-level
  imports only; no lazy/in-function imports; no upward imports.
- The import-linter contract is **source of truth**. Any package-structure
  change requires editing `[tool.importlinter]` in the **same change**, then
  `lint-imports` must report `Contracts: 1 kept, 0 broken.`
- Adding a sub-package under `wicket/` ⇒ register it in the root `__main__.py`
  `commands()` list (explicit registration; no dynamic discovery).

## Security invariants (hard rules)

- IMAP `SELECT` is always read-only (`auth.open_mailbox(..., readonly=True)` is
  the default for every caller, including all worker threads). **Never flip it.**
  These tools must never gain send/flag/modify/delete capability.
- The app-password file lives outside the repo under the state dir (dir 0700,
  file 0600). Never write it into the repo, log its contents, accept it as a CLI
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
.venv/bin/python -m wicket           # → discovery listing
.venv/bin/python -m wicket.catalog --help
.venv/bin/python -m wicket.fetch --help
.venv/bin/python -m wicket.report --senders --account you@gmail.com | head
```
