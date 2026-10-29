---
name: racecar-lexicon-topology
summary: >-
  The index this directory's tier nodes hang from, and nothing else. Identity and a
  parent: the corpus-level statements belong to the corpus, and the corpus is the
  adopter's.
pnode: [../README.md]
bearing: contract
---

# The delivered tiers

Two tier nodes — [`noun`](noun.md) and [`verb`](verb.md) — each carrying its own
`carrier:`. Together they are the containment rule every governed repo's CLI obeys:

    a noun may contain nouns and verbs; a verb contains nothing.

They travel for the same reason `noun`, `verb` and `param` travel in
[`../ontology/`](../ontology/README.md): the shape is the same in every repo racecar governs.

**The order is the `pnode` chain.** `noun.md` takes its `pnode` from this index and `verb.md`
from `noun.md`, so reading down gives `noun -> verb` without reaching into the joining corpus.

This index declares no `root_pattern`, `partition` or `buckets:`, as
[`../ontology/README.md`](../ontology/README.md) declares no corpus keys: those are statements
about a corpus. racecar's own `docs/lexicon/topology/README.md` carries them for racecar's
corpus, and an adopter's index for the adopter's.
