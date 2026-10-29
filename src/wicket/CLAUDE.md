# CLAUDE.md: wicket package

Agent rules for modifying this package. Human usage lives in `README.md`; do not
duplicate it here. Bound by the racecar standards (resolver:
`$HOME/.meridian/README.md`). For any structural change read
`arch-coherence/PYTHON.md` first; do not restate it here.

## Architecture invariants (do not break)

- **src layout.** Code is under `src/wicket/`, import-only when installed.
  Never reintroduce a flat top-level package.
- **Module roles** (racecar §1/§3):
  - `wicket/__main__.py`: the root noun's command line, in the form every noun
    uses (`NounParser`, `verbs(subcommands())`, one `_cmd_<verb>` per verb bound
    with `set_defaults(func=...)`, the `--json` loop, `main(argv)` =
    `lib.cli.run`). catalog/fetch/report/ingest are its **verbs**
    (`python -m wicket <verb> ...`), never separate `wicket.<verb>` dotted CLI
    entries — a dotted path reads as a noun/sub-noun, and none of these four are
    things you address, they are actions. The owner-authored maps are nouns,
    each its own node (`wicket/alias/__main__.py`, `wicket/store/__main__.py`),
    and so are the HALT (`wicket/halt/__main__.py`) and the account
    (`wicket/account/__main__.py`), all listed by `commands()`;
    a bare `python -m wicket` prints the nouns and
    verbs (`_print_commands`). Each `_cmd_<verb>` calls one `root/api.py`
    request function and hands its result to `json_renderer.show`, which prints
    it as JSON under `--json` and through `root/lib/renderer/plaintext.py`
    otherwise; `lib.cli.run` prints an `ApiError` on stderr and returns its
    `exit_code`. A face decides nothing and prints nothing itself; it computes
    `interactive` (is a tty present), a capability of the face, not the request.
  - `wicket/root/`: the root noun's code, racecar's layout for the package's own
    verbs. `root/api.py` holds each verb's orchestration (resolve the account,
    the per-account paths and credentials, seed credentials gated on
    `interactive`, dispatch into the worker, return its stats dict) and one
    request function per verb (`<verb>_root`, bound in `VERBS`) that the face
    calls: it takes the command line's arguments, says every refusal as an
    `ApiError` (a HALT, a busy store and a bad request exit 2, an IMAP error 1),
    and returns the verb's TypedDict (`root/lib/results.py`). The read helpers
    `held_messages` and `manifest` live there too. Each worker is
    `root/lib/<verb>.py`; every line the verbs print is in
    `root/lib/renderer/plaintext.py`, and the api calls the same formatters for
    a run that halts or skips a busy store partway, so its summary is said once.
    `report` is read-only (no IMAP, no credentials).
  - `wicket/{alias,store}/`: racecar CLI nouns (`__main__.py` face → `api.py`
    → `lib/`, results in `lib/results.py`, text in `lib/renderer/plaintext.py`).
    Their api reads the maps (`maps.read_map`), applies the edit in memory
    (`maps.add_items`/`remove_items`/…), checks the result with the reader's own
    rule (`env.parse_account_aliases`, `domains.parse_domain_routes`,
    `domains.parse_domain_aliases`), and only then writes (`maps.write_maps`).
    `store` writes domain-routes.json for `--from` and account-aliases.json for
    `--to`, both or neither.
  - `filing.py` (package root, one layer above the shared modules): the **one
    home for where mail is filed**: the domain rule (`compute_domain`), the
    thread rule (`opener`: a thread's earliest-`date` row across the store), the
    self and sent rules, the fallback, the rank (`file_thread`: anchor, forced,
    self, sent, rule, fallback, unfiled) with the `filed` kinds it records, and
    the router (`route_threads`). Never restate a filing rule in a verb.
    `reconcile.py` keeps only the reconcile.
  - `auth.py`, `env.py`, `domains.py`, `message.py`, `manifest.py` (package
    root): shared. `env.DEFAULT_THREADS` is the one home for the worker-pool
    size; `domains.canonical_domain` for subdomain folding; `message.message_key`
    for the provider-neutral key; `manifest` for the year-sharded store (ADR 0002)
    and the staged name (`staged_name`/`parse_staged`, writer and reader alike).
    `env` also owns the **destination rules**: `MAIL_ROOT` (+ `$WICKET_MAIL_ROOT`),
    `require_mail_root` (fail closed — an absent root is a locked vault, not an
    empty store), `known_accounts` (stores that exist; skips dotted dirs, so a
    `.git` in the tree is not an account), `account_of` / `Identities` (who is
    *me*), and `flatten_aliases` (the one home for the alias-file rule, which
    `domains` reuses for its two maps). `env` also owns the **halt dir** and the
    **log dir**, never one dir (`resolve_halt_path`: `$XDG_STATE_HOME/wicket/
    halt/<account>`; `resolve_log_path`: `.../log/<account>.log`, written only by
    `append_log`, the one log writer; each dir made `0700` by its first writer)
    and the **store lock** (`store_lock`: `fcntl.flock` on
    `<manifest dir>/.lock`, non-blocking, `StoreBusy` naming the holder's pid; not
    re-entrant, so an api or worker takes each store's lock once). `auth` owns the
    **HALT** (`write_halt`, `clear_halt`, `read_halt`, `list_halts`, `Halted`)
    and `first_year`, the one rule for where a whole-mailbox pass starts. `wicket.halt` reaches the HALT only through those.
  - **Three owner-authored maps at the mail root**, each answering a different
    question, and never conflated: `account-aliases.json` (addresses that ARE me;
    read by `env`), `domain-routes.json` (counterparties that are NOT me, and
    where their mail goes; read by `domains`), `domain-aliases.json` (what a
    domain is CALLED; read by `domains`). A counterparty in the alias map would
    make the filing rule treat it as *me* and file its mail under the
    recipient's domain — that is the bug the split prevents. `wicket.alias` and
    `wicket.store` are how you WRITE to them; `env`/`domains` only ever read them,
    through the `parse_*` rules the writers also apply.
  - `root/lib/catalog.py`, `root/lib/fetch.py`, `root/lib/ingest.py`: per-verb
    worker modules. **`fetch` files nothing**: it gets bodies into
    `<store>/staging/` (`env.resolve_staging_dir`) and writes no `downloaded` or
    `path`; `ingest` is the only filer, and `ingest --staged` files what fetch
    got. Duplicates have one home, `<mail root>/.duplicates/<store>/`
    (`env.resolve_duplicates_dir`). **No argparse, no `if __name__ == "__main__"`, no CLI.**
    Strictly non-interactive; the IMAP workers parallelize with a per-worker
    connection (imaplib is not thread-safe across commands). `root/lib/ingest.py`
    touches no IMAP at all: it parses `.eml` on disk, threads them, routes each
    thread, and files. It takes its resolvers (`AccountFor`) and maps from the
    api as arguments, so the worker reads no config file and no directory
    listing of its own. `maps.py` (shared) is generic on purpose: one
    `{primary: [items]}` engine serves all three maps, since they share that
    shape; validation is the readers' `parse_*` rules, not here.
- **Import direction** is checked by `[tool.importlinter]` in `pyproject.toml`:
  one `layers` contract, `wicket.__main__` (root face) → each noun's
  `__main__` → `wicket.root.api` and each noun's `api` → each root verb's
  worker (`wicket.root.lib.<verb>`), the root renderer and each noun's `lib` →
  `wicket.root.lib.results` → `reconcile` → `filing` → shared
  (`auth`/`domains`/`message`/`manifest`) → `env`, downward only (acyclic).
  The workers and nouns are independent siblings (no cross-imports). The face → api →
  lib routing is convention, not a wall: the contract gates direction and
  acyclicity (defects); a face that bypasses its api is a reviewed
  convention-deviation, for the owner to judge. Top-level imports only; no
  lazy/in-function imports; no upward imports.
- The import-linter contract is **source of truth**. Any package-structure
  change requires editing `[tool.importlinter]` in the **same change**, then
  `lint-imports` must report `Contracts: 1 kept, 0 broken.`
- Adding a new verb ⇒ declare it in the lexicon (`docs/lexicon/<verb>.md`), give
  it a `p_<verb>` block bound to a `_cmd_<verb>` in the root `__main__.py` and an
  entry in its `subcommands()` list, a request function in `root/api.py`'s
  `VERBS`, a result type in `root/lib/results.py`, a `print_<verb>` in
  `root/lib/renderer/plaintext.py`, and a worker `root/lib/<verb>.py` listed in
  `[tool.importlinter]` (explicit registration; no dynamic discovery).

## Security invariants (hard rules)

- IMAP `SELECT` is always read-only (`auth.open_mailbox(..., readonly=True)` is
  the default for every caller, including all worker threads). **Never flip it.**
  These tools must never gain send/flag/modify/delete capability.
- The app-password file lives outside the repo under the state dir (dir `0700`,
  file `0600`). Never write it into the repo, log its contents, accept it as a CLI
  argument, or print it.
- `auth.open_mailbox` is the one door to IMAP and refuses past the account's
  HALT. Never open a connection any other way, never write a HALT except
  through `auth.write_halt` (first cause wins; every worker checks the file
  before its next unit of work), and never remove one except through
  `auth.clear_halt`; each logs one line, and nothing else touches `halt/`. A HALT is an empty, touched file:
  the name is the account, the timestamp is when. A logged cause never carries
  the password or a path.
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
.venv/bin/python -m wicket             # → the nouns and verbs (no args lists them, exits 0)
.venv/bin/python -m wicket catalog --help
.venv/bin/python -m wicket fetch --help
.venv/bin/python -m wicket report --senders --mail-account you@gmail.com | head
.venv/bin/python -m wicket.store list
.venv/bin/python -m wicket.halt list
wicket ingest --src ~/Downloads/export   # installed console script, same dispatch
```
