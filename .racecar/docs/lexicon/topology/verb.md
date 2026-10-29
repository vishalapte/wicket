---
kind: schema
name: verb
carrier: file
domain: [racecar]
summary: "the leaf tier: a command, which contains nothing"
pnode: [noun.md]
bearing: contract
---

# tier: `verb`

The last tier: one command, at `<noun>/<verb>.md`.

`carrier: file`: a verb contains nothing, so a node at this tier is childless. Only the last
tier may be `carrier: file`, because a file cannot hold a further tier.

Its `pnode` is [`noun.md`](noun.md), which puts it after `noun` in the chain.

Not to be confused with `../verb/`, the BUCKET. A node at `<noun>/<verb>.md` is a verb's
USE; a node at `verb/<verb>.md` is that word's MEANING, used when the verb cuts across nouns.
Both declare `kind: verb`; the tier is about where a node sits, not what it is.
