---
name: fetch
kind: verb
domain: [wicket]
params: [mail-account, query, domains, last, max, target, alias-file, state-dir, threads, non-interactive, dry-run]
invocations: ["", "--target out", "--domains acme.com --dry-run --mail-account you@example.com --non-interactive", "--query from:x --max 5 --threads 1 --alias-file a.json --state-dir s --non-interactive", "--target out --json"]
summary: "Get .eml for matching messages into the store's staging dir, filing nothing; with no filter, every message not yet got, until Gmail throttles."
pnode: [./README.md]
bearing: draft
---

# fetch

Get .eml for matching messages into the store's staging dir, filing nothing; with no filter, every message not yet got, until Gmail throttles.
