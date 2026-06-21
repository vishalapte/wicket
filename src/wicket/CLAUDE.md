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
  - `wicket/{catalog,fetch,report}/__main__.py`: Pattern 3, leaf CLIs. Each
    owns its argparse via a `parser()` factory that `main()` calls and dispatches
    into its sibling worker (`observe.py` / `retrieve.py` / `summary.py`).
    `catalog`/`fetch` seed credentials and work over IMAP; `report` is read-only
    (no IMAP, no credentials), summarizing the manifest.
  - `auth.py`, `config.py`, `domains.py`, `message.py`, `manifest.py` (package
    root): shared. `config.DEFAULT_THREADS` is the one home for the worker-pool
    size; `domains.canonical_domain` for alias canonicalization;
    `message.message_key` for the provider-neutral key; `manifest` for the
    year-sharded store (ADR 0002).
  - `relocate.py`: safe `.eml` relocation to canonical domains, used by
    `fetch --force`. No CLI.
  - `catalog/observe.py`, `fetch/retrieve.py`: per-verb worker modules. **No
    argparse, no `if __name__ == "__main__"`, no CLI.** Strictly non-interactive,
    parallelize with a per-worker IMAP connection (imaplib is not thread-safe
    across commands).
- **Import direction** is enforced by `[tool.importlinter]` in `pyproject.toml`:
  per-tool `__main__` → per-tool worker → shared `auth`/`domains` → `config`,
  downward only. The four tools are independent siblings (no cross-imports);
  `fold` imports no IMAP modules. Top-level imports only; no lazy/in-function
  imports; no upward imports.
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
