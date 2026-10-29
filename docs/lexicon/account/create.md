---
name: create
kind: verb
domain: [wicket]
params: [name, config]
invocations: ["you@example.com --config docs", "you@example.com --config docs --json", "you@example.com --config does-not-exist"]
summary: "Make ~/.config/wicket/accounts/<name>/ and link its imap.json to <config>/imap.json."
pnode: [./README.md]
bearing: draft
---

# create

Make ~/.config/wicket/accounts/<name>/ and link its imap.json to <config>/imap.json.
