---
name: noun
kind: schema
domain: [racecar]
summary: "a command group, and a position that is its address"
pnode: [README.md]
bearing: contract
required: []
---

# `kind: noun`

A command group: what `python -m <pkg>.<noun>` addresses, declared by a `README.md` that
says `kind: noun`.

    # docs/lexicon/store/README.md
    ---
    name: store
    kind: noun
    domain: [acme]
    summary: the artifact store and the verbs that move things into it
    pnode: [../README.md]
    bearing: contract
    ---

## Why it requires nothing of its own

A noun owes what the joining corpus's `base` demands of every node — a name, a kind, a
domain, a summary, a parent, a bearing — and nothing further. Its two other facts are not
fields:

- **What it contains** is its position. A noun's verbs are the `*.md` beside its
  `README.md`, and its sub-nouns are the directories under it. There is no `verbs:` list.
- **Which tier it may sit at** is set by the corpus's declared topology, not this file.

A node declaring `kind: noun` is a noun whatever directory it is filed under.
