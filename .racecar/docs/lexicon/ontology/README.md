---
name: racecar-lexicon-ontology
summary: >-
  The index this directory's kind nodes hang from, and nothing else. Identity and a
  parent: the corpus-level statements belong to the corpus, and the corpus is the
  adopter's.
pnode: [../README.md]
bearing: contract
---

# The delivered kinds

Three kind nodes — [`noun.md`](noun.md), [`verb.md`](verb.md), [`param.md`](param.md) —
each naming a kind in its own `name:` and what that kind owes in its own `required:`. A
corpus picks them up because `.racecar/docs/lexicon` is one of the two corpora that join by
position. In racecar itself, `[tool.racecar.lexicon] corpora` names this directory instead.

This index declares no `discriminator`, `partition`, `base` or `kinds:` block: those are
statements about a corpus, and this directory is three nodes a corpus may join in. The
joining corpus's own `ontology/README.md` carries them.

So the fields a kind node here names — `name`, `kind`, `domain`, `summary`, `pnode`,
`bearing` — are the joining corpus's `base`, not declared as required here.
