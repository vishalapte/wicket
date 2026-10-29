---
name: racecar-lexicon
summary: >-
  The half of the lexicon racecar DELIVERS, authored here and landing at
  `.racecar/docs/lexicon/` in a governed repo. Every markdown file under here is canon
  racecar owns and overwrites there, the same contract the delivered checkers travel
  under; the repo's own `docs/lexicon/` is authored and is never touched.
pnode: [../../README.md]
bearing: contract
---

# `docs/rc_lexicon` — the half of the lexicon that travels

`docs/lexicon/` is racecar's own corpus, graded by `scripts/lexicon.py` and
`python -m racecar.graph`. This tree is the part of it that is the same in every governed
repo, so racecar ships it.

**Source and destination differ.** Here it is `docs/rc_lexicon/`; `rc_` marks the copy that
travels. In an adopter it lands at `.racecar/docs/lexicon/`. Everything under `.racecar/` is
racecar's, overwritten on sync, never hand-edited, and its layout mirrors the repo's own. The
delivered checkers have the same shape: authored in `scripts/`, received as `.racecar/scripts/`.

**The join is by position.** `docs/lexicon` and `.racecar/docs/lexicon` are the pair, and each
is skipped when it is not on disk. A repo that needs a third corpus declares it at
`[tool.racecar.lexicon] corpora` in `pyproject.toml`. The readers know both spellings of the
delivered corpus — `.racecar/docs/lexicon` and `docs/rc_lexicon` — so racecar joins its own
canon by position too.

**The order is the resolution rule, and canon is LAST.**

    custom  >  the repo's own `docs/lexicon`  >  this corpus

A declared corpus outranks everything, the repo's own corpus comes next, and canon supplies
only what nothing local says. A repo can therefore narrow a delivered kind's `required:`, and
the narrower list wins. Both readers print which copy was shadowed.

**The delivered copy carries its own provenance.** A sync writes `.racecar/.racecar-lexicon`
beside the corpus, holding git's tree object id for what it delivered — reproducible with
`git rev-parse <ref>:docs/rc_lexicon` in a racecar checkout. A reader that finds a stamp
disagreeing with the tree leaves the delivered corpus out of the join and says so. No stamp
means trust it.

The order of operations is the same for all three declarations: join the corpora, generate
the ontology / topology / graph from the union, then operate on that. The join is built for
the ontology and the topology; the graph's data walk still reads one tree.

A file here is graded as a document like any other — links resolve, `pnode` reaches the
root. Why it is delivered is recorded in `src/racecar/lib/delivery/catalogue.py`. In the
adopter the checkers skip hidden directories, so `_files.CONTENT_DIRS` names `.racecar` as
content.
