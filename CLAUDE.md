# CLAUDE.md: wicket repo

Repo-level agent rules. Human overview is in `README.md`; do not duplicate it.
Package-level rules live in `src/wicket/CLAUDE.md` and auto-load when working
in that subtree, this file is repo-scope only and must not restate them.

Bound by the racecar standards. Resolver: `$HOME/.meridian/README.md`: read it
first to find which component applies; do not load component files
speculatively.

## Orientation

wicket is a standalone, stdlib-only library that reads a mailbox over IMAP and
provides two verbs over one year-sharded manifest: `catalog` (observe the
mailbox into the manifest) and `fetch` (download `.eml`), both read-only on the
mailbox, plus a read-only `report` (manifest summaries, no IMAP). It was lifted out of `factotum`; its consumers (factotum, meridian)
depend on it. The provider story is mid-build: Gmail works today, Fastmail is
planned behind a `--source` interface.

- `src/wicket/**`: package code. Rules: `src/wicket/CLAUDE.md`.
- `scripts/`: repo tooling synced from racecar (`check_*.py`, `clean_files.sh`).
  Not importable; never import it from the package.
- `docs/`: plans and reference. `docs/adr/` holds decision records;
  `docs/QUERY-FIDELITY.md` is the portable-query divergence table.

## Architecture

- **src layout.** Code is under `src/wicket/`, import-only when installed.
  Never reintroduce a flat top-level package.
- **Layering is the contract.** `[tool.importlinter]` in `pyproject.toml` is the
  source of truth: per-tool `__main__` → per-tool worker → shared
  `auth`/`domains` → `config`, downward only. The two verbs are independent
  siblings (no cross-imports). Any package-structure change edits the contract
  in the **same change**; `lint-imports` must report `Contracts: 1 kept, 0
  broken.`
- Adding a sub-package under `wicket/` ⇒ register it in the root
  `__main__.py` `commands()` list (explicit registration; no dynamic discovery).

## Conventions

- **Do not commit.** `git commit` is blocked at the tool level; the owner
  commits manually.
- **No secrets in the repo.** The IMAP app password lives outside the tree under
  the per-source state dir (mode 0600). Never write it into the repo, log it, or
  accept it as a CLI argument.
- **Read-only IMAP is a hard invariant.** `SELECT` is always read-only; these
  tools must never gain send/flag/modify/delete capability.
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
.venv/bin/python -m wicket           # discovery listing
```
