"""The checks: one loop over the list of tuples, n methods, both directions.

Part of `lib.lexicon`, the package behind `scripts/lexicon.py`. The tests, the `lexicon`
noun's api and the delivered CLI import these functions directly.

Complexity: O(1) per tuple per check -- every read the checks need is on the Graph
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from lib.lexicon import _audit
from lib.lexicon._audit import (
    _DISTINGUISHABLE,
    _EXPECTED,
    _cli_gap,
    _well_formed,
    verb_gap,
)
from lib.lexicon._corpora import OWN, Lexicon
from lib.lexicon._graph import (
    Answer,
    Finding,
    Graph,
    Row,
    answer,
    footprint,
    undeclared,
    word_node,
)
from lib.lexicon._nodes import (
    API_NAMES,
    DEFAULT_DOMAIN,
    ONTOLOGY_REL,
    OWN_ONLY,
    _has_prose,
    _sections,
    domains_of,
    ontology_path,
    root_noun,
)
from lib.lexicon._terms import instead_disagrees
from lib.shared import _frontmatter, _markdown
from lib.shared._files import repo_files
from lib.shared._root import package_root

#: What a declared noun with no code is told. `racecar.surface` is not delivered, so the
#: skill is named too.
ADVICE = (
    "Declared, and no code yet: `python -m racecar.surface create --surface cli --noun "
    "{noun}` (the `/racecar-surface` skill) builds it, or remove the declaration."
)


def nouns_of(g: Graph) -> dict[tuple[str, ...], Path]:
    """`{chain: the README that declares it}` for every noun row in the list of tuples."""
    root = root_noun(g.lexicon)
    return {
        () if row.noun == root else tuple(row.noun.split(".")): row.node
        for row in g.rows
        if row.verb is None
    }


def findings(g: Graph) -> list[Finding]:
    """Grade the package tree against the nouns the list names; return the findings.

    Writes nothing. Each named noun must reach code, and each vertical must have an api.
    The per-verb half is `check_implemented`.
    """
    out: list[Finding] = []

    if not g.kinds:
        return [
            Finding(
                str(ontology_path(g.lexicon)),
                "no ontology declared. The nounspace cannot be graded against a set of "
                "directory names; declare `kinds:` here.",
                "implemented",
            )
        ]

    pkg = g.pkg
    if pkg is None:
        out.append(
            Finding(
                str(package_root(g.root)),
                "no single package found; cannot place a vertical.",
                "implemented",
            )
        )
        return out

    declared = nouns_of(g)
    if not declared:
        out.append(
            Finding(
                str(g.lexicon.own),
                "no noun declares itself. The nounspace is empty.",
                "implemented",
            )
        )

    for chain in sorted(declared, key=lambda c: (len(c), c)):
        node = declared[chain].relative_to(g.root)
        module = pkg.joinpath(*chain)
        rel = module.relative_to(g.root)

        # A noun reaches code through a vertical, a script, or both; having neither is the
        # failure. The vertical's obligations hold even when a script exists.
        vertical = (module / "__init__.py").is_file()
        # A delivered script is addressed `<noun>`, flat, so it routes only a top-level noun.
        script = g.root / "scripts" / f"{chain[0]}.py" if len(chain) == 1 else None
        has_script = script is not None and script.is_file()

        if not vertical and not has_script:
            out.append(
                Finding(
                    str(node),
                    f"declares the noun {'.'.join(chain) or pkg.name!r}, which reaches "
                    f"no code: no vertical at {rel}"
                    + (
                        f" and no script at scripts/{chain[0]}.py"
                        if len(chain) == 1
                        else ""
                    )
                    + ". "
                    + ADVICE.format(noun=".".join(chain) or pkg.name),
                    "implemented",
                )
            )
            continue

        if (
            vertical
            and chain
            and not any((module / candidate).exists() for candidate in API_NAMES)
        ):
            out.append(
                Finding(
                    str(rel),
                    f"no {' or '.join(API_NAMES)}. Every surface reaches a noun through its "
                    "api, so a vertical without one can be reached by nothing.",
                    "implemented",
                )
            )

    # A package addressable as `python -m <pkg>.<noun>` that no node declares is reported by
    # `check_undeclared_verbs`.
    return out


def check_undeclared_verbs(g: Graph) -> list[Answer]:
    """A command the CLI offers that the list of tuples does not name.

    A set difference with the list as one operand. Only nouns the list already names are
    asked about; an undeclared noun is reported once, below, not per verb.
    """
    if g.pkg is None:
        return []
    root = root_noun(g.lexicon)
    domain = (g.selected or (DEFAULT_DOMAIN,))[0]
    declared: dict[str, set[str]] = {}
    for row in g.rows:
        # A noun's own row declares the noun and names no verb.
        named = declared.setdefault("" if row.noun == root else row.noun, set())
        if row.verb is not None:
            named.add(row.verb)
    out: list[Answer] = []
    for chain, named in sorted(declared.items()):
        parts = tuple(chain.split(".")) if chain else ()
        module = ".".join((g.pkg.name, *parts))
        offered = g.cli.get(module)
        if not offered:
            continue
        where = g.lexicon.own.joinpath(*parts)
        out += [
            Answer(
                "",
                undeclared(domain, chain or root, verb, g.lexicon),
                (f"python -m {module} {verb}",),
                (
                    Finding(
                        f"{where}/{verb}.md",
                        f"`python -m {module} {verb}` is offered and has no node. Either "
                        "the command should not exist, or the lexicon should name it — "
                        f"`lexicon create --tuple {domain}/{chain or root}/{verb}`.",
                        "",
                    ),
                ),
            )
            for verb in sorted(offered - named)
        ]

    # A package addressable as `python -m <pkg>.<noun>` that no node declares a noun.
    out += [
        Answer(
            "",
            undeclared(domain, child.name, None, g.lexicon),
            (f"python -m {g.pkg.name}.{child.name}",),
            (
                Finding(
                    str(child.relative_to(g.root)),
                    f"addressable as `python -m {g.pkg.name}.{child.name}` but no "
                    f"`{child.name}/README.md` declares it a noun.",
                    "",
                ),
            ),
        )
        for child in sorted(g.pkg.iterdir())
        if (child / "__main__.py").is_file() and child.name not in declared
    ]
    return out


def check_undeclared_flags(g: Graph) -> list[Answer]:
    """A flag used by more than one command, with no node fixing what it means.

    `check_flags` walks the nodes, so it never sees a flag with no node. A flag used at
    one site is that command's own option and is skipped. Short flags are exempt.
    """
    documented = set(g.flag_canon) | set(g.flag_local)
    # No `__main__.py` means no flags in use; the audit cannot run without a package root.
    combined: dict[str, set[str]] = (
        {flag: set(sites) for flag, sites in _audit.flag_sites(g.root).items()}
        if _audit.has_cli(g.root)
        else {}
    )
    for flag, sites in _audit.script_flag_sites(g.root).items():
        combined.setdefault(flag, set()).update(sites)
    out: list[Answer] = []
    domain = (g.selected or (DEFAULT_DOMAIN,))[0]
    for flag, sites in sorted(combined.items()):
        if not flag.startswith("--") or flag[2:] in documented or len(sites) < 2:
            continue
        where = ", ".join(sorted(sites)[:3]) + ("..." if len(sites) > 3 else "")
        out.append(
            Answer(
                "",
                undeclared(domain, "param", flag[2:], g.lexicon),
                tuple(sorted(sites)),
                (
                    Finding(
                        f"--{flag[2:]}",
                        f"accepted by {len(sites)} commands ({where}) and no node fixes "
                        "what it means. A word two commands share can drift between them; "
                        f"add docs/lexicon/param/{flag[2:]}.md.",
                        "",
                    ),
                ),
            )
        )
    return out


ROW = "| [{label}]({href}) | {second} | {gloss} |"


def _indexed(lexicon: Lexicon, under: str) -> list[tuple[Path, dict[str, str]]]:
    """The repo's own nodes under `under`, each with its frontmatter, index excluded."""
    return [
        (lexicon.path(entry), _frontmatter.load(lexicon.path(entry)))
        for entry in lexicon.nodes(origins=OWN_ONLY, under=under)
        if entry.filename != "README.md"
    ]


def index_body(
    lexicon: Lexicon,
    under: str,
    second_key: str,
    indexed: list[tuple[Path, dict[str, str]]] | None = None,
) -> str:
    """Render a tree's index table from its nodes' frontmatter.

    Derived, never stored (`DOC_GRAPH.md`). Each row's gloss is the node's own `summary`.
    """
    table = []
    for path, meta in sorted(
        _indexed(lexicon, under) if indexed is None else indexed,
        key=lambda pm: (str(pm[1].get("kind") or ""), pm[0].stem),
    ):
        href = path.relative_to(lexicon.own / under).as_posix()
        second = (
            meta.get("kind", "?") if second_key == "kind" else meta.get(second_key, "?")
        )
        table.append(
            ROW.format(
                label=meta.get("name", path.stem),
                href=href,
                second=second,
                gloss=meta.get("summary", ""),
            )
        )
    return "\n".join(table)


def check_index(
    lexicon: Lexicon, under: str, second_key: str, write: bool
) -> list[Finding]:
    """Hold a tree's index table to its nodes, or rewrite it when `write`.

    Reads the directory, not the list of tuples, so orphan nodes are indexed too. Only a
    README carrying the INDEX markers is checked; a missing README is
    `check_doc_graph.py`'s finding. The regenerated block is computed once and used both
    to detect drift and as the text written.
    """
    index = lexicon.at(f"{under}/README.md".lstrip("/"), OWN)
    # Gathered once: the emptiness test and the table read the same nodes.
    indexed = _indexed(lexicon, under)
    if index is None or not indexed:
        return []
    readme = lexicon.path(index)
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- BEGIN INDEX -->", "<!-- END INDEX -->"
    if start not in text or end not in text:
        return []
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    header = f"| term | {second_key} | in one line |\n|---|---|---|"
    body = index_body(lexicon, under, second_key, indexed)
    want = f"{start}\n\n{header}\n{body}\n\n{end}"
    rebuilt = head + want + tail
    if rebuilt == text:
        return []
    if write:
        readme.write_text(rebuilt, encoding="utf-8")
        return []
    return [
        Finding(
            str(readme.relative_to(lexicon.root)),
            "index is stale against the nodes. Regenerate with `lexicon.py check "
            "--apply`; it is derived, not written.",
            "indexed",
        )
    ]


_COMMAND_IN_PROSE = re.compile(r"python -m ([a-z_][\w.]*)((?:\s+[a-z][\w-]*)*)")


def address_table(g: Graph) -> tuple[set[str], dict[str, set[str]]]:
    """`(dotted noun chains, noun -> its verbs)` for every address in the list of tuples.

    The root's chain is the empty string, as `python -m <pkg>` has nothing after the package.
    """
    root = root_noun(g.lexicon)
    nouns: set[str] = set()
    verbs: dict[str, set[str]] = {}
    for row in g.rows:
        chain = "" if row.noun == root else row.noun
        nouns.add(chain)
        known = verbs.setdefault(chain, set())
        if row.verb is not None:
            known.add(row.verb)
    return nouns, verbs


def command_prose_findings(g: Graph, exempt: tuple[str, ...] = ()) -> list[Finding]:
    """Every `python -m <pkg>…` written in prose, checked against the declared tree.

    A sub-noun takes a dot and a verb takes a space: `racecar graph.topology check` is
    `python -m racecar.graph.topology check` with the prefix removed. `check_docs.py`
    validates a cited path; this validates a cited command.
    """
    nouns, verbs = address_table(g)
    root = g.root
    # The package name, from the graph root's `name:`; the directory is `lexicon`.
    pkg = root_noun(g.lexicon)
    out: list[Finding] = []
    # `repo_files` holds the shared exclusions; `tests/slow/test_repo_files.py` fails on a
    # private walk.
    scanned = sorted(repo_files(root, "*.md")) + sorted(repo_files(root, "*.py"))
    for path in scanned:
        rel = path.relative_to(root).as_posix()
        if any(rel == e or rel.startswith(e) for e in exempt):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "python -m " not in text:
            continue
        for match in _COMMAND_IN_PROSE.finditer(text):
            dotted, rest = match.group(1), match.group(2).split()
            head, *tail = dotted.split(".")
            if head != pkg:
                continue
            # `addressed` is the dotted noun chain walked so far, as a string.
            addressed = ".".join(tail)
            if addressed and addressed not in nouns:
                continue
            for word in rest:
                candidate = f"{addressed}.{word}" if addressed else word
                if candidate in nouns:
                    line = text[: match.start()].count("\n") + 1
                    out.append(
                        Finding(
                            f"{rel}:{line}",
                            f"`{match.group(0).strip()}` addresses the sub-noun `{word}` "
                            f"with a space. A sub-noun takes a dot: "
                            f"`python -m {pkg}.{candidate} …`.",
                            "prose",
                        )
                    )
                    break
                if word not in verbs.get(addressed, set()):
                    break
                addressed = candidate if candidate in nouns else addressed
    return out


def check_stub(row: Row, g: Graph) -> list[Answer]:
    """Is any node this tuple reaches still a stub: frontmatter, headings, no prose?

    `--apply` scaffolds `verb/<word>.md` and `param/<word>.md`, so this reaches those
    nodes. The test is content, not a marker such as `bearing: draft`.
    """
    written = [str(p) for p in footprint(row, g) if _has_prose(p)]
    empty = [
        Finding(
            str(node),
            "stubbed — frontmatter only, no prose. What the word MEANS cannot be "
            "derived, so `--apply` did not write it.",
            "stub",
        )
        for node in footprint(row, g)
        if not _has_prose(node)
    ]
    return answer(row, written, empty)


def check_domain(row: Row, g: Graph) -> list[Answer]:
    """Does every node this tuple reaches declare who fixes its word, and section it per domain?

    A node's domains must be a subset of its parent's, so each projection stays a rooted
    DAG. Only a leaf owes a `## <domain>` section per domain; a container spans domains
    because its children do.
    """
    if not g.partitioned:
        return answer(row, [], [])
    out: list[Finding] = []
    for node in footprint(row, g):
        rel = str(node)
        mine = domains_of(node)
        if not mine:
            out.append(
                Finding(
                    rel,
                    "no `domain:`. Every lexicon node declares who fixes the word.",
                    "domain",
                )
            )
            continue
        container = node.name == "README.md"
        if len(mine) > 1 and not container:
            have = _sections(node)
            out += [
                Finding(
                    rel,
                    f"declares `domain: {mine}` but has no `## {dom}` section. "
                    "One file per named thing, one section per domain.",
                    "domain",
                )
                for dom in mine
                if dom not in have
            ]
        parent = (
            node.parent / "README.md"
            if node.name != "README.md"
            else node.parent.parent / "README.md"
        )
        if parent.exists() and parent != node:
            up = domains_of(parent)
            if up and not set(mine) <= set(up):
                out.append(
                    Finding(
                        rel,
                        f"domain {mine} is not a subset of its parent "
                        f"{_position(parent, g)} {up} — that leaves a projection "
                        "rootless.",
                        "domain",
                    )
                )
    faulted = {f.where for f in out}
    kept = [str(p) for p in footprint(row, g) if str(p) not in faulted]
    return answer(row, kept, out)


def _position(node: Path, g: Graph) -> str:
    """A node's place within its home, for a message; its path when it is no entry."""
    entry = g.lexicon.entry_of(node)
    return str(node) if entry is None else entry.position


def check_required(row: Row, g: Graph) -> list[Answer]:
    """Does every node this tuple reaches carry what its own declared kind requires?

    A kind the ontology does not declare is a finding; the kinds are a closed set. A node
    with `status:` (reserved or retired) owes only a matching `instead:`.
    """
    if not g.kinds:
        return answer(row, [], [])
    out: list[Finding] = []
    above = g.lexicon.own.parent.parent
    for node in footprint(row, g):
        meta = _frontmatter.load(node)
        declared = str(meta.get("kind") or "")
        spec = g.kinds.get(declared)
        if spec is None:
            out.append(
                Finding(
                    str(node),
                    f"declares `kind: {declared}`, which {ONTOLOGY_REL.as_posix()} does "
                    "not declare. Declare it, or use a kind that is.",
                    "required",
                )
            )
            continue
        if meta.get("status"):
            if instead_disagrees(str(meta["status"]), str(meta.get("instead") or "")):
                out.append(
                    Finding(
                        str(node),
                        f"declares `status: {meta['status']}` with no `instead:` — a "
                        "retirement that names no replacement says what to stop writing "
                        "and not what to write.",
                        "required",
                    )
                )
            continue
        missing = [
            field
            for field in (*g.base, *(spec.get("required") or []))
            if field not in meta
        ]
        if missing:
            where = node.relative_to(above) if above in node.parents else node
            out.append(
                Finding(
                    str(where),
                    f"kind {declared!r} requires {', '.join(missing)} — declared in "
                    f"{ONTOLOGY_REL.as_posix()} and absent here.",
                    "required",
                )
            )
    faulted = {f.where for f in out}
    kept = [
        str(p)
        for p in footprint(row, g)
        if str(p) not in faulted
        and str(p.relative_to(above) if above in p.parents else p) not in faulted
    ]
    return answer(row, kept, out)


def noun_reaches_code(chain: tuple[str, ...], g: Graph) -> list[Finding]:
    """Does the noun this tuple belongs to reach code, and can anything reach it?

    A noun reaches code through a vertical, a script under `scripts/`, or both; having
    neither is the failure. A vertical without an api is a finding even when a script
    exists.
    """
    if g.pkg is None:
        return []
    module = g.pkg.joinpath(*chain)
    rel = module.relative_to(g.root)
    vertical = (module / "__init__.py").is_file()
    # A delivered script is addressed `<noun>`, flat, so it routes only a top-level noun.
    scripted = len(chain) == 1 and (g.root / "scripts" / f"{chain[0]}.py").is_file()
    node = (g.lexicon.own.joinpath(*chain) / "README.md").relative_to(g.root)

    if not vertical and not scripted:
        return [
            Finding(
                str(node),
                f"declares the noun {'.'.join(chain) or g.pkg.name!r}, which reaches no "
                f"code: no vertical at {rel}"
                + (
                    f" and no script at scripts/{chain[0]}.py"
                    if len(chain) == 1
                    else ""
                )
                + ". "
                + ADVICE.format(noun=".".join(chain) or g.pkg.name),
                "implemented",
            )
        ]
    if vertical and chain and not any((module / c).exists() for c in API_NAMES):
        return [
            Finding(
                str(rel),
                f"no {' or '.join(API_NAMES)}. Every surface reaches a noun through its "
                "api, so a vertical without one can be reached by nothing.",
                "implemented",
            )
        ]
    return []


def check_implemented(row: Row, g: Graph) -> list[Answer]:
    """Does the tree implement what this tuple declares?

    The CLI half is asked for every noun, the root included (the audit keys the root under
    the bare package name). The api half is skipped for the root, because `src/<pkg>/api/`
    is the `api` noun's vertical. `offered is None` (no CLI) leaves only the api half.
    """
    verb = row.verb
    if verb is None:  # a noun's own row names no command; `grade` never passes one
        return answer(row, [], [])
    if g.pkg is None:
        return answer(row, [], [])
    chain = tuple(row.noun.split(".")) if row.noun != root_noun(g.lexicon) else ()
    module_path = ".".join((g.pkg.name, *chain))

    # Several tuples share a noun, so the same finding arrives several times; `grade` keeps one.
    unreachable = noun_reaches_code(chain, g)
    if unreachable:
        return answer(row, [], unreachable)
    reachable = g.api.get(".".join(chain)) if chain else None
    unmet = (
        verb_gap(verb, g.cli.get(module_path), set(reachable))
        if reachable is not None
        else _cli_gap(verb, g.cli.get(module_path))
    )
    if unmet is None:
        # The pass case returns the evidence rather than nothing.
        return answer(row, [f"python -m {module_path} {row.verb}"], [])
    rel = g.pkg.joinpath(*chain).relative_to(g.root)
    where = row.node.relative_to(g.root) if g.root in row.node.parents else row.node
    return answer(
        row,
        [],
        [
            Finding(
                str(where),
                f"declares the verb {row.verb!r}, which "
                + unmet.format(module=module_path, api=f"{rel}/api"),
                "implemented",
            )
        ],
    )


def check_cuts_across(row: Row, g: Graph) -> list[Answer]:
    """Does a word this tuple uses cut across more than one thing and still have no node?

    A verb's users are the nouns whose tuples carry it; a param's are the `noun verb` pairs
    that declare it. Reports only.
    """
    verb = row.verb
    if verb is None:  # a noun's own row uses no word; `grade` never passes one
        return answer(row, [], [])
    out: list[Finding] = []
    fixed: list[str] = []
    for bucket, word, users, over in (
        ("verb", verb, g.verb_users.get(verb, frozenset()), "nouns"),
        *(
            ("param", flag, g.param_users.get(flag, frozenset()), "verbs")
            for flag in g.attrs[row].params
        ),
    ):
        if len(users) < 2:
            continue
        node = g.lexicon.own / bucket / f"{word}.md"
        where = word_node(g, bucket, word)
        if where is not None:
            fixed.append(where)
            continue
        who = ", ".join(sorted(users)[:3]) + ("..." if len(users) > 3 else "")
        out.append(
            Finding(
                str(node),
                f"`{word}` cuts across {len(users)} {over} ({who}) and has no node.",
                "cuts-across",
            )
        )
    return answer(row, fixed, out)


def check_flag_type(row: Row, g: Graph) -> list[Answer]:
    """Does each param this tuple declares have a node that agrees with the code?

    A local node contradicting canon is `_audit.flag_clashes`'s finding.
    """
    out: list[Finding] = []
    agreed: list[str] = []
    for flag in g.attrs[row].params:
        node = g.flag_local.get(flag) or g.flag_canon.get(flag)
        if node is None:
            continue  # no node yet; `cuts-across` is what asks for one
        malformed = _well_formed(node)
        if malformed:
            where, _, what = malformed.partition(": ")
            out.append(Finding(where, what, "flag-type"))
            continue
        actual = g.flag_types.get(f"--{flag}")
        if actual is None:
            continue  # documented for adopters; this repo has no occasion for it
        expected = _EXPECTED.get(node.kind)
        wrong = actual & _DISTINGUISHABLE if expected is None else set()
        if (expected is None and wrong) or (
            expected is not None and not actual & expected
        ):
            out.append(
                Finding(
                    node.where,
                    f"documented `type: {node.kind}` but this repo declares --{flag} as "
                    f"{sorted(wrong or actual)}",
                    "flag-type",
                )
            )
        else:
            agreed.append(f"--{flag}")
    return answer(row, agreed, out)


def check_flag_collision(row: Row, g: Graph) -> list[Answer]:
    """Does a param this tuple declares carry two incompatible types inside one domain?

    Two meanings across domains get a section each; two inside one domain is the owner's
    to fix.
    """
    out: list[Finding] = []
    for flag in g.attrs[row].params:
        clashing = g.flag_types.get(f"--{flag}", frozenset()) & _DISTINGUISHABLE
        if len(clashing) > 1:
            out.append(
                Finding(
                    f"--{flag}",
                    f"declared as {' and '.join(sorted(clashing))} in this repo — one "
                    "spelling with two meanings inside one domain. Rename one; racecar "
                    "can fix its own words.",
                    "collision",
                )
            )
    # Fires only on a clash, so a clean answer is empty.
    return answer(row, [], out)


def check_indexed(row: Row, g: Graph) -> list[Answer]:
    """Is every node this tuple reaches carried by its tree's index table?

    Only a tree whose README carries the INDEX markers is asked.
    """
    out: list[Finding] = []
    listed_ok: list[str] = []
    for node in footprint(row, g):
        # A tree's README carries the table; it is not a row in it.
        if node.name == "README.md":
            continue
        # The index beside the node, in the node's own home.
        entry = g.lexicon.entry_of(node)
        index = (
            None
            if entry is None
            else g.lexicon.at(
                str(PurePosixPath(entry.position).parent / "README.md").lstrip("./"),
                entry.origin,
            )
        )
        listed = None if index is None else index_rows(g.lexicon.path(index))
        if listed is None:
            continue  # this tree publishes no index, so it owes nothing
        if node.name in listed:
            listed_ok.append(str(node))
            continue
        out.append(
            Finding(
                str(node.parent / "README.md"),
                f"index carries no row for `{node.name}`. The table is derived from the "
                "nodes beside it; regenerate with `lexicon.py check --apply`.",
                "indexed",
            )
        )
    return answer(row, listed_ok, out)


def check_unindexed(g: Graph) -> list[Answer]:
    """Does any index table carry a row for a node that is not there?"""
    out: list[Answer] = []
    domain = (g.selected or (DEFAULT_DOMAIN,))[0]
    for under in ("", "param"):
        index = g.lexicon.at(f"{under}/README.md".lstrip("/"), OWN)
        listed = None if index is None else index_rows(g.lexicon.path(index))
        if index is None or listed is None:
            continue
        readme = g.lexicon.path(index)
        out += [
            Answer(
                "",
                undeclared(
                    domain,
                    PurePosixPath(under).name or g.lexicon.own.name,
                    Path(href).stem,
                    g.lexicon,
                ),
                (),
                (
                    Finding(
                        str(readme),
                        f"index carries a row for `{href}`, which is not a node here. The "
                        "table is derived; regenerate with `lexicon.py check --apply`.",
                        "",
                    ),
                ),
            )
            for href in sorted(listed)
            if g.lexicon.at(os.path.normpath(f"{under}/{href}".lstrip("/")), OWN)
            is None
        ]
    return out


def index_rows(readme: Path) -> set[str] | None:
    """The hrefs an index README's table lists, or None when it publishes no table.

    No markers means no duty; an empty table between markers is a duty being failed.
    """
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- BEGIN INDEX -->", "<!-- END INDEX -->"
    if start not in text or end not in text:
        return None
    block = text.split(start, 1)[1].split(end, 1)[0]
    return {link.target for link in _markdown.parse(block, frontmatter=False).links()}
