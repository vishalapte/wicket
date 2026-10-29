"""Reading the graph: what a node declares, and where the declarations live.

Part of `lib.lexicon`, the package `scripts/lexicon.py` is a command line over.

Complexity: O(N), N = nodes read (one frontmatter parse each, memoized per path)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lib.lexicon._corpora import (
    DELIVERED,
    OWN,
    Corpus,
    Entry,
    Lexicon,
    LexiconError,
    lexicon_corpora,
)
from lib.shared import _frontmatter, _markdown
from lib.shared._root import find_repo_root

# The three exit codes, beside the error. A check decides which one a run earns.
OK, FINDINGS, UNMET = 0, 1, 2


# Three names for one error: all three mean the same thing.
NounspaceError = VocabularyError = NomenclatureError = LexiconError

#: The repo's own entries only: what a reader of the repo's commands, nouns and domains keeps.
OWN_ONLY = frozenset({OWN})

ONTOLOGY_REL = Path("ontology") / "README.md"


def _own_ontology(home: Corpus) -> Path:
    """ONE home's own ontology declaration: the first `ontology:` entry of its root node that
    exists, or the conventional location when it declares none."""
    declared = _frontmatter.load(home.path / "README.md").get("ontology") or []
    for entry in declared if isinstance(declared, list) else [declared]:
        # Normalized, not resolved: keeps a `..` readable in findings and the path relative.
        candidate = Path(os.path.normpath(home.path / str(entry).rstrip("/")))
        if candidate.is_dir():
            return candidate / "README.md"
        if candidate.is_file():
            return candidate
    return home.path / ONTOLOGY_REL


def ontology_paths(lexicon: Lexicon) -> list[Path]:
    """Step two for the ontology. One declaration per home of the union, in precedence
    order, skipping a home that has none -- or the repo's own conventional location when
    no home has one, so a caller always has a path to name in a finding.
    """
    found = [_own_ontology(home) for home in lexicon.homes]
    return [path for path in found if path.is_file()] or [lexicon.own / ONTOLOGY_REL]


def ontology_path(lexicon: Lexicon) -> Path:
    """The corpus's own ontology: the first joined one.

    Corpus-level statements (`base`, `discriminator`, `partition`) are read from here; the
    kinds are a union over `ontology_paths`.
    """
    return ontology_paths(lexicon)[0]


API_NAMES = ("api.py", "api")


def kind_of(path: Path) -> str:
    """A node's kind, read from its own frontmatter, never from its directory."""
    return str(_frontmatter.load(path).get("kind") or "")


def term_nodes(lexicon: Lexicon) -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    """Every node that declares a kind, grouped by it. A node with no `kind:` is not a term."""
    out: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for entry in lexicon.nodes(origins=OWN_ONLY):
        if entry.kind:
            path = lexicon.path(entry)
            out.setdefault(entry.kind, []).append((path, _frontmatter.load(path)))
    return out


# What a kind node contributes to its kind's spec: the closed set declared at
# `architecture/ontology/ontology/kind.md`, so both ontology shapes yield the same dict.
KIND_SPEC_KEYS = (
    "required",
    "optional",
    "depends_on",
    "precedence",
    "container",
    "abstract",
    "versioned",
)

#: The directory an ontology lives in, and whose name is what marks a graph-shaped one.
ONTOLOGY_DIRNAME = "ontology"


def is_kind_node(path: Path) -> bool:
    """Whether `path` declares a kind: `name:` (which kind) and `required:` (what it
    owes). Both, because `name:` alone is carried by every node in every corpus."""
    if path.name == "README.md":
        return False
    frontmatter = _frontmatter.load(path)
    return bool(frontmatter.get("name")) and "required" in frontmatter


def is_graph(meta: Path, declaration: Path) -> bool:
    """Whether `meta` is the graph shape: a directory named `ontology`, indexed by its own
    `README.md`, holding at least one kind node.

    Both conditions are needed: `docs/lexicon/graph/ontology/` is named `ontology` and
    holds a CLI noun's verb nodes.
    """
    return (
        meta.is_dir()
        and meta.name == ONTOLOGY_DIRNAME
        and declaration.name == "README.md"
        and any(is_kind_node(p) for p in meta.glob("*.md"))
    )


def kind_nodes(meta: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    """Every kind declared as its own node in the ontology directory `meta`, and where.

    The graph shape: one `.md` per kind beside the index, `name:` naming the kind (never
    the filename) and `required:` what it owes. A file beside the index that declares
    neither field is reported, and so is a kind declared twice.

    Raises `ValueError`, the refusal `ontology.py`'s callers already catch; `declared_kinds`
    turns it into the lexicon's own.
    """
    kinds: dict[str, dict[str, Any]] = {}
    where: dict[str, Path] = {}
    for path in sorted(meta.glob("*.md")):
        if path.name == "README.md":
            continue
        frontmatter = _frontmatter.load(path)
        if not frontmatter.get("name") or "required" not in frontmatter:
            raise ValueError(
                f"{path}: a kind node declares `name:` (the kind) and `required:` "
                "(the fields that kind owes, `[]` when none); this one declares "
                f"{sorted(frontmatter) or 'nothing'}"
            )
        name = str(frontmatter["name"])
        if name in where:
            raise ValueError(
                f"{path}: declares kind {name!r}, already declared by "
                f"{where[name]} — a kind has one home"
            )
        spec = {k: frontmatter[k] for k in KIND_SPEC_KEYS if k in frontmatter}
        spec["_pnode"] = frontmatter.get("pnode") or []
        kinds[name] = spec
        where[name] = path
    return kinds, where


def _supertypes(
    kinds: dict[str, dict[str, Any]], where: dict[str, Path]
) -> dict[str, list[str]]:
    """Each kind's supertypes: the `pnode` entries that resolve to another kind node.

    A kind's `pnode` is inheritance. A `pnode` landing outside the kind set is containment
    and inherits nothing.
    """
    by_path = {path.resolve(): name for name, path in where.items()}
    supers: dict[str, list[str]] = {}
    for name in kinds:
        own = where[name]
        parents = []
        for ref in kinds[name]["_pnode"]:
            parent = by_path.get((own.parent / str(ref)).resolve())
            if parent is not None and parent != name and parent not in parents:
                parents.append(parent)
        supers[name] = parents
    return supers


def inherit(
    kinds: dict[str, dict[str, Any]], where: dict[str, Path]
) -> dict[str, dict[str, Any]]:
    """Every kind's spec with its supertypes' folded in, supertype first.

    Only the list-valued spec keys are inherited (`required`, `optional`, `depends_on`);
    scalar ones such as `abstract` are not. A kind that inherits from itself through
    `pnode` is refused (`ValueError`).
    """
    supers = _supertypes(kinds, where)
    resolved: dict[str, dict[str, Any]] = {}
    state: dict[str, int] = {}

    def walk(name: str) -> dict[str, Any]:
        if state.get(name) == 2:
            return resolved[name]
        if state.get(name) == 1:
            raise ValueError(
                f"{where[name]}: kind {name!r} inherits from itself through "
                "`pnode` — a graph of kinds must be acyclic"
            )
        state[name] = 1
        own = {k: v for k, v in kinds[name].items() if k != "_pnode"}
        inherited: dict[str, list[Any]] = {}
        for parent in supers[name]:
            for key, value in walk(parent).items():
                if not isinstance(value, list):
                    continue
                acc = inherited.setdefault(key, [])
                acc.extend(v for v in value if v not in acc)
        spec = dict(own)
        for key, acc in inherited.items():
            spec[key] = [*acc, *(v for v in (own.get(key) or []) if v not in acc)]
        state[name] = 2
        resolved[name] = spec
        return spec

    return {name: walk(name) for name in kinds}


def graph_kinds(
    meta: Path, declaration: Path, declared: dict[str, Any]
) -> dict[str, dict[str, Any]] | None:
    """The resolved kinds of the graph-shaped ontology at `meta`, or None when it is a
    document (its index's own `kinds:` block, or no kinds at all).

    Read by both `declared_kinds` here and `ontology.load_ontology`. `declared` is the
    index's own frontmatter. An index carrying a `kinds:` block beside kind nodes is
    refused.
    """
    if not is_graph(meta, declaration):
        return None
    kinds, where = kind_nodes(meta)
    if declared.get("kinds"):
        raise ValueError(
            f"{declaration}: declares a `kinds:` block AND has kind nodes beside it "
            f"({', '.join(sorted(kinds))}) — an ontology is a document or a "
            "graph, never both"
        )
    return inherit(kinds, where)


def _one_ontology_kinds(path: Path) -> dict[str, Any]:
    """The kinds one declaration at `path` holds -- the kind nodes beside it when it is a
    graph, its own `kinds:` block when it is a document.

    A declaration the shared reader refuses is a `LexiconError`.
    """
    declared = _frontmatter.load(path)
    try:
        graph = graph_kinds(path.parent, path, declared)
    except ValueError as err:
        raise LexiconError(str(err)) from err
    if graph is not None:
        return graph
    kinds = declared.get("kinds")
    return dict(kinds) if isinstance(kinds, dict) else {}


def declared_kinds(lexicon: Lexicon) -> dict[str, Any]:
    """The kinds the ontologies of the union declare, read per declaration by `graph_kinds`.

    A union over `lexicon_corpora`. A kind declared in more than one corpus resolves to the
    first one in `lexicon_corpora` order: custom, then the repo's own, then the delivered
    corpus. So a repo can narrow a delivered kind. `shadowed_kinds` reports the resolution.
    """
    out: dict[str, Any] = {}
    for path in ontology_paths(lexicon):
        for name, spec in _one_ontology_kinds(path).items():
            out.setdefault(name, spec)
    return out


def shadowed_kinds(lexicon: Lexicon) -> dict[str, Path]:
    """Every kind declared in more than one of the joined corpora, mapped to the
    declaration whose copy lost.

    Not a finding (restating a delivered kind is how a repo narrows it), but reported.
    """
    seen: set[str] = set()
    out: dict[str, Path] = {}
    for path in ontology_paths(lexicon):
        for name in _one_ontology_kinds(path):
            if name in seen:
                out[name] = path
            else:
                seen.add(name)
    return out


def declared_nouns(lexicon: Lexicon) -> dict[tuple[str, ...], Path]:
    """Every node declaring `kind: noun`, keyed by its chain, in every domain.

    The chain is the node's directory path. The empty chain is the root package:
    `docs/lexicon/README.md` mirrors `python -m <pkg>`. Which of them a run acts on is
    `eligible_nouns`'s question.
    """
    return {
        entry.parts: lexicon.path(entry)
        for entry in lexicon.nodes(origins=OWN_ONLY, kind="noun")
    }


def declared_verbs(lexicon: Lexicon, chain: tuple[str, ...]) -> set[str]:
    """The verbs a noun answers to: the `kind: verb` nodes sitting beside its README."""
    return {
        entry.stem
        for entry in lexicon.nodes(origins=OWN_ONLY, kind="verb")
        if entry.parts == chain and entry.filename != "README.md"
    }


def render(problems: list[str]) -> str:
    """The gate's one-block report, the shape `racecar.arch` composes from every checker."""
    if not problems:
        return "nounspace: OK"
    lines = [f"nounspace: {len(problems)} finding(s)"]
    lines += [f"  - {line}" for line in problems]
    return "\n".join(lines)


def has_nounspace(root: Path) -> bool:
    """Whether this repo declares a nounspace at all.

    The predicate `racecar.arch` uses to decide the repo is a subject of this rule.
    """
    return bool(declared_kinds(lexicon_corpora(root)))


SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".collections",
}

DOC_SUFFIXES = (".md",)

CODE_SUFFIXES = (".py", ".sh", ".mk")


def find_root(start: Path | None = None) -> Path:
    """The repo under check: the nearest `.git` ancestor, strictly.

    Keyed on the repo, not on a term tree, because an adopter has no lexicon of its own.
    `find_repo_root` returns its starting point when there is no `.git`; that fallback is
    refused here.
    """
    root = find_repo_root(start)
    if not ((root / ".racecar").is_dir() or (root / ".git").exists()):
        raise LexiconError(f"no repo root (no .racecar/ or .git) at or above {root}")
    return root


DEFAULT_DOMAIN = "racecar"


def noun_count(lexicon: Lexicon) -> int:
    """How many nouns this repo's own lexicon declares."""
    return len(lexicon.nodes(origins=OWN_ONLY, kind="noun"))


def partition_field(lexicon: Lexicon) -> str | None:
    """The field this lexicon partitions on, or None when it declares none.

    Opt-in per corpus: a lexicon that declares no `partition:` has no domains.
    """
    declared = _frontmatter.load(ontology_path(lexicon))
    value = declared.get("partition")
    return str(value) if value else None


def domains_of(node: Path) -> list[str]:
    """The domains a node declares. Absent is not "every domain"; it is a finding here."""
    raw = _frontmatter.load(node).get("domain")
    if isinstance(raw, str):
        return [raw.strip()]
    return [str(d).strip() for d in raw] if isinstance(raw, list) else []


def declared_domains(lexicon: Lexicon) -> list[str]:
    """Every domain any of the repo's own nodes claims: the set `--domain` is validated
    against."""
    seen: set[str] = set()
    for entry in lexicon.nodes(origins=OWN_ONLY):
        seen.update(domains_of(lexicon.path(entry)))
    return sorted(seen)


def graded_here(node: Path, lexicon: Lexicon) -> bool:
    """Whether this repo's checks grade `node`: every node except one racecar delivered.

    The delivered copy (`.racecar/docs/lexicon`) is racecar's to fix and the next sync
    overwrites it. racecar's own canon is authored in place, so racecar grades it.
    """
    entry = lexicon.entry_of(node)
    return entry is None or entry.origin != DELIVERED


def _sections(node: Path) -> set[str]:
    """The `## <name>` headings a node carries, lowercased."""
    return {h.title.strip().lower() for h in _markdown.read(node).headings(2)}


def _has_prose(node: Path) -> bool:
    """Whether a node says anything beyond its own headings: the test for a stub."""
    doc = _markdown.read(node)
    return any(
        line.text.strip() and doc.heading_at(line.no) is None for line in doc.body()
    )


def _is_command(entry: Entry, lexicon: Lexicon) -> bool:
    """A verb node is a command when it sits under a noun; under a kind bucket it is a meaning.

    `graph/check.md` is `racecar.graph check`; `verb/check.md` addresses nothing. The test
    is the neighbour: a noun directory carries a `README.md` declaring `kind: noun`.
    """
    if not entry.parts:
        return True
    parent = lexicon.at("/".join((*entry.parts, "README.md")), entry.origin)
    return parent is not None and parent.kind == "noun"


def _params_of(node: Path) -> list[str]:
    raw = _frontmatter.load(node).get("params")
    return [str(p) for p in raw] if isinstance(raw, list) else []


def _declared_name(lexicon: Lexicon) -> str | None:
    """The `name:` a corpus's root node declares, or None when it declares none.

    Shared by `root_noun` and `corpus_domain`, which differ in their fallbacks.
    """
    # The repo's own root node, so a name `declare` has just written is read back.
    declared = _frontmatter.load(lexicon.own / "README.md").get("name")
    return str(declared).strip('"') if declared else None


def root_noun(lexicon: Lexicon) -> str:
    """What the root package is called, read from the root node's `name:`.

    The empty chain is the root. Falls back to the package directory's name.
    """
    return _declared_name(lexicon) or lexicon.own.name


def corpus_domain(lexicon: Lexicon) -> str:
    """The domain a run acts on when `--domain` names none: the corpus's own.

    Read from the root node's `name:`, which every node below claims in `domain:`.
    `DEFAULT_DOMAIN` is the answer for a corpus with no root node to ask.
    """
    return _declared_name(lexicon) or DEFAULT_DOMAIN


def verb_node(lexicon: Lexicon, noun: str, verb: str | None) -> Path:
    """Where one tuple's node lives, in the repo's own home. A dotted noun is the
    directories it nests in.

    The inverse of the chain `tuples()` walks. The root noun is the empty chain: its verbs
    sit directly under the lexicon. A tuple with no verb is the noun itself, and its node
    is the noun's README.
    """
    chain = () if noun == root_noun(lexicon) else tuple(noun.split("."))
    leaf = "README.md" if verb is None else f"{verb}.md"
    return lexicon.own.joinpath(*chain, leaf)


#: The param fields a node may omit, and what omitting one means (`param.md` in the ontology).
#: `required`: the verb refuses to run without it. `defined`: a person has written what it is;
#: a node a `create` wrote is not, and says so. `position`: the verbs, as `<noun>.<verb>`, that
#: take it by position rather than as a `--flag`.
PARAM_DEFAULTS: dict[str, Any] = {"required": False, "defined": False, "position": []}


def param_fields(meta: dict[str, Any]) -> dict[str, Any]:
    """A param node's `required`, `defined` and `position`, each defaulted where absent."""
    return {
        "required": meta.get("required") is True,
        "defined": meta.get("defined") is True,
        "position": [str(v) for v in meta.get("position") or []],
    }


def describe(lexicon: Lexicon, noun: str, verb: str | None = None) -> dict[str, Any]:
    """What the lexicon declares for one noun, or one of its verbs, as data.

    `{"noun": noun, "summary": ..., "verbs": {verb: {"summary": ..., "params":
    [{"name", "type", "summary", "required", "defined", "position"}, ...]}}}`, every verb
    when `verb` is None. A param with no node of its own, or a node that declares no
    `type`, comes back with type `None`. Raises `LexiconError` when the noun, or the named
    verb, is not declared.
    """
    readme = verb_node(lexicon, noun, None)
    if not readme.is_file():
        raise LexiconError(f"{noun}: not declared ({readme} does not exist)")
    chain = () if noun == root_noun(lexicon) else tuple(noun.split("."))
    wanted = sorted(declared_verbs(lexicon, chain)) if verb is None else [verb]
    verbs: dict[str, Any] = {}
    for name in wanted:
        node = verb_node(lexicon, noun, name)
        if not node.is_file():
            raise LexiconError(f"{noun} {name}: not declared ({node} does not exist)")
        params = []
        for param in _params_of(node):
            # The param's node as the union resolves it: a word racecar delivers is
            # described from the delivered node when the repo holds none of its own.
            found = lexicon.find(f"param/{param}.md")
            meta = _frontmatter.load(found) if found is not None else {}
            params.append(
                {
                    "name": param,
                    "type": meta.get("type"),
                    "summary": meta.get("summary"),
                    **param_fields(meta),
                }
            )
        verbs[name] = {
            "summary": _frontmatter.load(node).get("summary"),
            "params": params,
        }
    return {
        "noun": noun,
        "summary": _frontmatter.load(readme).get("summary"),
        "verbs": verbs,
    }
