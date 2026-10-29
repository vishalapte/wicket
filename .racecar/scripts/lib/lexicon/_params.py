"""The param checks: what a param's node says about how each verb takes it.

Part of `lib.lexicon`. A param node may carry `required`, `defined` and `position`, each
defaulted where absent (`PARAM_DEFAULTS`). `param-use` grades the first and last against what
argparse builds; `defined` reports every node a `create` wrote and no person has set.

Complexity: O(P) per tuple in its params, each a lookup in the per-verb arguments read once
"""

from __future__ import annotations

from typing import Any

from lib.lexicon._graph import Answer, Finding, Graph, Row, answer
from lib.lexicon._nodes import root_noun


def _argument(row: Row, g: Graph, flag: str) -> dict[str, Any] | None:
    """The argument argparse builds for `flag` in this tuple's verb, or None."""
    if g.pkg is None or row.verb is None:
        return None
    chain = tuple(row.noun.split(".")) if row.noun != root_noun(g.lexicon) else ()
    module = ".".join((g.pkg.name, *chain))
    dest = flag.replace("-", "_")
    for arg in g.verb_args.get((module, row.verb), ()):
        spelled = arg.get("flags") or []
        if f"--{flag}" in spelled or (not spelled and arg.get("dest") == dest):
            return arg
    return None


def check_param_use(row: Row, g: Graph) -> list[Answer]:
    """Does each param's node say how this verb takes it: required, and flag or positional?

    `required` and `position` are checked against what argparse builds, as `type:` is. One
    node serves every verb that takes the word, so a word taken two ways (required here,
    optional there) is reported.
    """
    out: list[Finding] = []
    agreed: list[str] = []
    verb_id = f"{row.noun}.{row.verb}"
    for flag in g.attrs[row].params:
        node = g.flag_local.get(flag) or g.flag_canon.get(flag)
        arg = _argument(row, g, flag)
        if node is None or arg is None:
            continue
        positional = not arg.get("flags")
        required = bool(arg.get("required"))
        wrong = []
        if node.required != required:
            wrong.append(
                f"`required: {str(node.required).lower()}` but {verb_id} "
                f"{'requires' if required else 'does not require'} it"
            )
        if (verb_id in node.position) != positional:
            wrong.append(
                f"{verb_id} takes it {'by position' if positional else 'as a flag'} but "
                f"`position:` {'omits' if positional else 'lists'} {verb_id}"
            )
        if wrong:
            out.append(Finding(node.where, f"{flag}: " + "; ".join(wrong), "param-use"))
        else:
            agreed.append(flag)
    return answer(row, agreed, out)


def check_defined(row: Row, g: Graph) -> list[Answer]:
    """Is every param this verb takes defined by a person, or still as a `create` wrote it?

    A `create` cannot know a param's type, whether it is required or whether it is
    positional, so it writes `defined: false` with a string type and the defaults, and the
    code built from that is a stub. This is the list an author works from.
    """
    out: list[Finding] = []
    agreed: list[str] = []
    for flag in g.attrs[row].params:
        node = g.flag_local.get(flag) or g.flag_canon.get(flag)
        if node is None:
            continue
        if node.defined:
            agreed.append(flag)
            continue
        out.append(
            Finding(
                node.where,
                f"{flag}: `defined: false` -- written by `create`, so its type, whether it is "
                "required and whether it is positional are guesses. Set them, then "
                "`defined: true`.",
                "defined",
            )
        )
    return answer(row, agreed, out)
