"""The list: `(domain, noun, verb, node)`, and everything a run reads once.

Part of `lib.lexicon`, the package `scripts/lexicon.py` is a command line over. The tests,
the `lexicon` noun's api and the delivered CLI import these functions.

Complexity: O(P + A), P = nodes walked once into the list, A = the one CLI audit
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from lib.lexicon import _audit
from lib.lexicon._audit import (
    Node,
)
from lib.lexicon._corpora import OWN, Lexicon
from lib.lexicon._eligible import eligible_nouns, in_selection
from lib.lexicon._nodes import (
    DEFAULT_DOMAIN,
    OWN_ONLY,
    _is_command,
    _params_of,
    declared_domains,
    declared_kinds,
    domains_of,
    graded_here,
    ontology_path,
    partition_field,
    root_noun,
    verb_node,
)
from lib.shared import _frontmatter
from lib.shared._root import package_dir, packages


def listing(lexicon: Lexicon, selected: list[str]) -> list[dict[str, Any]]:
    """The list, as records: `[{domain, noun, verb, node, params}, ...]`.

    Built from `tuples()`, not `status_rows()`: a verb the tree implements and the lexicon
    does not declare is reported by `check`, not listed.
    """
    return [
        {
            "domain": row.domain,
            "noun": row.noun,
            "verb": row.verb,
            "node": str(row.node),
            "params": list(attrs.params),
        }
        for row, attrs in tuples(lexicon, selected).items()
    ]


# ---------------------------------------------------------------------------------------------
# Grading: the list of tuples, one loop, n methods.
# ---------------------------------------------------------------------------------------------
class Row(NamedTuple):
    """The tuple: `(domain, noun, verb, node)`. Unique across the first three.

    `node` is where the declaration lives, so a method never goes looking. Nodes that are
    not commands (`key/pnode.md`, `param/force.md`) are not tuples.

    `params` is an attribute of the key, not part of it: adding a flag does not make a
    different tuple.

    A noun with no verb of its own is `(domain, noun, None)`, and its `node` is the noun's
    README. Readers of the list as verbs skip these rows; readers of it as the set of
    declared nouns keep them.
    """

    domain: str
    noun: str
    verb: str | None
    node: Path


def row_order(row: Row) -> tuple[str, str, str, str]:
    """The order the list is kept in: a noun's own row first, then its verbs.

    `sorted()` on bare rows compares `None` with a verb string under the same noun and raises
    `TypeError`, so every sort of rows goes through this rather than through tuple order.
    """
    return (row.domain, row.noun, row.verb or "", str(row.node))


class Attrs(NamedTuple):
    """What hangs off one tuple. Never part of it.

    `reaches` is what the node points at: the noun's README, the verb's meaning node where
    the word cuts across, and a node per param. A method starts from it and never walks.
    """

    params: tuple[str, ...]
    reaches: tuple[Path, ...]


class Graph(NamedTuple):
    """The list, and everything a run reads about the tree it is checked against, read once.

    A method reads fields here; it does not walk anything. `cli` and `api` are the expensive
    reads: the CLI audit runs the whole command tree.
    """

    root: Path
    lexicon: Lexicon
    selected: tuple[str, ...]
    pkg: Path | None
    kinds: dict[str, Any]
    base: tuple[str, ...]
    cli: dict[str, set[str]]
    api: dict[str, frozenset[str]]
    addressable: frozenset[tuple[str, ...]]
    partitioned: bool
    rows: tuple[Row, ...]
    attrs: dict[Row, Attrs]
    verb_users: dict[str, frozenset[str]]
    param_users: dict[str, frozenset[str]]
    flag_types: dict[str, frozenset[str]]
    flag_canon: dict[str, Node]
    flag_local: dict[str, Node]
    verb_args: dict[tuple[str, str], tuple[dict[str, Any], ...]]


class Finding(NamedTuple):
    """One gap, as a record. Internal to a method; what a method RETURNS is an `Answer`."""

    where: str
    what: str
    method: str

    def __str__(self) -> str:
        return f"{self.where}: {self.what}"


class Answer(NamedTuple):
    """What one check says about one tuple: `(check, tuple, implemented, missing)`.

    One flat list of these is every answer from every check; a caller filters it by check,
    by whether anything is missing, or by noun.

    - `implemented` — evidence the surface holds up its end for this tuple.
    - `missing` — what it does not. Empty means the check passed.

    On a reverse answer with a non-empty `missing`, `row` is a tuple the lexicon does not
    have, synthesised from the surface; it is the argument `lexicon create --tuple` takes.
    A forward answer's `row` always comes from the lexicon.
    """

    check: str
    row: Row
    implemented: tuple[str, ...]
    missing: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        """Whether this check found nothing wrong about this tuple."""
        return not self.missing


def undeclared(domain: str, noun: str, verb: str | None, lexicon: Lexicon) -> Row:
    """The tuple the lexicon would have for a command the surface offers and it does not name.

    Its `node` is where that node would go. A reverse half returns this, and
    `create --tuple` takes it.
    """
    return Row(domain, noun, verb, verb_node(lexicon, noun, verb))


class Word(NamedTuple):
    """One node of the lexicon, whatever kind it is. Not a tuple.

    A `Row` is a command; only a verb node under a noun becomes one. A `Word` covers every
    node, including nouns, params and the verb nodes under a kind bucket.

    - `noun` is None under a kind bucket, and a dotted chain where a noun nests
      (`graph.ontology`). A node in the corpus root belongs to the root noun, which its
      own node names.
    - `fields` carries the keys this kind's `required:` names, read off the node: a verb's
      `params`, a param's `type`, nothing for kinds that require nothing. The ontology
      says which, so a new kind needs no code here.
    """

    domain: str
    kind: str
    name: str
    noun: str | None
    node: Path
    fields: dict[str, Any]


def _noun_chain(
    parts: tuple[str, ...], lexicon: Lexicon, origin: str = OWN
) -> str | None:
    """The dotted noun a node hangs under, or None when no noun does.

    Every directory between the node and the corpus root must declare `kind: noun`; one that
    does not (`param/`, an `index`) puts the node in a kind bucket. The whole chain is
    tested, because `graph/ontology/check.md` is two nouns deep.

    A node directly in the corpus root belongs to the root noun, named by its node, as in
    `tuples()`. None means only that the node is in a kind bucket.
    """
    for depth in range(len(parts), 0, -1):
        readme = lexicon.at("/".join((*parts[:depth], "README.md")), origin)
        if readme is None or readme.kind != "noun":
            return None
    return ".".join(parts) if parts else root_noun(lexicon)


def words(
    lexicon: Lexicon, selected: list[str], kind: list[str]
) -> list[dict[str, Any]]:
    """Every node of the named kinds, as records. The corpus by kind, not by command.

    READMEs are in: a noun's node is its README. `kind` empty means every kind; it is a list
    because the flag is repeatable. The caller validates what it names: the CLI refuses an
    unknown `--kind` against the ontology's `kinds:`.
    """
    wanted = set(kind)
    required = declared_kinds(lexicon)
    found: list[Word] = []
    for entry in lexicon.nodes(origins=OWN_ONLY):
        declares = entry.kind
        if not declares or (wanted and declares not in wanted):
            continue
        node = lexicon.path(entry)
        meta = _frontmatter.load(node)
        spec = required.get(declares) or {}
        keys = spec.get("required") or [] if isinstance(spec, dict) else []
        declared = domains_of(node)
        for domain in declared or [DEFAULT_DOMAIN]:
            # As in `tuples()`: a node declaring no domain stays whatever was selected,
            # so the missing declaration is reported.
            if not in_selection(declared, selected, domain):
                continue
            found.append(
                Word(
                    domain=domain,
                    kind=declares,
                    name=str(meta.get("name") or node.stem).strip('"'),
                    noun=_noun_chain(entry.parts, lexicon),
                    node=node,
                    fields={k: meta[k] for k in keys if k in meta},
                )
            )
    return [
        {
            "domain": w.domain,
            "kind": w.kind,
            "name": w.name,
            "noun": w.noun,
            "node": str(w.node),
            "fields": w.fields,
        }
        for w in sorted(found, key=lambda w: (w.domain, w.kind, w.name, str(w.node)))
    ]


def tuples(lexicon: Lexicon, selected: list[str]) -> dict[Row, Attrs]:
    """The list: every `(domain, noun, verb)` the graph declares, and what hangs off each.

    One walk of the corpus; every caller that wants the tuples reads this.
    """
    found: dict[Row, Attrs] = {}
    root = root_noun(lexicon)
    # The list starts from the eligible nouns in every domain; domains are applied per row
    # below. A reserved noun, and every verb under it, is left out.
    eligible = eligible_nouns(lexicon, declared_domains(lexicon))
    # The repo's own commands: racecar delivers none, so its delivered copy adds no row.
    for entry in lexicon.nodes(origins=OWN_ONLY, kind="verb"):
        if entry.filename == "README.md" or not _is_command(entry, lexicon):
            continue
        node = lexicon.path(entry)
        chain = entry.parts
        if chain not in eligible:
            continue
        noun = ".".join(chain) if chain else root
        verb = str(_frontmatter.load(node).get("name") or node.stem).strip('"')
        params = tuple(_params_of(node))
        # The nodes this command uses, as the union resolves them. Every reader of `reaches`
        # (`footprint`) is a check that GRADES the node, so a node in racecar's domain is left
        # out: racecar grades its own words where it writes them (`graded_here`).
        positions = (
            "/".join((*chain, "README.md")),
            f"verb/{verb}.md",
            *(f"param/{flag}.md" for flag in params),
        )
        reaches = tuple(
            path
            for path in (lexicon.find(position) for position in positions)
            if path is not None and path != node and graded_here(path, lexicon)
        )
        declared = domains_of(node)
        for domain in declared or [DEFAULT_DOMAIN]:
            # A node declaring no domain stays in the list whatever was selected, so the
            # missing declaration is reported in every projection.
            if not in_selection(declared, selected, domain):
                continue
            found[Row(domain, noun, verb, node)] = Attrs(params, reaches)
    # Every noun, as its own row with no verb. Placed by `_noun_chain`, the rule `words()`
    # uses, so a README declaring `kind: noun` inside a kind bucket is not a command noun here
    # either.
    for chain, node in eligible.items():
        noun_name = _noun_chain(chain, lexicon)
        if noun_name is None:
            continue
        declared = domains_of(node)
        for domain in declared or [DEFAULT_DOMAIN]:
            if not in_selection(declared, selected, domain):
                continue
            found[Row(domain, noun_name, None, node)] = Attrs((), ())
    return dict(sorted(found.items(), key=lambda item: row_order(item[0])))


def addressable(root: Path) -> frozenset[tuple[str, ...]]:
    """Every chain in the repo's packages that a `python -m` invocation reaches, read once.

    A directory with a `__main__.py` is addressable; anything else is not.
    """
    found: set[tuple[str, ...]] = set()
    for pkg in packages(root):
        for entry in pkg.rglob("__main__.py"):
            found.add(entry.parent.relative_to(pkg).parts)
    return frozenset(found)


def api_surface(root: Path) -> dict[str, frozenset[str]]:
    """`{dotted noun: the names its api exposes}` for every vertical under `src/`, read once."""
    pkg = package_dir(root)
    if pkg is None:
        return {}
    out: dict[str, frozenset[str]] = {}
    for init in sorted(pkg.rglob("__init__.py")):
        module = init.parent
        chain = module.relative_to(pkg).parts
        if chain:
            out[".".join(chain)] = frozenset(_audit.api_functions(module))
    return out


def graph(root: Path, lexicon: Lexicon, selected: list[str]) -> Graph:
    """Read the list and the tree once, into the value every method is graded against."""
    listed = tuples(lexicon, selected)
    # Both counts come FROM the list. A word cuts across when more than one tuple uses it,
    # which is a fact about the list.
    verb_users: dict[str, set[str]] = {}
    param_users: dict[str, set[str]] = {}
    for row, attrs in listed.items():
        if row.verb is None:
            continue
        verb_users.setdefault(row.verb, set()).add(row.noun)
        for flag in attrs.params:
            param_users.setdefault(flag, set()).add(f"{row.noun} {row.verb}")
    # Every spelling the code declares, from both routes, merged once. `collision` asks
    # whether one spelling carries two meanings and `flag-type` asks whether a node agrees
    # with the code.
    types: dict[str, set[str]] = {}
    if _audit.has_cli(root):
        for flag, kinds in _audit.declared_flag_types(root).items():
            types.setdefault(flag, set()).update(kinds)
    for flag, kinds in _audit.script_flag_types(root).items():
        types.setdefault(flag, set()).update(kinds)
    # Keyed by the node's file stem, not its declared `name`: a param references a node by
    # spelling, and the declared `name` is itself checked for stray dashes.
    canonical: dict[str, Node] = {}
    local: dict[str, Node] = {}
    for flag_node in _audit.flag_nodes(lexicon):
        stem = Path(flag_node.where.split(" ")[0]).stem
        # The first by precedence wins: `flag_nodes` lists the union in that order.
        (canonical if flag_node.canon else local).setdefault(stem, flag_node)
    return Graph(
        root=root,
        lexicon=lexicon,
        selected=tuple(selected),
        pkg=package_dir(root),
        kinds=declared_kinds(lexicon),
        base=tuple(
            (_frontmatter.load(ontology_path(lexicon)).get("base") or {}).get(
                "required"
            )
            or []
        ),
        cli=_audit.cli_verbs(root) if _audit.has_cli(root) else {},
        api=api_surface(root),
        addressable=addressable(root),
        partitioned=partition_field(lexicon) is not None,
        rows=tuple(listed),
        attrs=listed,
        verb_users={word: frozenset(who) for word, who in verb_users.items()},
        param_users={word: frozenset(who) for word, who in param_users.items()},
        flag_types={flag: frozenset(kinds) for flag, kinds in types.items()},
        flag_canon=canonical,
        flag_local=local,
        verb_args=_audit.cli_args(root) if _audit.has_cli(root) else {},
    )


def word_node(g: Graph, bucket: str, word: str) -> str | None:
    """Where the node fixing `word` in `bucket` (`verb` or `param`) is, or None.

    The one test of whether a word is fixed, for the check that reports a word with no node
    and for `--apply`, which writes the frame for one. A param's node may be in any corpus
    the lexicon joins, the delivered one included; the graph holds every one it found.
    """
    node = g.lexicon.find(f"{bucket}/{word}.md")
    if node is not None:
        return str(node)
    known = (
        (g.flag_canon.get(word) or g.flag_local.get(word))
        if bucket == "param"
        else None
    )
    return None if known is None else known.where


def answer(row: Row, implemented: list[str], found: list[Finding]) -> list[Answer]:
    """Fold a forward method's working into the one shape every check answers in.

    `check` is left blank here and stamped by `grade`, which knows which method it called.
    Returns a list so forward and reverse halves share a return type.
    """
    return [Answer("", row, tuple(implemented), tuple(found))]


def footprint(row: Row, g: Graph) -> tuple[Path, ...]:
    """The node this tuple declares itself on, and every node that node points at."""
    return (row.node, *g.attrs[row].reaches)
