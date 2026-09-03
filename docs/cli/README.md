---
command: python -m wicket
pattern: leaf
pnode: [../../README.md]
---

<!-- GENERATED — DO NOT EDIT. This page is a projection of the `python -m wicket…` CLI tree
     (src/wicket/**/__main__.py + argparse). The code is the source; this is derived.
     Regenerate:  python scripts/gen_cli_docs.py --write -->

# `python -m wicket`

> leaf — an argparse entry point.

CLI entry: python -m wicket

## Usage

```text
usage: python -m wicket [-h] {catalog,fetch,report,ingest,config} ...

Portable, read-only mail tooling over IMAP: catalog a mailbox, fetch matching
.eml, report over the manifest, or ingest a local export. Append --help to any
verb for its options.

positional arguments:
  {catalog,fetch,report,ingest,config}
    catalog             Observe the mailbox into the year-sharded manifest
    fetch               Download .eml for matching threads, filed by sender
                        domain
    report              Read-only reports over the manifest (senders,
                        addresses)
    ingest              Additively file a local .eml folder into the manifest
                        + archive
    config              Manage account-aliases / domain-aliases / domain-
                        routes maps

options:
  -h, --help            show this help message and exit
```

## Example output

_Illustrative only. Fabricated by the command's own author to show the range of outcomes; never executed by this generator, and not a captured run._

### catalog: incremental sweep

Invocation: `wicket catalog --account you@example.com`

```text
done: observed 128 message(s); wrote 2 year shard(s) under ~/.delphi/mail/you@example.com/manifest
```

Exit code: `0`

### fetch: dry run

Invocation: `wicket fetch --domains acme.com,globex.com --dry-run --account you@example.com`

```text
done (dry-run): 14 to download, 6 already on disk; 3 already held. Nothing written.
```

Exit code: `0`

### report: one-screen summary

Invocation: `wicket report --account you@example.com`

```text
manifest: ~/.delphi/mail/you@example.com/manifest
  messages               3241
  downloaded             2987  (held locally)
    gone from Gmail        41  (only copy)
    still in mailbox     2946
  observed only            254  (not held)
  distinct senders         318
  distinct addresses      512
```

Exit code: `0`

### ingest: routed local export

Invocation: `wicket ingest --src ~/Downloads/export`

```text
read: 40 file(s), 37 unique, 3 in-folder dup(s)
  added 22 to you@example.com (2026:22); 9 already archived
  added 6 to travel (2026:6)
  trashed: 37 source file(s) -> ~/.Trash
```

Exit code: `0`

### config: list an account-aliases entry

Invocation: `wicket config account aliases list`

```text
{
  "travel": [
    "reservations@example-travel.test"
  ]
}
```

Exit code: `0`

### error: unknown account

Invocation: `wicket report --account nobody@example.com`

```text
unknown account 'nobody@example.com': it has no store under ~/.delphi/mail and is not listed in account-aliases.json. If it is an address you receive at, add it as an alias of the account that owns it; if it is genuinely a new mailbox, create its store directory first.
```

Exit code: `2`
