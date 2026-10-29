"""What a command acts on: the domains it selects and the nouns eligible in them.

Part of `lib.lexicon`. A command builds each of these lists once, where its arguments are
parsed, and hands them on; nothing downstream rebuilds them.
"""

from __future__ import annotations

from pathlib import Path

from lib.lexicon._corpora import DELIVERED, Lexicon
from lib.lexicon._nodes import (
    corpus_domain,
    declared_domains,
    declared_nouns,
    domains_of,
)
from lib.shared import _frontmatter


def in_selection(declared: list[str], selected: list[str], domain: str) -> bool:
    """Whether a node declaring `declared` domains is in this run's selection as `domain`.

    A node declaring no domain is in every selection, so it can be reported.
    """
    return not (declared and selected and domain not in selected)


def reserved_nouns(lexicon: Lexicon) -> frozenset[str]:
    """The nouns the delivered lexicon reserves: `kind: noun` with `status: reserved`.

    A reserved noun is not eligible and no repo may declare it. It binds a repo that
    receives the delivered lexicon (`.racecar/docs/lexicon/`), not racecar, which
    authors it (`docs/rc_lexicon/`).
    """
    return frozenset(
        str(_frontmatter.load(lexicon.path(entry)).get("name")).strip('"')
        for entry in lexicon.nodes(origins=frozenset({DELIVERED}), kind="noun")
        if entry.filename == "README.md"
        and len(entry.parts) == 1
        and _frontmatter.load(lexicon.path(entry)).get("status") == "reserved"
    )


def eligible_nouns(
    lexicon: Lexicon, selected: list[str]
) -> dict[tuple[str, ...], Path]:
    """The nouns a run acts on: `declared_nouns` in the `selected` domains, less the reserved.

    `selected` is required, so no reader grades another domain's projection by
    forgetting to filter; `eligible_domains` supplies it. A noun declaring no domain is
    in every selection (`in_selection`).
    """
    reserved = reserved_nouns(lexicon)
    found: dict[tuple[str, ...], Path] = {}
    for chain, node in declared_nouns(lexicon).items():
        if chain and chain[0] in reserved:
            continue
        declared = domains_of(node)
        if any(in_selection(declared, selected, domain) for domain in declared or [""]):
            found[chain] = node
    return found


def eligible_domains(
    lexicon: Lexicon, requested: list[str] | None = None, *, every: bool = False
) -> list[str]:
    """The domains a run acts on: all (`every`), `requested`, or the corpus's own.

    A requested domain with no entries is not an error; it holds nothing. The default is
    the corpus's own domain, read from its root node, never the literal `racecar`.
    """
    if every:
        return declared_domains(lexicon)
    if requested is not None:
        return list(requested)
    return [corpus_domain(lexicon)]
