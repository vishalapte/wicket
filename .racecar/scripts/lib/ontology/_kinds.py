"""Check, or derive, the ontology a body of source material conforms to.

Delivered as `lib/ontology/` beside `scripts/ontology.py`, its command line. It runs in an
adopter's `.racecar/scripts/` with no racecar installed, so it imports only what is
delivered with it: `lib.topology` for the tier walk, `lib.lexicon._nodes` for the corpus
join and the kind reader. `racecar.graph.ontology.lib._verbs` delegates here, never the
reverse.

An ontology says what kinds of thing exist in a domain and what each must carry
(`ONTOLOGY.md`). This module scores a corpus against exactly one ontology, declared at
`--meta`.

`ontology.yaml` (or its containing directory), named at `--meta`, sibling to the
sources it describes:

    name: deal
    discriminator: kind   # optional; default "kind" -- the frontmatter field
                          # naming a source's kind
    base:
      required: [asof]
    kinds:
      entity:
        required: [role]
      instrument:
        required: [counterparty]
        depends_on: [entity]

`kinds` maps a kind name to the fields a node of that kind must carry (`required`) and,
optionally, the kinds it depends on (`depends_on`). That graph is checked acyclic
(`acyclic_kinds`) before any source is scored. `base.required` is optional: fields every
node carries, checked in addition to its kind's own `required`.

An ontology lives at `<meta>/ontology/` and is a graph or a document, never both. An
index alone (`ontology/README.md` with a `kinds:` block, or an `ontology.yaml`) is a
document. The index with `<kind>.md` nodes beside it, each declaring `name:` and
`required:`, is a graph; a kind node's `pnode` is inheritance, so a kind filed under
another carries that kind's requirements plus its own. `load_ontology` resolves both to
one flat `kinds` mapping.

`identify` and `derive` run the same inference from field presence in `--data`;
`identify` reports, `derive` writes to `--meta`. `check` scores `--data` against an
already-declared ontology and infers nothing. Every verdict reports the unmapped
fraction.

Complexity: O(S), S = sources read (one frontmatter parse each) x C corpora joined
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml
from lib.lexicon._corpora import home_directories
from lib.lexicon._nodes import ONTOLOGY_DIRNAME, graph_kinds
from lib.shared import _frontmatter
from lib.topology import _walk

# Keys `lib.topology._walk.load` adds to every node beyond its frontmatter; stripped so
# `derive()` does not count them as required fields.
_TOPOLOGY_DERIVED_KEYS = frozenset({"id", "name", "serves", "path"})
DEFAULT_SAMPLE_LIMIT = 500
# Past this fraction unmapped, the declared ontology does not fit — `check` reports
# no match rather than naming a schema most of the corpus disagrees with.
UNMAPPED_THRESHOLD = 0.2
ONTOLOGY_FILENAME = "ontology.yaml"
# `ONTOLOGY_DIRNAME` (`<meta>/ontology/`) is imported from `lib.lexicon._nodes`.

# The frontmatter field naming a source's kind, absent a declared override.
DEFAULT_DISCRIMINATOR = "kind"


@dataclasses.dataclass(frozen=True)
class Fit:
    """How well a sampled corpus matches the ONE ontology declared at `--meta`.

    `best` is the ontology's own `name` when the fit clears
    `UNMAPPED_THRESHOLD`, `None` otherwise — either because `--meta` declares no
    ontology at all, or because it declares one the sources mostly don't match.
    Both cases mean the same next step: call `derive()`.
    """

    best: str | None
    unmapped_fraction: float
    total: int
    by_kind: dict[str, int]


@dataclasses.dataclass(frozen=True)
class Proposal:
    """A candidate ontology inferred from `--data`, not yet declared anywhere.

    Adopting it is the corpus owner's decision.
    """

    kinds: dict[str, list[str]]  # kind -> required fields, inferred from presence
    dag: dict[str, list[str]]  # kind -> kinds it depends on, best-effort
    total: int


def _from_topology(data: Path, limit: int) -> list[dict[str, Any]] | None:
    """The declared topology's node graph, reshaped to what `sample()` returns.

    Returns `None` when `data` declares no topology, so the caller falls back to its own
    glob. A topology node is one `<dir>/README.md`; `sample()`'s glob node is one `.md`
    file. When a topology is declared, its nodes are used, so `check`/`identify`/`derive`
    score the same nodes `racecar.graph.topology check` grades.
    """
    if _walk.resolve(data / "topology") is None:
        return None
    out: list[dict[str, Any]] = []
    for node in list(_walk.load(data).values())[:limit]:
        source = {k: v for k, v in node.items() if k not in _TOPOLOGY_DERIVED_KEYS}
        if source.get("kind") is None:
            source.pop("kind", None)
        path = node["path"]
        source["_path"] = path if path.is_file() else path / "README.md"
        out.append(source)
    return out


def sample(data: Path, limit: int = DEFAULT_SAMPLE_LIMIT) -> list[dict[str, Any]]:
    """The corpus's declared nodes when `data` has a topology (`_from_topology`).

    Otherwise every `.md` file under `data` carrying YAML frontmatter, up to `limit`,
    walked in sorted order so the same corpus samples the same way twice.
    """
    from_topology = _from_topology(data, limit)
    if from_topology is not None:
        return from_topology
    out: list[dict[str, Any]] = []
    for path in sorted(data.rglob("*.md"))[:limit]:
        frontmatter = _frontmatter.load(path)
        if frontmatter:
            out.append({**frontmatter, "_path": path})
    return out


def _ontology_path(meta: Path) -> Path:
    """The declared ontology's source at `meta`.

    `meta`'s own `README.md` first; else the first `*.yaml` (a flat projection of a
    declaration too large for one node's frontmatter). A `meta` that is not a directory
    is used as given. `README.md` is the whole ontology when it is a document, or the
    index (corpus-level keys only) when kind nodes sit beside it; `_nodes.is_graph`
    tells those apart.
    """
    if not meta.is_dir():
        return meta
    readme = meta / "README.md"
    if readme.is_file():
        return readme
    candidates = sorted(meta.glob("*.yaml"))
    return candidates[0] if candidates else meta / ONTOLOGY_FILENAME


def _ontology_sources(meta: Path) -> list[Path]:
    """One ontology directory per joined corpus, in order, skipping a corpus that holds none.

    Anything that is not a directory named `ontology` (a plain corpus root, a bare
    `.yaml` path) is returned as the one source.
    """
    if not meta.is_dir() or meta.name != ONTOLOGY_DIRNAME:
        return [meta]
    return home_directories(meta)


# Kind-node reading, graph-vs-document detection and `pnode` inheritance live in
# `lib.lexicon._nodes.graph_kinds`.


def _load_one_ontology(meta: Path) -> dict[str, Any] | None:
    """The ontology declared at the one directory `meta`, or `None` when it declares none.

    With kind nodes beside the index it is a graph: `README.md` carries the corpus-level
    keys (`discriminator`, `partition`, `base`, ...) and each `<kind>.md` carries what
    that kind requires and an optional `pnode` it inherits through. Without, it is a
    document: the index's `kinds:` block, or an `ontology.yaml`. Both return the same
    dict. A directory holding kind nodes and a `kinds:` block on its index is refused.
    """
    path = _ontology_path(meta)
    if not path.is_file():
        return None
    if path.suffix == ".md":
        declared = _frontmatter.load(path)
    else:
        declared = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # None when there are no kind nodes: the index is then the document.
    kinds = graph_kinds(meta, path, declared)
    return declared if kinds is None else {**declared, "kinds": kinds}


def load_ontology(meta: Path) -> dict[str, Any] | None:
    """The ontology in force at `meta`: the outer join of the joined corpora's ontologies.

    Returns `None` when none declares one. A kind declared in more than one corpus
    resolves to the first in `lexicon_corpora` order (custom, then the repo's own, then
    delivered), so a repo can narrow a delivered kind; `ontology_shadowed` reports the
    losers. Corpus-level keys resolve the same way. A document-and-graph refusal from
    `_load_one_ontology` propagates unchanged.
    """
    sources = _ontology_sources(meta)
    if len(sources) == 1:
        return _load_one_ontology(sources[0])
    joined: dict[str, Any] | None = None
    kinds: dict[str, Any] = {}
    for source in sources:
        one = _load_one_ontology(source)
        if one is None:
            continue
        for name, spec in (one.get("kinds") or {}).items():
            kinds.setdefault(name, spec)
        corpus_keys = {k: v for k, v in one.items() if k != "kinds"}
        joined = corpus_keys if joined is None else {**corpus_keys, **joined}
    if joined is None:
        return None
    return {**joined, "kinds": kinds} if kinds else joined


def ontology_shadowed(meta: Path) -> dict[str, Path]:
    """Every kind declared in more than one joined corpus, mapped to the source whose copy lost.

    Not a finding; `racecar.graph check` prints it.
    """
    seen: dict[str, Path] = {}
    out: dict[str, Path] = {}
    for source in _ontology_sources(meta):
        one = _load_one_ontology(source)
        for name in (one or {}).get("kinds") or {}:
            if name in seen:
                out[name] = source
            else:
                seen[name] = source
    return out


def acyclic_kinds(kinds: dict[str, dict[str, Any]]) -> bool:
    """Whether the ontology's `depends_on` graph is acyclic.

    Public because `lib.topology._walk` also calls it."""
    state: dict[str, int] = {}

    def walk(kind: str) -> bool:
        if state.get(kind) == 2:
            return True
        if state.get(kind) == 1:
            return False
        state[kind] = 1
        for dep in (kinds.get(kind) or {}).get("depends_on") or []:
            if dep in kinds and not walk(dep):
                return False
        state[kind] = 2
        return True

    return all(walk(kind) for kind in kinds)


def _partition_of(declared: dict[str, Any] | None) -> str | None:
    """The frontmatter field this corpus splits into projections along, or None.

    A corpus that declares no `partition:` has no projections.
    """
    value = declared.get("partition") if declared else None
    return str(value) if value else None


def project(
    sources: list[dict[str, Any]], field: str | None, domains: list[str]
) -> list[dict[str, Any]]:
    """The sub-corpus whose `field` names any of `domains`.

    A node claiming several domains appears in each of their projections.
    """
    if not field or not domains:
        return sources
    wanted = set(domains)
    out = []
    for source in sources:
        raw = source.get(field)
        claimed = {str(raw)} if isinstance(raw, str) else {str(x) for x in (raw or [])}
        if claimed & wanted:
            out.append(source)
    return out


def _discriminator_of(declared: dict[str, Any] | None) -> str:
    """The frontmatter field naming a source's kind: the declared override, or
    `DEFAULT_DISCRIMINATOR` absent one."""
    if declared and declared.get("discriminator"):
        return str(declared["discriminator"])
    return DEFAULT_DISCRIMINATOR


def check(data: Path, meta: Path, domains: list[str] | None = None) -> Fit:
    """Score `data` against the ontology declared at `meta`, optionally one projection of it.

    `domains` scores one projection of a corpus that declares `partition:`; it raises
    `ValueError` when none is declared, or when `kinds` has a `depends_on` cycle.
    """
    sources = sample(data)
    declared = load_ontology(meta)
    partition = _partition_of(declared)
    if domains:
        if partition is None:
            raise ValueError(
                f"{_ontology_path(meta)}: this corpus declares no `partition:`, so it has no "
                f"projections — cannot score --domain {', '.join(domains)}"
            )
        sources = project(sources, partition, domains)
    if not declared or not declared.get("kinds"):
        return Fit(best=None, unmapped_fraction=1.0, total=len(sources), by_kind={})

    name = str(declared.get("name") or "unnamed")
    kinds = declared["kinds"]
    if not acyclic_kinds(kinds):
        raise ValueError(
            f"{_ontology_path(meta)}: `kinds` has a cycle in `depends_on` — an "
            "ontology's own dependency graph must be acyclic"
        )
    base_required = (declared.get("base") or {}).get("required") or []
    discriminator = _discriminator_of(declared)

    matched = 0
    by_kind: dict[str, int] = {}
    for source in sources:
        raw_kind = source.get(discriminator)
        if not raw_kind:
            continue
        kind = str(raw_kind)
        spec = kinds.get(kind)
        if spec is None:
            continue
        required = [*base_required, *(spec.get("required") or [])]
        if all(field in source for field in required):
            matched += 1
            by_kind[kind] = by_kind.get(kind, 0) + 1

    total = len(sources)
    unmapped_fraction = 1.0 if total == 0 else 1 - (matched / total)
    best = name if unmapped_fraction < UNMAPPED_THRESHOLD else None
    return Fit(
        best=best, unmapped_fraction=unmapped_fraction, total=total, by_kind=by_kind
    )


_CARDINALITY_CHOICES = frozenset(
    {"one-to-one", "one-to-many", "many-to-one", "many-to-many"}
)


def relation_findings(  # pylint: disable=too-many-locals
    data: Path, meta: Path, topology_meta: Path | None = None
) -> list[str]:
    """Check every content node's use of a relation declared under `relations:`.

    `relations:` sits beside `kinds:` on the index, bundled rather than one node per
    relation (`ONTOLOGY.md` "Relations are declared, not just containment"). No
    `relations:` returns no findings.

    A node uses a relation by carrying a frontmatter field named for it, valued as a list
    of references resolved relative to the node's own directory, as `pnode` is. `from`/`to`
    name the kinds the relation may connect (`"any"` or absent means unconstrained).
    `cardinality` (`one-to-one`/`one-to-many`/`many-to-one`/`many-to-many`) is checked by
    in/out-degree per node; absent means unconstrained.
    """
    declared = load_ontology(meta)
    if not declared or not declared.get("relations"):
        return []
    relations = declared["relations"]
    nodes = _walk.load(data, topology_meta)

    errors: list[str] = []
    for name, spec in sorted(relations.items()):
        from_kind = spec.get("from")
        to_kind = spec.get("to")
        out_count: dict[str, int] = {}
        in_count: dict[str, int] = {}
        for nid, node in sorted(nodes.items()):
            refs = node.get(name)
            if not refs:
                continue
            if from_kind and from_kind != "any" and node.get("kind") != from_kind:
                errors.append(
                    f"{nid}: declares {name!r} but is kind {node.get('kind')!r}, "
                    f"not the declared `from: {from_kind}`"
                )
                continue
            for ref in refs:
                own_path = node["path"]
                base = own_path.parent if own_path.is_file() else own_path
                target_path = (base / ref).resolve()
                if not target_path.is_file():
                    errors.append(f"{nid}: {name} {ref!r} resolves to nothing")
                    continue
                try:
                    if target_path.name == "README.md":
                        target_id = target_path.parent.relative_to(data).as_posix()
                    else:
                        target_id = (
                            target_path.parent.relative_to(data) / target_path.stem
                        ).as_posix()
                except ValueError:
                    # Outside `data` entirely -- never a declared node either way.
                    target_id = (
                        target_path.parent.name
                        if target_path.name == "README.md"
                        else target_path.stem
                    )
                target = nodes.get(target_id)
                if target is None:
                    errors.append(
                        f"{nid}: {name} {ref!r} points outside the declared node set"
                    )
                    continue
                if to_kind and to_kind != "any" and target.get("kind") != to_kind:
                    errors.append(
                        f"{nid}: {name} -> {target_id} (kind "
                        f"{target.get('kind')!r}), not the declared `to: {to_kind}`"
                    )
                    continue
                out_count[nid] = out_count.get(nid, 0) + 1
                in_count[target_id] = in_count.get(target_id, 0) + 1

        cardinality = spec.get("cardinality")
        if cardinality in _CARDINALITY_CHOICES:
            from_one, to_one = cardinality.split("-to-")
            if from_one == "one":
                for target_id, count in sorted(in_count.items()):
                    if count > 1:
                        errors.append(
                            f"{name}: {target_id} has {count} incoming edges; "
                            f"declared `cardinality: {cardinality}` allows at most one"
                        )
            if to_one == "one":
                for nid, count in sorted(out_count.items()):
                    if count > 1:
                        errors.append(
                            f"{name}: {nid} has {count} outgoing edges; declared "
                            f"`cardinality: {cardinality}` allows at most one"
                        )
    return errors


def derive(data: Path, meta: Path | None = None, min_presence: float = 0.5) -> Proposal:
    """Propose kinds, required fields, and a best-effort DAG from `data` alone.

    `min_presence` is the fraction of a kind's sampled nodes a field must appear on to be
    proposed as required. Uses `meta`'s declared `discriminator` when given.
    """
    sources = sample(data)
    discriminator = _discriminator_of(load_ontology(meta) if meta else None)
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        kind = source.get(discriminator)
        if kind:
            by_kind.setdefault(kind, []).append(source)

    kinds: dict[str, list[str]] = {}
    values_by_kind: dict[str, set[str]] = {}
    for kind, nodes in by_kind.items():
        counts: dict[str, int] = {}
        for node in nodes:
            for field in node:
                if field in (discriminator, "_path"):
                    continue
                counts[field] = counts.get(field, 0) + 1
        kinds[kind] = sorted(
            field
            for field, count in counts.items()
            if count / len(nodes) >= min_presence
        )
        values_by_kind[kind] = {
            str(value)
            for node in nodes
            for field, value in node.items()
            if field not in (discriminator, "_path")
        }

    # Kind A depends on kind B when a field value on an A node is literally a value on
    # some B node: a referential signal, not a semantic one.
    dag: dict[str, list[str]] = {}
    for kind, nodes in by_kind.items():
        deps: set[str] = set()
        for node in nodes:
            for field, value in node.items():
                if field in (discriminator, "_path"):
                    continue
                for other_kind, values in values_by_kind.items():
                    if other_kind != kind and str(value) in values:
                        deps.add(other_kind)
        if deps:
            dag[kind] = sorted(deps)

    if not acyclic_kinds({kind: {"depends_on": deps} for kind, deps in dag.items()}):
        # A cyclic guess is dropped rather than proposed.
        dag = {}

    return Proposal(kinds=kinds, dag=dag, total=len(sources))


def render_ontology_yaml(name: str, proposal: Proposal) -> str:
    """A `derive()` proposal, rendered as the `ontology.yaml` shape this module
    itself reads — what `derive --meta` writes."""
    doc: dict[str, Any] = {
        "name": name,
        "kinds": {
            kind: {
                "required": fields,
                **({"depends_on": proposal.dag[kind]} if kind in proposal.dag else {}),
            }
            for kind, fields in sorted(proposal.kinds.items())
        },
    }
    return yaml.safe_dump(doc, sort_keys=False)
