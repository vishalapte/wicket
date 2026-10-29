---
name: verb
kind: schema
domain: [racecar]
summary: "a command: what it takes, and where its binding to code is written"
pnode: [README.md]
bearing: contract
required: [params]
---

# `kind: verb`

A command — one word run against a [noun](noun.md), living at `<noun>/<verb>.md` because
position is the address: `graph/topology/check.md` is
`python -m racecar.graph.topology check`.

## `params:`, and why it is the one required field

A verb owes `params:`: the flags it declares, as spellings, in one list. `scripts/lexicon.py`
compares the list against what argparse builds for that command, so a node that drifts from
its code is a finding.

The list is spellings only; what each spelling means is [`param`](param.md)'s job.

A verb that ships nothing is exempt: the `status:`/`instead:` exemption suspends every kind's
`required:`. `verb/lint.md` is reserved rather than implemented.

## `invocations:`, optional: the command lines that exercise the verb

A verb may declare the command lines a before-and-after comparison runs, beside its
`params:`: one entry per line, the arguments after the verb as a string, or a mapping with
`args`, `before` (the noun's own flags, which go ahead of the verb, such as `--root`) and
`fixture` (a repo-relative directory inside the tree's copy that the line runs from).

    invocations: ["", "--json", {before: "--root examples", args: ""}]

It is authored apart from the code it exercises (P-07). Every entry of a built verb runs, in
its own copy of the tree, with a scratch home and no credentials in its environment, so a
`write` line writes only into the copy. The parser's own lines — the bare node, `-h`, an
unknown verb and flag, `<verb> -h` — are derived and never declared. `python3
.racecar/scripts/surface.py check` reports a built `read` or `write` verb with no entry, and
`update` proposes one from its parser for the author to prune. A `job` verb owes none.
`surface create` writes `invocations: [""]` on each `read` or `write` verb it records that
declares none.

## A verb's binding is not in this corpus at all

It is a module-level `VERBS = {...}` dict in the noun's `api.py`, mapping the CLI spelling to
the function that runs it. As a dict key, a word that Python cannot bind — `list` shadows a
builtin, `import` is a keyword — is plain data.

    # src/acme/store/api.py
    from acme.store.lib._import import fan_out

    VERBS = {"import": fan_out}

The map is optional; a repo may bind some verbs through it and the rest by name. Its keys
must be string literals: `unreadable_verb_maps` reports a `VERBS` the checker cannot read.
`VERBS = {}` is legal and is not a finding.

## The second map, which racecar does not read

A repo whose CLI dispatches by table rather than by argparse subparsers carries a second map,
private to the leaf:

    # src/acme/store/__main__.py
    _KINDS = {"create": "write", "build": "write", "report": "read", "list": "read"}

    _VERBS = {
        "build": _build.main,
        "create": _create.main,
        "list": _list.main,
        "report": _report.main,
    }

`api.VERBS` is the exposed surface every other surface reaches the noun through, and it is
the one graded. `_VERBS` is how that repo's command line reaches it, private to the leaf.

`_KINDS` marks which verbs write. racecar holds that fact in `surface.jsonl`, which gives
every row a `kind` of `read`, `write` or `job`; this node does not grade `_KINDS`.

## racecar declares no `VERBS` map of its own, and still shadows the builtin

`src/racecar/lexicon/api.py` binds `list` directly, with a `redefined-builtin` suppression
and no annotations in that file. `surface.jsonl` binds `lexicon.list` by name, and that file
is owner-authorized, so the binding stays until the owner changes it.
