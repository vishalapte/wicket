---
name: racecar-boundary
summary: >-
  Everything racecar owns in this repository, and nothing else. Every file under here is
  canon racecar delivered, overwritten by the next sync and never hand-edited; the repo's
  own trees are elsewhere and are never touched.
pnode: [../README.md]
bearing: contract
---

# `.racecar` — the boundary

One directory for everything racecar owns in a governed repo. What is under here is racecar's:
it arrived by `racecar-sync`, it is overwritten by the next one, and an edit to it is work you
lose rather than a local override. What is outside is yours, and racecar never writes to it.

`scripts/` holds the delivered checkers, flat, each importing nothing but the standard library
and its siblings here. Run one as a script, `python3 .racecar/scripts/<name>.py`, and Python
puts this folder on its path, so the siblings import. To import one from your own code, put
`.racecar/scripts` on `sys.path` first: loading the file by its path alone does not, and its
first sibling import fails with `No module named 'lib'`. `docs/lexicon/` holds the half of the
lexicon that is the same in every governed repo — a noun is a command group, a verb is a
command, a param is a flag, and the two tiers they sit at — joined with this repo's own
`docs/lexicon/` by position, with your declarations winning wherever they overlap.

**This file is why the boundary has an index at all, and it is load-bearing rather than
decorative.** The delivered corpus's own README declares `pnode: [../../README.md]`, and that
one relative path has to resolve at BOTH ends of the delivery: from `docs/rc_lexicon/` in
racecar it reaches the repo root, and from `.racecar/docs/lexicon/` here it reaches this file.
Without it the delivered tree hangs off nothing — an unresolvable edge in your doc graph and an
orphan in your placement check, in files you did not write and cannot fix, because the next sync
overwrites them.

Beside the two trees sit the four files a sync writes ABOUT the delivery rather than delivering:
`.racecar-delivered.txt` (what landed, which is what lets a later sync retire what racecar has
stopped shipping), `.racecar-version` (the racecar ref it came from), and `.racecar-scripts` and
`.racecar-lexicon` (git's own tree id for each delivered tree, so `git rev-parse <ref>:<tree>`
here answers whether these are still the bytes racecar shipped). They sit outside the trees
because a file written into a tree changes the sha that records it.
