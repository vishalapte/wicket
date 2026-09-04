---
role: summary
generator:
  name: racecar-llm-summary
  version: "0.99.3"
target:
  repo: wicket
  date: 2026-09-04
  version: "0.5.0"
bundle:
  - WICKET.md
  - WICKET-DOSSIER.md
external_paths:
  - provider.py                          # Phase 2; the MailProvider seam does not exist yet
  - imap.json                            # the credential file, outside the tree under the state dir
  - myaccount.google.com/apppasswords    # where a Gmail app password is minted
---

# wicket — Summary

The entry point of a two-file bundle. This half carries the synthesis: what the
system is, why each part exists, what each part catches, and what it does not.
Every enumeration — verbs, entities, relationships, delivered scripts — lives in
[`WICKET-DOSSIER.md`](WICKET-DOSSIER.md), and nothing is stated in both.

## What it is, and who it is for

wicket archives a personal mailbox to local `.eml` files, filed by counterparty
domain, and keeps a queryable record of what it has seen and what it holds. The
user is one person with their own mail and their own machine; there is no
multi-tenant story, no server, and no cloud project. It is a standalone,
stdlib-only library that two other private repos (factotum, meridian) consume as
a dependency — they want "give me every message this counterparty sent, as
files" without learning IMAP.

The constraint that shapes everything: **wicket is read-only on the mailbox, and
that is enforced rather than promised.** `SELECT` is always read-only, so the
tool cannot send, flag, delete, or modify, whatever anyone later asks it to do.

## The thesis, and what follows from it

One idea does most of the work: **separate what the mailbox says exists from
what you actually hold, and keep both in the same row.**

A message is observed (it exists in the mailbox, with these headers) and
separately settled (its body is downloaded, at this path). Two booleans, one
record, sharded by year. Nearly every property worth having follows from that
rather than being built:

- **Incremental sweeps.** Observation is idempotent, so re-running adds only
  what is new and touches only the year shards that changed.
- **Offline queries.** Everything except the two IMAP verbs reads the manifest,
  so summaries and consumer reads need no network and no credentials.
- **Deletion is visible, not silent.** A message that stops appearing in the
  mailbox is marked, not dropped; you can still see it once existed.
- **The store rebuilds.** The `.eml` on disk is the truth and the manifest is a
  cache over it, so the record can be reconstructed from mailbox plus disk.

The alternative — a "seen" index and a "held" index — was rejected because two
indexes over the same messages drift, and nothing tells you which one is wrong.

## The primitives

Six kinds, described here at the level of kind; the dossier names the members.

A **store** is one directory holding everything about one destination: its
manifest and its archive. A store is either an **account** (a real mailbox, with
credentials, that can be swept) or a **bucket** (a topical destination with no
mailbox behind it — `travel`, `shopping`). A **message** is one row in a
manifest shard, keyed by a provider-neutral key derived from `Message-ID`. A
**thread** is a conversation reconstructed from headers, never from subject
matching, and it is the unit of both filing and routing. A **filing domain** is
the counterparty's domain, which decides the folder; it is derived, never stored
as truth. **Identities** is the set of addresses that are *you* — a membership
test rather than a list, because a catch-all cannot be enumerated.

## The subsystems

### The manifest — the record everything else reads

**Why it exists.** Before it, "what do I have" and "what does the mailbox have"
were separate questions with separate answers, reconciled by hand.

**What it covers.** One row per message per store, carrying identity, what was
observed, and what was settled, in year-sharded JSON Lines with deterministic
serialization.

**Why that matters.** A write touches only the affected years, and a rewritten
shard's untouched rows serialize byte-identically — so any verb can merge into a
shard without disturbing what was already there, and a re-run is a no-op.

**What it catches.** Divergence between the mailbox and the disk: a row marked
held whose file is gone, a message that has left the mailbox, a domain folder
that disagrees with the row.

**What it does NOT catch.** It cannot tell you a message was *modified* in the
mailbox — headers are re-observed, bodies are not re-fetched once held. It has
no integrity check on the `.eml` bytes: a truncated download stays marked held.
And it holds no backup property whatsoever (see State of play).

### catalog — observation

**Why it exists.** Sweeping headers is cheap and sweeping bodies is not; the two
belong on separate schedules, and conflating them made every "what's new" query
cost a full download.

**What it covers.** Mailbox headers into the manifest, incrementally by default,
with a full sweep on request.

**Why that matters.** The expensive verb can be run rarely and the cheap one
often, and the cheap one alone answers most questions.

**What it catches.** Anything the mailbox now says that the record did not: new
mail, changed labels, and messages that have disappeared.

**What it does NOT catch.** It never touches bodies, so it cannot tell you a
message is large, or an attachment exists, beyond what the headers say. It
grades the mailbox's *current* state only: a message that arrived and was
deleted between two sweeps is invisible to both.

### fetch — settlement

**Why it exists.** The point of the archive is files you can open without a
network, a provider, or an account that still exists.

**What it covers.** Downloading full RFC822 bodies for matching threads into
`<domain>/<YYYY-MM>/`, and recording where each landed.

**Why that matters.** Filing by counterparty domain rather than by date or
label makes the archive browsable by the question people actually ask of old
mail — everything from this company.

**What it catches.** A thread whose counterparty resolves to a domain gets one
folder, and a reply that arrives months later joins it rather than starting a
second one.

**What it does NOT catch.** Mail with no resolvable counterparty (several
external parties, or none) has no natural home and lands unfiled; the fix is an
owner-authored map, not a cleverer rule. Selection is per provider query today,
so a Fastmail-shaped query does not exist yet.

### ingest — the offline path

**Why it exists.** One mailbox in the corpus cannot be reached over IMAP at all:
basic auth is dead there and an OAuth app registration was ruled out. Without an
offline path, that mail simply is not archivable.

**What it covers.** A local `.eml` export — a drag-export folder, or one file —
parsed, threaded, routed, and filed into the manifest and archive with no
network involved. It is the only verb that both observes and settles in one
pass.

**Why that matters.** It makes the archive's coverage independent of any
provider's API policy, which is the failure that motivated it.

**What it catches.** A re-export of mail already archived is recognized by key
and left byte-for-byte alone, so running it twice costs nothing and loses
nothing. A folder plainly addressed to a different account than the one named is
refused, because the account argument is a destination and not a filter — the
guard exists because getting that wrong once filed a folder into two stores.

**What it does NOT catch.** It cannot tell a *partial* export from a complete
one: a folder holding half a thread files half a thread, and nothing knows the
other half was never dragged out. Threading is header-based, so a message whose
client stripped `References` and `In-Reply-To` starts its own thread. And a
message nobody claims is left alone rather than guessed at — deliberate, but it
means unrouted mail accumulates in the source folder until a map claims it.

### report — the views

**Why it exists.** Every question that does not need the network should not pay
for it.

**What it covers.** Summaries over the manifest, and the bucket view — a query
across *every* store answering what a topic claims and where those messages
physically live.

**Why that matters.** The bucket view is what makes the "mailbox always wins"
rule survivable. Without it, wanting travel mail in one place would mean moving
files, and moving files is the thing that cannot work (below).

**What it catches.** A topical grouping that spans stores, without relocating
anything.

**What it does NOT catch.** It reports on the record, so it inherits every gap
in the record: mail never catalogued is mail the report does not know about. It
has no full-text search — it reads rows, not bodies.

### The destination model — stores, and the three maps

**Why it exists.** Deciding where a message goes is the one question every verb
asks, and it was originally answered in three different places.

**What it covers.** Three owner-authored maps at the mail root, all optional and
none inferred: which addresses *are you* and which store owns them; which
counterparties are *not you* and where their mail goes; and how a subdomain
folds onto its primary.

**Why that matters.** "This address is me" and "this counterparty's mail goes
here" have identical file shapes and opposite meanings. Listing a vendor in the
identity map makes every message *from* that vendor look outbound and files it
under the recipient's domain. Separate files, separate loaders, no ambiguity.

**What it catches.** A mistyped destination is a hard error rather than a new
directory, because **wicket never creates a store**. A route to a bucket that
does not exist resolves to nothing and the mail stays put.

**What it does NOT catch.** The maps are owner-authored, so they are only as
right as the owner. `config` (below) guarantees a row is *parseable*; nothing
validates that a domain route reflects what you actually want, and nothing
notices that an alias has gone stale.

### config — editing the destination maps

**Why it exists.** The three maps above were hand-edited JSON before this: no
validation, and a malformed row surfaced only the next time something tried to
read it.

**What it covers.** `list`/`create`/`update`/`delete` over each of the three
maps, addressed as a noun (`wicket config <account|domain> <aliases|routes>
<verb>`) rather than a fifth action — editing a map is not a mailbox operation
the way catalog/fetch/ingest/report are, so it takes its own nested subparsers
instead of sitting flat beside them.

**Why that matters.** Every write validates the primary and its items with
the same rules the read side already validates with — an address vs. a
bucket, a domain vs. a `*.parent` wildcard — so a row this tool writes is
guaranteed readable back. Each map's file can also be relocated
independently of the mail root and of the other two, which the hand-edited
era had no way to express.

**What it catches.** A malformed address, an invalid bucket name, or a
domain that doesn't parse — before it ever reaches disk.

**What it does NOT catch.** It validates *shape*, never *intent*: nothing
stops an owner from routing a domain to the wrong bucket, or aliasing an
address they did not mean to. The maps are still owner-authored judgment;
this only guarantees the file stays parseable.

### Credentials and the read-only invariant

**Why it exists.** The archive is the whole personal mail corpus; the blast
radius of a mistake is the point.

**What it covers.** One app password per account, stored outside the repo and
outside the mail tree at mode `0600`, never accepted as a command-line argument.
`SELECT` is read-only on every connection.

**Why that matters.** The credential is account-scoped and revocable in one
click, and the connection cannot write, so the worst case is a leak of read
access rather than a mailbox someone can destroy.

**What it does NOT catch.** A standing credential is still a standing
credential: nothing rotates it, nothing expires it, and nothing detects that it
has been copied. The read-only guarantee covers the *mailbox* only — ingest does
move files out of a local source folder, which is not a mailbox.

## Decisions, and what they rejected

- **The mailbox wins a message's physical home; a topical rule never relocates
  it.** A manifest is the observation record *of a mailbox*, so if a counterparty
  rule could pull a Gmail-owned message into `shopping`, the next sweep would
  re-observe it in Gmail and write it back — the same message in two stores,
  forever, recreated by every run. The counterparty map originally outranked the
  mailbox; it was inverted for exactly this reason, and the topical question
  became a view instead.
- **Routing is per thread, never per message.** A conversation you were addressed
  on in some replies and cc'd on in others would otherwise split across two
  stores, and a thread has one identifier and one folder by construction.
- **The sender counts as a participant.** Routing on recipients alone stranded
  every outbound thread, because mail you sent is addressed entirely to
  counterparties.
- **Fail closed when the mail root is absent.** The tree is meant to live in an
  encrypted vault, and a *locked* vault leaves its mount point as an ordinary
  empty directory. Read as "a fresh, empty store", that would write mail in
  plaintext underneath the mount point. Every verb now refuses.
- **Trash, never unlink.** An ingested source file is *moved* to the system trash
  once the message is durably archived. The destructive default keeps an undo.
- **An app password rather than OAuth.** No consent screen, no cloud project, no
  refresh dance; the cost is a standing credential, priced against a read-only
  connection and a one-click revoke.
- **Rejected: symlinking bucket views into the account archives.** Attractive —
  one physical copy, browsable in a file manager — and already the pattern a
  sibling repo uses. Deferred because the vault is served over FUSE-T or WebDAV,
  where BSD file flags are unimplemented and symlink support is
  backend-dependent: the view would be least reliable exactly where the data is
  going to live.

## What looks wrong but is intentional

- **A verb that will not create its own destination.** Every other tool of this
  shape makes the directory it needs. Here an absent directory is the signal
  that something is wrong — a locked vault, a typo — and creating it destroys
  the signal.
- **`account=None` means *route*, not *use the default account*.** In the ingest
  entry point that one argument does not mean what it means in every sibling
  verb. Omitting the destination is how you ask for per-thread routing.
- **The domain in a row is derived, and the path is the truth.** A downloaded
  message's folder comes from where the file actually sits, not from the field;
  the field can be null. This is why reconciliation deliberately refuses to move
  an archived file.
- **The archive is not a backup and is not treated as one.** The mail tree is
  never committed, is not in the offsite corpus backup, and this machine has no
  Time Machine destination. Anything destructive is preceded by an explicit
  copy, by hand.

## State of play

**Working today:** the full Gmail path — sweep, download, offline reports — plus
the offline ingest path with per-thread routing, buckets, the three maps, the
trash-not-unlink rule, and the vault guard. CRUD over the three maps
(`config`), each independently relocatable. Consumers read held messages
through the library rather than through IMAP.

**Specified but not built:** the provider seam that would put Gmail behind an
interface, the Fastmail backend behind it, and the portable query grammar those
two need — the grammar has a dated decision record and a key-by-key divergence
table, and no compiler. The fidelity checker that would drift-check that table
ships with the compiler and does not exist yet.

**Honest gaps:** no backup of the archive; no integrity verification of
downloaded bodies; no full-text search; no scheduler, healthcheck, metrics, or
log configuration — observability is each verb's stdout summary and its exit
code. The consumers named as dependents have not been rewired yet, so the code
this repo replaced still lives in the repo it was lifted out of.

## Seams, and the invariants a change may not cost

**Seams.** New behaviour plugs in at three places: a new verb is a new
self-contained package, its argument parser folded into the root's single
dispatcher and registered explicitly (a literal name, an entry in the verb
table) — never discovered dynamically; a new provider is meant to plug in
behind the interface Phase 2 extracts; and destination policy is data, not
code — the three maps are how an owner changes routing without touching the
package, and `config` is how that data is edited without hand-authoring JSON.

**Invariants.** Any change must preserve all of these, and each has a
consequence attached:

- **Read-only on the mailbox.** Gaining send, flag, or delete capability makes
  every other guarantee here unenforceable.
- **An archived message is never deleted, and ingest is additive.** This is what
  makes re-running safe; without it, the offline path has no undo.
- **A thread has one identifier and one folder.** Break it and a conversation
  scatters across stores, which no report can reassemble.
- **No verb creates a destination.** Break it and a locked vault becomes a
  plaintext mail spill.
- **Nothing secret enters the repo, a log, or a command line.** The credential's
  only home is the state directory at `0600`.
- **The layering is downward-only and acyclic**, enforced by a contract in the
  packaging config that must report one contract kept and none broken.

## Vocabulary

- **Store** — one directory under the mail root holding one destination's
  manifest and archive. Not a database.
- **Account** — a store that is a real mailbox, named by its address.
- **Bucket** — a store that is *not* a mailbox: a topical destination with no
  credentials and nothing to sweep. It is the physical home only for mail no
  mailbox owns.
- **Manifest** — the year-sharded row record for one store. A cache over the
  archive, not the source of truth.
- **Archive** — the `.eml` files on disk, filed by domain and month. The truth.
- **Observed / settled** — the two halves of a row: what the mailbox says
  exists, and what is actually held.
- **Filing domain** — the counterparty's domain, which names the folder.
  Derived from the direction of the message, then folded onto its primary.
- **Identities** — the addresses that are *you*; a membership test, because a
  catch-all pattern cannot be enumerated.
- **Route** — assigning a whole thread to the store it belongs in. Distinct from
  *filing*, which picks the folder inside that store.
- **Unfiled** — a message whose counterparty could not be resolved. It is filed,
  under a reserved name; it is not lost.
- **Unrouted** — a thread no store claims. It is *not* filed, and its source file
  is left alone.

## Confidence

**Least confident**

- The claim that consumers (factotum, meridian) currently read through this
  library rather than through their own IMAP code is taken from this repo's
  stated plan, not verified in those repos. Phase 4 is explicitly "rewire the
  consumers, delete the moved code", which implies the old code is still live
  there. Verify in the consumer repos, not here.
- The bucket-view claim that it reads across *every* store is sourced from the
  report layer's own description; the exact set of stores it walks (all
  directories under the mail root, versus those a map names) was not re-derived
  from the code for this revision. Verify against `src/wicket/report/lib.py`.
- Two enumerated kinds in the dossier are declared-only and covered by no
  extractor: the domain entities and the library exports. Their member lists are
  stated and corroborated by nothing, because nothing in the tree enumerates the
  domain classes and each verb declares its exports on a single line that a
  line-anchored extractor cannot decompose. Treat both as the author's claim.
- The "specified but not built" boundary is drawn from the decision records and
  the phase list. A partially-started Phase 2 would look identical from here.

**Not in this brief**

- Anything about the contents of the mail store — how many messages, which
  counterparties, which accounts exist on any machine. The store's records
  belong to whoever they describe, and the brief describes the program only.
- Why this system exists rather than a commercial mail archiver, what it costs
  to run, or what the owner intends to do with the corpus. `unknown — ask user`.
- The state of the consumer repos, and whether anything else depends on this
  one. `unknown — ask user`.
