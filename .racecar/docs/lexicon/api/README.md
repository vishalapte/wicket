---
name: api
kind: noun
domain: [racecar]
status: reserved
summary: the layer a noun's commands enter its code through
pnode: [../README.md]
bearing: contract
---

# api

`api` names one thing in every racecar package: the layer a noun's commands enter its code
through, the `api.py` beside that noun's `__main__.py`.

**Reserved.** racecar owns the word. A repo that receives this lexicon may not declare `api`
as a noun of its own, so `lexicon create` and `surface create` refuse it, and the lexicon's
list of nouns leaves it out.

racecar's commands on that layer: `python -m racecar.api check` reports each surface whose
dispatch routes around `api`, and `fix` plans the repair. Their verb nodes sit in racecar's
own `docs/lexicon/api/`, where this node is the same file.
