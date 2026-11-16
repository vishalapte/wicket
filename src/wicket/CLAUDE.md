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
    `ApiError` with its code, and returns the verb's TypedDict
    (`root/lib/results.py`). The read helpers
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
    `domains.parse_domain_aliases`), and only then writes (`maps.write_maps`);
    `maps.edit` does the four for one file, and `errors.refusing` turns the
    reader's `ValueError` into the `ApiError` the face prints.
    `store` writes domain-routes.json for `--from` and account-aliases.json for
    `--to`, both or neither.
  - **Refusals** (`errors.py`, `error-codes.jsonl`): `error-codes.jsonl` is the one
    home of the closed list of refusal codes, one JSON object per line (`reason`,
    `exit`, `when`, `fix`), shipped as package data and read once into
    `errors.CODES`. An exception class names its code with a `reason` class
    attribute and its facts in `facts` (`env.NoMailRoot`, `auth.Halted`,
    `env.StoreBusy`, ...); a class with none is `bad_request`. `ApiError(message,
    reason, **facts)` looks its exit code up (an unlisted reason, or a placeholder
    in the fix with no fact, raises) and its text is the message, then
    `fix: <the code's fix>` with `<placeholders>` filled from its facts. A message says only what happened; advice is the code's `fix`.
    Every request function in each api's `VERBS` is wrapped in `errors.refusals`,
    which logs the refusal's event and re-raises. Adding a code is a line in the
    file and a raise that names it; `tests/test_error_codes.py` holds the two in step.
  - **Logging** (`env`): one formatter, `env.LogfmtFormatter`, writes every log
    line: `TIME HOST wicket[PID]:` then logfmt, `account=`, `level=`, `event=`
    (`<verb>.<step>`) first; a field with no value is left out. The events table
    is in `env` beside it; a new log statement adds a row. Code logs a human
    message, an event (`extra=env.event(name, **fields)`), or both. Every face's
    `main` calls `env.configure_logging()`: the stderr handler prints messages as
    the terminal always showed them, and the `wicket.log` handler appends every
    event to `$XDG_STATE_HOME/wicket/log/wicket.log` in every run (best effort).
    `env.append_log(account, name, **fields)` is the one writer of an account's
    log (durable: it raises), and hands the same record on to `wicket.log`. The api
    sets the process account (`env.set_process_account`), which a line with no
    account of its own takes; a module global, so worker threads see it, cleared by
    `errors.refusals` when each request starts. A dry run's events
    carry `dry_run=true` and reach `wicket.log` alone.
  - `filing.py` (package root, one layer above the shared modules): which store a
    thread belongs in (the router, `route_threads`, voting with `participants`), a
    thread's opener (`opener`: its earliest-`date` row, whose thread id a local
    export's thread adopts), and the search a domain filter becomes
    (`build_domain_query`). There is no filing rank and no row `domain`: a row is
    linked to every domain it touches by its `from`/`to`/`cc`/`bcc`. A message's one file is
    `<store>/messages/YYYY/MM/<stem>.eml` (`manifest.message_file`, named by
    `message.message_stem`, month `message.message_date`), found by the row's `stem`
    (`manifest.message_files`). Never restate a filing rule in a verb.
    `reconcile.py` keeps only the reconcile.
  - `auth.py`, `env.py`, `domains.py`, `message.py`, `manifest.py` (package
    root): shared. `env.DEFAULT_THREADS` is the one home for the worker-pool
    size; `domains.canonical_domain` for subdomain folding; `message.message_key`
    for the provider-neutral key and `message.assign_threads` for a local export's
    header threads; `manifest` for the year-sharded store (ADR 0002);
    `provider` for each provider's dialect and the staged name
    (`Provider.staged_name`/`provider.parse_staged`, writer and reader alike).
    `env` also owns the **destination rules**: `MAIL_ROOT` (+ `$WICKET_DATA_DIR`),
    `require_mail_root` (fail closed — an absent root is a locked vault, not an
    empty store), `known_accounts` (stores that exist; skips dotted dirs, so a
    `.git` in the tree is not an account), `account_of` (who is *me*), and `flatten_aliases` (the one home for the alias-file rule, which
    `domains` reuses for its two maps). `env` also owns the **halt dir** and the
    **log dir**, never one dir (`resolve_halt_path`: `$XDG_STATE_HOME/wicket/
    halt/<account>`; `resolve_log_path`: `.../log/<account>.log`, written only by
    `append_log`, the one account-log writer; `resolve_wicket_log_path`:
    `.../log/wicket.log`, written only by the `wicket.log` handler; each dir made
    `0700` by its first writer, each log file `0600`)
    and the **store lock** (`store_lock`: `fcntl.flock` on
    `<manifest dir>/.lock`, non-blocking, `StoreBusy` naming the holder's pid; not
    re-entrant, so an api or worker takes each store's lock once). `auth` owns the
    **HALT** (`write_halt`, `clear_halt`, `read_halt`, `halt_in_force`, `list_halts`,
    `Halted`)
    and `first_year`, the one rule for where a whole-mailbox pass starts. `wicket.halt` reaches the HALT only through those.
  - **Three owner-authored maps at the mail root**, each answering a different
    question, and never conflated: `account-aliases.json` (addresses that ARE me;
    read by `env`), `domain-routes.json` (counterparties that are NOT me, and
    where their mail goes; read by `domains`), `domain-aliases.json` (what a
    domain is CALLED; read by `domains`). A counterparty in the alias map would
    make the filing rule treat it as *me* and file its mail under the
    recipient's domain — that is the bug the split prevents. `wicket.alias` and
    `wicket.store` are how you WRITE to them; `env`/`domains` only ever read them,
    through the `parse_*` rules the writers also apply. A fourth,
    `excluded-lists.json` (mailing lists never fetched or filed, `{"*" | account:
    [list-id]}`), is read by `wicket.exclusions` and written by `wicket.exclude`;
    `fetch` appends `Provider.exclude_list` to every search and `ingest` skips the
    mail (`Target.excluded_lists`), while `catalog` records the whole mailbox.
    `catalog` and `ingest` write a row's `list_id` (its `List-Id`, absent when
    none: a fact, never the decision); `report` leaves a row out by
    `manifest.is_excluded` (`excluded`, or `list_id` on the map now).
  - `root/lib/catalog.py`, `root/lib/fetch.py`, `root/lib/ingest.py`: per-verb
    worker modules. **`fetch` files nothing**: it gets bodies into
    `<store>/staging/` (`env.resolve_staging_dir`) and writes no `downloaded` or
    `stem`; `ingest` is the only filer, and `ingest --staged` files what fetch
    got. Duplicates have one home, `<mail root>/.duplicates/<store>/`
    (`env.resolve_duplicates_dir`). **No argparse, no `if __name__ == "__main__"`, no CLI.**
    Strictly non-interactive; the IMAP workers parallelize with a per-worker
    connection (imaplib is not thread-safe across commands). `root/lib/ingest.py`
    touches no IMAP at all: it parses `.eml` on disk, threads them, routes each
    thread, and files. It takes its resolvers (`AccountFor`) and maps from the
    api as arguments, so the worker reads no config file and no directory
    listing of its own. `maps.py` (shared) is generic on purpose: one
    `{primary: [items]}` engine serves all four maps, since they share that
    shape; validation is the readers' `parse_*` rules, not here.
- **Import direction** is checked by `[tool.importlinter]` in `pyproject.toml`:
  one `layers` contract, `wicket.__main__` (root face) → each noun's
  `__main__` → `wicket.root.api` and each noun's `api` → each root verb's
  worker (`wicket.root.lib.<verb>`), the root renderer and each noun's `lib` →
  `wicket.root.lib.results` → `reconcile` → `filing` → shared
  (`auth`/`domains`/`message`/`manifest`) → `errors` → `env` and `provider`,
  downward only (acyclic).
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
  `auth.clear_halt`; each logs one event (`halt.set`, `halt.clear`), and nothing else touches `halt/`. A HALT is an empty, touched file:
  the name is the account, the timestamp is when. A logged cause never carries
  the password or a path.
- Keep `--non-interactive` failing **loudly** (non-zero exit). Cron/launchd
  relies on that to surface a revoked app password. Do not add a silent fallback.

## Providers

Two backends, `provider.GMAIL` and `provider.FASTMAIL`. Every provider-specific
IMAP detail (the host, the all-mail folder or the folders a pass walks, the native
id, thread and labels items, the search a year or `--last` becomes, how an id
orders and names a file, the staged name, the required capability, the
app-password hint) lives in `wicket.provider`; `auth`, the workers and the api ask
the provider and never test its name. An account's provider is where its
`imap.json` links (`provider.provider_of`); anything else is refused. Every SELECT
is read-only: `auth.open_mailbox`'s own and `auth.select_folder`'s, the only two. A
third provider is a third `Provider`; never put its quirks in a verb.

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
