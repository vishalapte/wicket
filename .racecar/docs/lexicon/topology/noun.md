---
kind: schema
name: noun
carrier: dir
domain: [racecar]
summary: "the tier a command group sits at"
pnode: [README.md]
bearing: contract
---

# tier: `noun`

The first tier, and the recursive one: a directory holding a `README.md` that declares
`kind: noun`. Its children are the nouns and verbs of that command group.

`carrier: dir` makes nesting possible: `graph/topology/` is a noun inside a noun, so
`python -m racecar.graph.topology` is addressable.

**Position is the address.** `graph/topology/check.md` is
`python -m racecar.graph.topology check` because of where it sits, and the same path names
`src/racecar/graph/topology/`, which is what `scripts/lexicon.py` compares. What a noun owes
is declared in [`../ontology/noun.md`](../ontology/noun.md).
