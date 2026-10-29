"""The coverage table: what the lexicon declares beside what each route offers.

Part of `lib.lexicon`; `scripts/lexicon.py` is its command line.

Complexity: O(N*V), N = declared nouns, V = verbs each, over precomputed surfaces
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from lib.lexicon import _audit
from lib.lexicon._corpora import Lexicon
from lib.lexicon._eligible import eligible_nouns
from lib.lexicon._graph import Graph
from lib.lexicon._loop import Checked, matrix
from lib.lexicon._nodes import (
    DEFAULT_DOMAIN,
    _params_of,
    corpus_domain,
    declared_verbs,
    domains_of,
    root_noun,
    verb_node,
)
from lib.shared._as_json import FORM_PARAMS
from lib.shared._root import package_dir


def site_noun(chain: tuple[str, ...], pkg: Path | None, noun: str) -> str:
    """The noun as `_audit.flag_sites` spells it in a site: `api`, never `racecar.api`.

    `flag_sites` strips the package prefix and `cli_verbs` keeps it; this is the one
    home for the strip. The root's own verbs are sited under the package's name
    (`racecar check`).
    """
    if chain:
        return ".".join(chain)
    return pkg.name if pkg else noun


def long_flags_at(sites_by_flag: dict[str, set[str]], site: str, verb: str) -> set[str]:
    """The LONG flags `flag_sites` records at `site verb`, spelled without their dashes.

    Long only, so `-v` beside `--verbose` is not counted twice.
    """
    return {
        flag[2:]
        for flag, sites in sites_by_flag.items()
        if flag.startswith("--") and f"{site} {verb}" in sites
    }


def status_rows(
    lexicon: Lexicon, root: Path, selected: list[str]
) -> list[dict[str, Any]]:
    """Every `(domain, noun, verb, params)` row, marked declared and implemented.

    The one source for the table, `list --json` and `check --json`. Rows are the union
    of the lexicon and the code, so code with no node shows too.
    """
    pkg = package_dir(root)
    # No `except LexiconError`: an unreadable tree is a refusal (`_audit.cli_tree`), not
    # an empty one.
    offered = _audit.cli_verbs(root) if _audit.has_cli(root) else {}
    declared_flags = _audit.flag_sites(root) if _audit.has_cli(root) else {}
    wanted = set(selected)

    rows_out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for chain, node in sorted(eligible_nouns(lexicon, selected).items()):
        # `eligible_nouns` already filtered by `selected`; this picks which of the
        # node's domains labels the row.
        doms = domains_of(node) or [DEFAULT_DOMAIN]
        # The empty chain is the root package, named for the package.
        noun = ".".join(chain) or root_noun(lexicon)
        dotted = ".".join((pkg.name, *chain)) if pkg else noun
        # Both routes, unioned: `src/<pkg>/<noun>/` (the CLI audit) and a delivered
        # `scripts/<noun>.py` (flat, so a top-level noun only). A noun with both has
        # them merged; a disagreement between them is not detected here.
        from_script = _audit.script_surface(root, noun) if len(chain) == 1 else {}
        implemented = offered.get(dotted, set()) | set(from_script)
        for verb in sorted(declared_verbs(lexicon, chain) | implemented):
            seen.add((noun, verb))
            # `verb_node` is the one home for the path, root noun included.
            node_for = verb_node(lexicon, noun, verb)
            declared_params = set(_params_of(node_for)) if node_for.exists() else set()
            live_params = long_flags_at(
                declared_flags, site_noun(chain, pkg, noun), verb
            ) | from_script.get(verb, set())
            # The row's domain is the verb's, not the noun's: a borrowed noun (`host` is
            # `[ansible, racecar]`) can carry a racecar-only verb.
            verb_doms = domains_of(node_for) if node_for.exists() else []
            label = sorted(set(verb_doms or doms) & wanted) or sorted(
                set(doms) & wanted
            )
            rows_out.append(
                {
                    "domain": label[0],
                    "noun": noun,
                    "verb": verb,
                    "params": sorted(declared_params | live_params),
                    "in_graph": node_for.exists(),
                    "implemented": verb in implemented,
                    "params_in_graph": sorted(declared_params),
                    "params_implemented": sorted(live_params),
                }
            )

    # Implemented nouns the lexicon never declared.
    for dotted, verbs_here in sorted(offered.items()):
        if not pkg or not dotted.startswith(f"{pkg.name}."):
            continue
        noun = dotted[len(pkg.name) + 1 :]
        for verb in sorted(verbs_here):
            if (noun, verb) in seen:
                continue
            rows_out.append(
                {
                    # The corpus's own domain: this repo implements the verb and
                    # never declared it.
                    "domain": corpus_domain(lexicon),
                    "noun": noun,
                    "verb": verb,
                    "params": sorted(long_flags_at(declared_flags, noun, verb)),
                    "in_graph": False,
                    "implemented": True,
                    "params_in_graph": [],
                    "params_implemented": sorted(
                        long_flags_at(declared_flags, noun, verb)
                    ),
                }
            )
    return sorted(rows_out, key=lambda r: (r["noun"], r["verb"]))


class Reach(NamedTuple):
    """One noun's counts: what the LEXICON declares, and what each route actually carries.

    `bin` is `scripts/<noun>.py`; `cli` is `python -m <pkg>.<noun>`. `None` means the
    route is absent, which is not zero.
    """

    noun: str
    verbs: int
    verbs_bin: int | None
    verbs_cli: int | None
    params: int
    params_bin: int | None
    params_cli: int | None


def reach(lexicon: Lexicon, root: Path, selected: list[str]) -> list[Reach]:
    """Per noun, the lexicon's counts beside each route's."""
    pkg = package_dir(root)
    # Refused rather than emptied, for the reason `status_rows` states.
    offered = _audit.cli_verbs(root) if _audit.has_cli(root) else {}
    flags = _audit.flag_sites(root) if _audit.has_cli(root) else {}
    verb_args = _audit.cli_args(root) if _audit.has_cli(root) else {}
    out: list[Reach] = []

    for chain in sorted(eligible_nouns(lexicon, selected)):
        noun = ".".join(chain) or root_noun(lexicon)
        dotted = ".".join((pkg.name, *chain)) if pkg else noun
        site = site_noun(chain, pkg, noun)

        declared = declared_verbs(lexicon, chain)
        want_params: set[str] = set()
        for verb in declared:
            want_params |= set(_params_of(verb_node(lexicon, noun, verb)))

        script = _audit.script_surface(root, noun) if len(chain) == 1 else {}
        has_bin = bool(script) or (root / "scripts" / f"{noun}.py").is_file()
        bin_params: set[str] = set()
        for got in script.values():
            bin_params |= got

        cli_here = offered.get(dotted)
        # Per verb, never the node's own flags (`--root` on `racecar.arch` belongs to no
        # verb node).
        verbs_here = (cli_here or set()) | set(script)
        # `flag_sites` keys by the noun without the package prefix, so match on `site`.
        cli_params = {
            flag[2:]
            for flag, sites in flags.items()
            if flag.startswith("--") and any(f"{site} {v}" in sites for v in verbs_here)
        }
        # A positional is a param too, spelled by its dest; it has no `--` site to find.
        cli_params |= {
            str(arg["dest"]).replace("_", "-")
            for (module, verb), args in verb_args.items()
            if module == dotted and verb in verbs_here
            for arg in args
            if not arg.get("flags")
        }
        out.append(
            Reach(
                noun,
                len(declared),
                len(script) if has_bin else None,
                len(cli_here) if cli_here is not None else None,
                len(want_params - FORM_PARAMS),
                len(bin_params - FORM_PARAMS) if has_bin else None,
                len(cli_params - FORM_PARAMS) if cli_here is not None else None,
            )
        )
    return out


def document(g: Graph, run: Checked) -> dict[str, Any]:
    """`check --json`: `{domains, rows, nouns, findings, answers}` for both routes.

    `rows` are what `list --json` emits, with both verdicts; `nouns` are the coverage
    table's per-noun counts; `findings` are `{severity, subject, rule, message}`
    records; `answers` is every tuple against every check. `run` is what `_loop.checked`
    returned for `g`.
    """
    selected = list(g.selected)
    return {
        "domains": selected,
        "rows": [
            {
                "domain": r["domain"],
                "noun": r["noun"],
                "verb": r["verb"],
                "params": r["params"],
                "in_graph": r["in_graph"],
                "implemented": r["implemented"],
            }
            for r in status_rows(g.lexicon, g.root, selected)
        ],
        "nouns": [
            {
                "noun": n.noun,
                "verbs": n.verbs,
                "verbs_bin": n.verbs_bin,
                "verbs_cli": n.verbs_cli,
                "params": n.params,
                "params_bin": n.params_bin,
                "params_cli": n.params_cli,
            }
            for n in reach(g.lexicon, g.root, selected)
        ],
        "findings": run.findings,
        "answers": matrix(run.answers, g.root),
    }
