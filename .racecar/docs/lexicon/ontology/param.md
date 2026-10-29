---
name: param
kind: schema
domain: [racecar]
summary: "a param a verb takes, fixed once on a node of its own"
pnode: [README.md]
bearing: contract
required: [type]
---

# `kind: param`

A flag, fixed once for every verb that takes it.

## `type:`, and why it is the one required field

A param owes `type:` — `path`, `flag`, `string`, and so on. It is checked against the
`dest`/`type`/`action` the argparse tree builds, so the node either agrees with the code or
produces a finding.

## `required:`, `position:` and `defined:`, and what their absence means

Three optional fields, each with a default a node takes by omitting it:

| field | type | default | says |
|---|---|---|---|
| `required` | boolean | `false` | the verb refuses to run without it |
| `position` | list of `<noun>.<verb>` | `[]` | the verbs that take it by position rather than as a `--flag` |
| `defined` | boolean | `false` | a person has written its type, `required` and `position` |

`required` and `position` are checked like `type`: the node either agrees with every verb
that takes the word or `check` reports the verb it disagrees with (`param-use`). A word
required in one verb and optional in another is reported.

`defined` is not checked against the code. `create` writes a node for each param it declares
with `type: string`, `required: false`, `position: []` and `defined: false`, and the code
built from it is a stub. `check` lists every `defined: false` param until a person sets them.

## A param's meaning is a node, not a field

A param is fixed once, at `<corpus>/param/<name>.md`, and a verb's `params:` list holds
spellings pointing at that bucket.

    # docs/lexicon/param/data.md
    ---
    name: data
    kind: param
    domain: [acme]
    type: path
    summary: the corpus a command reads
    pnode: [README.md]
    bearing: contract
    ---

The bucket's own index says which flags earn a node. Which verbs use a word is a relationship
between two nodes, so this file cannot require it.
