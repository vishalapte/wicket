"""The `shipped` rule: a word this repo's CLI ships, on a node that disowns its domain.

Part of `lib.lexicon`, the package `scripts/lexicon.py` is a command line over. Its own
module because its subject is a node rather than a tuple.

Complexity: O(P + N), P = nodes swept once, N = nouns in the one cached CLI audit
"""

from __future__ import annotations

from lib.lexicon._graph import Answer, Finding, Graph, undeclared
from lib.lexicon._nodes import (
    DEFAULT_DOMAIN,
    OWN_ONLY,
    corpus_domain,
    domains_of,
)
from lib.shared import _frontmatter
from lib.shared._root import package_dir


def shipped_words(g: Graph) -> dict[str, frozenset[str]]:
    """Every word this repo's CLI ships, as `{dotted noun: the verbs under it}` plus the nouns.

    Read from the CLI audit rather than from `addressable()`, which holds nouns but no verbs.
    """
    # Both sides are made package-relative before they meet: `g.cli` keys a noun by its full
    # dotted module path (`racecar.lexicon`), `g.addressable` by its chain under the package
    # (`('lexicon',)`).
    pkg = package_dir(g.root)
    prefix = f"{pkg.name}." if pkg else ""
    out: dict[str, frozenset[str]] = {}
    for noun, verbs in g.cli.items():
        relative = noun[len(prefix) :] if prefix and noun.startswith(prefix) else noun
        out[relative] = frozenset(verbs)
    for chain in g.addressable:
        if chain:
            out.setdefault(".".join(chain), frozenset())
    return out


def shipped_findings(g: Graph) -> list[Answer]:
    """Does any node disown this repo's domain while this repo's CLI ships its word?

    Borrowing a word into racecar's surface makes racecar one of its fixers, whatever its
    provenance. A sweep of the corpus, not a per-tuple check: the borrowed words sit in
    the kind buckets (`param/`, `rule/`, `verb/`), which no tuple reaches.
    """
    if not g.partitioned or DEFAULT_DOMAIN not in (g.selected or (DEFAULT_DOMAIN,)):
        return []
    # The words to compare against are racecar's, not this repo's. An adopter cannot know
    # racecar's vocabulary (its lexicon is not delivered), so outside racecar this returns
    # no findings, as `_cli_gap` does for the root noun.
    if corpus_domain(g.lexicon) != DEFAULT_DOMAIN:
        return []
    shipped = shipped_words(g)
    out: list[Answer] = []
    for entry in g.lexicon.nodes(origins=OWN_ONLY):
        node = g.lexicon.path(entry)
        mine = domains_of(node)
        if not mine or DEFAULT_DOMAIN in mine:
            continue
        name = str(_frontmatter.load(node).get("name") or node.stem).strip('"')
        chain = entry.parts
        dotted = ".".join(chain)
        # A node under a noun is graded against that noun's verbs. Three ways the CLI can
        # ship this word; the first is the chain term: a word addressed as its own node.
        hit = ".".join((*chain, name)) in shipped
        # A verb: the word is a subcommand of the noun its node sits under.
        hit = hit or (bool(chain) and name in shipped.get(dotted, frozenset()))
        # A noun's own README, named for the directory it sits in.
        hit = hit or (bool(chain) and name == chain[-1] and dotted in shipped)
        # A node with no chain is graded by the bare-name term.
        hit = hit or (not chain and name in shipped)
        # A verb-bucket node, for a word racecar ships as a verb of some noun.
        # `param/host.md` fixes the flag `--host` and must not match.
        hit = hit or (
            entry.kind == "verb" and any(name in verbs for verbs in shipped.values())
        )
        if not hit:
            continue
        out.append(
            Answer(
                "",
                undeclared(DEFAULT_DOMAIN, dotted or name, name, g.lexicon),
                (),
                (
                    Finding(
                        str(node),
                        f"`domain: {mine}` omits `{DEFAULT_DOMAIN}`, but the CLI ships "
                        f"`{name}`. Shipping a borrowed word makes racecar one of its fixers.",
                        "",
                    ),
                ),
            )
        )
    return out
