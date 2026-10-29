---
name: ingest
kind: verb
domain: [wicket]
params: [mail-account, src, staged, source, domain, tag, no-delete, force, dry-run]
invocations: ["--staged", "--staged --dry-run --mail-account you@example.com", "--src does-not-exist", "--src docs --domain ../etc", "--src docs --dry-run --no-delete --tag a --tag b --force --source local --mail-account you@example.com", "--src docs --domain ../etc --json"]
summary: "Additively file what fetch staged, or a local .eml folder, into the manifest and archive."
pnode: [./README.md]
bearing: draft
---

# ingest

Additively file what fetch staged, or a local .eml folder, into the manifest and archive.
