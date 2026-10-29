---
name: graph
kind: schema
domain: [racecar]
summary: "one graph a repo declares: what its nodes are, what an edge means, who reads it"
pnode: [README.md]
bearing: contract
required: [node, edge, source, reader]
---

# `kind: graph`

One graph a repo declares. A repo has several — a doc graph, an import graph, an axiom
graph — and which ones it has is a fact about that repo. An adopter who declares none gets
an empty answer.

## Why this kind is delivered

Each repo declares its own set of graphs, so no generator ships its author's list.

## The four required fields

`node:` and `edge:` say what ONE node is and what ONE edge means — "contract" and
"`import`" for the import graph. They are the column headings of any rendering.

`source:` is where the graph is read from: a path, or a glob.

`reader:` names the code that counts it, as `module:function`, declared rather than derived
from `name:`.

## What is NOT declared, and why

**The counts.** How many nodes and edges is the reader's return, read from the artifact that
owns it.

**Whether the edges are countable.** A graph whose edges nothing declares — cross-references
in prose, say — reports `not declared`, which differs from zero. The reader decides that.

## `edges:` is correspondence, not derivation

A graph may declare `edges:` — the other graphs it is CHECKED AGAINST, each with the file
that implements the check and what that check asserts. It is not a derivation edge and
implies no build order.

This node carries no relative links, because it must resolve both here and at
`.racecar/docs/lexicon/ontology/` in an adopter. Edges point backward, like every other edge
in a racecar graph: a node says what it is checked against, never what checks it.

An `against:` naming something that is no declared graph is legal — a README is not one. The
claim is closed by the implementing file existing.
