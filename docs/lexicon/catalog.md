---
name: catalog
kind: verb
domain: [wicket]
params: [mail-account, years, full, mailbox, store-dir, state-dir, threads, non-interactive, dry-run]
invocations: ["", "--years x", "--years ,", "--full --threads 1 --mailbox INBOX --store-dir x --state-dir y --mail-account you@example.com --non-interactive --dry-run", "--years x --json"]
summary: "Observe the mailbox into the year-sharded manifest; headers only, never bodies."
pnode: [./README.md]
bearing: draft
---

# catalog

Observe the mailbox into the year-sharded manifest; headers only, never bodies.
