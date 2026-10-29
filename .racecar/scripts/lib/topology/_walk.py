"""Declare, walk by, and grade a corpus's topology: the tier shape a graph's containment
follows, orthogonal to its ontology (the type system). See `TOPOLOGY.md`.

Delivered as `lib/topology/` and run in an adopter's `.racecar/scripts/` with no racecar
installed, so it imports only delivered modules (`lib.lexicon._corpora`, `lib.shared._root`).
`racecar.graph.topology.lib._verbs` wraps this module; never the reverse.

The verbs match `lib.ontology._kinds`: `load_topology_spec` reads the declaration,
`check(data, meta, domains)` grades a corpus, `relation_findings`/`structural_findings` return
findings, and `_partition_of` and `project` cut one projection out of a partitioned corpus.

`topology.yaml` says where a node subtree is rooted and how deep it may nest:

    name: architecture
    root_pattern: '^(?P<id>[PR]\\d\\d)-[a-z]+$'
    tiers: [rule, group, setting]

`root_pattern` selects which top-level directories are node-subtree roots. `tiers` bounds the
depth at `len(tiers)` levels. A node's tier position and its declared `kind:` are read
independently.

A topology lives at `<meta>/topology/` as a document or a graph. An index with its own `tiers:`
list is the document. The index with `<tier>.md` nodes beside it, each declaring `name:` and
`carrier:`, is the graph; each tier's `pnode` is its place in the chain (the first tier's is the
index, each later one's is the tier above). `load_topology_spec` assembles the same `tiers:` list
from either shape.

A `meta` with no declared topology walks permissively: every directory holding a `README.md` is
a candidate node, with no root-pattern restriction and no depth bound.

Complexity: O(N), N = nodes walked (one frontmatter parse each) x C corpora joined
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from lib.lexicon._corpora import home_directories
from lib.shared import _frontmatter
from lib.shared._root import find_repo_root

# The repo this runs in (the adopter's, once delivered), and its architecture tree.
ROOT = find_repo_root()
META_ROOT = ROOT / "architecture"

TOPOLOGY_FILENAME = "topology.yaml"


class Tier(NamedTuple):
    """One tier of a declared topology: its name, and what carries a node at it.

    `carrier` is `"dir"` (default: a node is a directory holding a `README.md`) or `"file"`
    (the node is a `.md` file). Only the last tier may be `"file"`.
    """

    name: str
    carrier: str = "dir"


def _parse_tier(spec: Any) -> Tier:
    """One `tiers:` list entry -- a bare name (a directory tier) or a
    `{name, carrier}` mapping -- resolved to a `Tier`."""
    if isinstance(spec, dict):
        carrier = str(spec.get("carrier") or "dir")
        if carrier not in ("dir", "file"):
            raise ValueError(
                f"tier {spec.get('name')!r}: carrier must be 'dir' or 'file', got "
                f"{carrier!r}"
            )
        return Tier(name=str(spec["name"]), carrier=carrier)
    return Tier(name=str(spec))


class Topology(NamedTuple):
    """A declared topology, resolved to what `load` actually needs."""

    name: str
    root_pattern: re.Pattern[str]
    tiers: tuple[Tier, ...]


def _topology_path(meta: Path) -> Path:
    """The declared topology's source at `meta`.

    `meta`'s own `README.md` first, then a `*.yaml` fallback. A `meta` that is not a
    directory is used as given.
    """
    if not meta.is_dir():
        return meta
    readme = meta / "README.md"
    if readme.is_file():
        return readme
    candidates = sorted(meta.glob("*.yaml"))
    return candidates[0] if candidates else meta / TOPOLOGY_FILENAME


# A topology's one home: `<meta>/topology/`. An index alone is a document; an index
# with tier nodes beside it is a graph.
TOPOLOGY_DIRNAME = "topology"


def _is_tier_node(path: Path) -> bool:
    """Whether `path` declares a tier: it carries both `name:` and `carrier:`.

    `name:` alone does not suffice: every node carries it, including racecar's own
    `docs/lexicon/graph/topology/`, which is a corpus node rather than a meta.
    """
    if path.name == "README.md":
        return False
    frontmatter = _frontmatter.load(path)
    return bool(frontmatter.get("name")) and bool(frontmatter.get("carrier"))


def _is_graph(meta: Path, declaration: Path) -> bool:
    """Whether `meta` is the GRAPH shape: a `topology` directory of tier nodes."""
    return (
        meta.is_dir()
        and meta.name == TOPOLOGY_DIRNAME
        and declaration.name == "README.md"
        and any(
            _is_tier_node(p)
            for source in _topology_sources(meta)
            for p in source.glob("*.md")
        )
    )


def _topology_sources(meta: Path) -> list[Path]:
    """One `topology/` directory per joined corpus, in order, skipping a corpus that holds none.

    The mirror of `lib.ontology._kinds._ontology_sources`: a corpus's tiers come from every
    corpus joined to make it, so racecar's delivered `noun` and `verb` tiers
    (`.racecar/docs/lexicon/topology/`) reach the repo's own index. Without the join the walk
    falls back to permissive and containment goes unchecked.

    Anything that is not a directory named `topology` is one source and nothing else.

    A corpus that declares a topology of its own (a `tiers:` list or tier nodes) is not
    joined: its own declaration wins and the delivered tiers are not read. Joining it would
    make `load_topology_spec` raise on a conflict the adopter did not create.
    """
    if not meta.is_dir() or meta.name != TOPOLOGY_DIRNAME:
        return [meta]
    declares_its_own = bool(
        (_frontmatter.load(_topology_path(meta)) or {}).get("tiers")
    ) or any(_is_tier_node(p) for p in meta.glob("*.md"))
    if declares_its_own:
        return [meta]
    return home_directories(meta)


def _tiers_from_nodes(meta: Path) -> list[Any]:
    """The `tiers:` list a directory of tier nodes declares, in order.

    The order is the `pnode` chain: the first tier's `pnode` is the index, each later tier's
    is the tier above it. Rendered in the row shape `_parse_tier` reads: a bare name for a
    `carrier: dir` tier, a `{name, carrier}` mapping otherwise.
    """
    nodes = {
        p.resolve(): _frontmatter.load(p)
        for source in _topology_sources(meta)
        for p in source.glob("*.md")
    }
    nodes = {p: fm for p, fm in nodes.items() if _is_tier_node(p)}
    parent_of: dict[Path, Path | None] = {}
    for path, frontmatter in nodes.items():
        refs = [
            (path.parent / str(ref)).resolve()
            for ref in (frontmatter.get("pnode") or [])
        ]
        tiers_above = [ref for ref in refs if ref in nodes and ref != path]
        if len(tiers_above) > 1:
            raise ValueError(
                f"{path}: names {len(tiers_above)} tiers in its `pnode` — tiers are "
                "an ordered chain, so a tier has at most one tier above it"
            )
        parent_of[path] = tiers_above[0] if tiers_above else None

    roots = [path for path, parent in parent_of.items() if parent is None]
    if len(roots) != 1:
        named = ", ".join(sorted(p.name for p in roots)) or "none"
        raise ValueError(
            f"{meta}: a topology is one ordered chain, so exactly one tier takes its "
            f"`pnode` from the index and every other from the tier above it; found "
            f"{len(roots)} without a tier above ({named})"
        )
    children: dict[Path, list[Path]] = {}
    for path, parent in parent_of.items():
        if parent is not None:
            children.setdefault(parent, []).append(path)
    for parent, kids in children.items():
        if len(kids) > 1:
            named = ", ".join(sorted(p.name for p in kids))
            raise ValueError(
                f"{parent}: {len(kids)} tiers name it as the tier above them "
                f"({named}) — a topology is a chain, not a branching tree"
            )

    order: list[Path] = []
    cursor: Path | None = roots[0]
    while cursor is not None:
        if cursor in order:
            raise ValueError(f"{cursor}: the tier chain loops back on itself")
        order.append(cursor)
        kids = children.get(cursor) or []
        cursor = kids[0] if kids else None
    if len(order) != len(nodes):
        stranded = ", ".join(sorted(p.name for p in nodes if p not in order))
        raise ValueError(
            f"{meta}: {stranded} is not reachable from the first tier — every tier "
            "belongs to the one chain"
        )
    return [
        (
            str(nodes[path]["name"])
            if str(nodes[path]["carrier"]) == "dir"
            else {
                "name": str(nodes[path]["name"]),
                "carrier": str(nodes[path]["carrier"]),
            }
        )
        for path in order
    ]


def load_topology_spec(meta: Path) -> dict[str, Any] | None:
    """The raw declared topology, or `None` when `meta` declares none.

    One dict whichever shape it is written in. For a graph, the index supplies `root_pattern`
    and the corpus-level keys and the tier nodes supply `tiers:`.
    """
    path = _topology_path(meta)
    if not path.is_file():
        return None
    if path.suffix == ".md":
        spec = _frontmatter.load(path)
    else:
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not _is_graph(meta, path):
        return spec
    if spec.get("tiers"):
        raise ValueError(
            f"{path}: declares a `tiers:` list AND has tier nodes beside it — a "
            "topology is a document or a graph, never both"
        )
    return {**spec, "tiers": _tiers_from_nodes(meta)}


def resolve(meta: Path | None) -> Topology | None:
    """A declared topology resolved to a compiled pattern + tier tuple, or `None`.

    `None` means no declared topology; the caller walks permissively. Raises `ValueError` on a
    malformed `root_pattern`.
    """
    if meta is None:
        return None
    spec = load_topology_spec(meta)
    if not spec or not spec.get("root_pattern") or not spec.get("tiers"):
        return None
    try:
        pattern = re.compile(str(spec["root_pattern"]))
    except re.error as exc:
        raise ValueError(
            f"{_topology_path(meta)}: `root_pattern` does not compile: {exc}"
        ) from exc
    tiers = tuple(_parse_tier(t) for t in spec["tiers"])
    file_tiers = [i for i, t in enumerate(tiers) if t.carrier == "file"]
    if file_tiers and file_tiers != [len(tiers) - 1]:
        raise ValueError(
            f"{_topology_path(meta)}: only the last tier may be `carrier: file` "
            f"(a file cannot contain a further tier); got it at "
            f"{[tiers[i].name for i in file_tiers]}"
        )
    return Topology(
        name=str(spec.get("name") or "unnamed"), root_pattern=pattern, tiers=tiers
    )


# The permissive fallback for a corpus with no declared topology: every directory holding a
# README.md is a candidate node, no root-pattern restriction, no depth bound. `None` tiers
# means unbounded depth; the depth check special-cases it.
_FALLBACK_ROOT_PATTERN = re.compile(r".*")
_FALLBACK_TIERS: tuple[Tier, ...] | None = None


def load(
    data: Path | None = None, meta: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Every node, read fresh from the tree at `data` (racecar's own `architecture/` absent one).

    Kind is read from each node's own `kind:` frontmatter, not from depth. A node with no
    `kind` loads with `kind: None`; the composite check reports it.

    Keyed by path relative to `data`, because a basename recurs across containers. `name` is
    the basename; `id` and the dict key are the path. A `carrier: file` leaf's id drops `.md`.

    Root discovery and the depth bound come from `meta`'s declared topology (`meta` absent is
    `data`). A `meta` with no declared topology walks permissively.
    """
    tax = (data if data is not None else META_ROOT).resolve()
    resolved = resolve(meta if meta is not None else tax / "topology")
    root_pattern = resolved.root_pattern if resolved else _FALLBACK_ROOT_PATTERN
    tiers = resolved.tiers if resolved else _FALLBACK_TIERS

    nodes: dict[str, dict[str, Any]] = {}
    # The walk is anchored on the declared (or permissive) root pattern, which excludes
    # siblings such as architecture/'s own `scripts/`, `canon/` and PRINCIPLES.md.
    for d in sorted(p for p in tax.rglob("*") if p.is_dir()):
        rel = d.relative_to(tax).parts
        if not root_pattern.match(rel[0]):
            continue
        readme = d / "README.md"
        if (tiers is not None and len(rel) > len(tiers)) or not readme.is_file():
            continue
        fm = _frontmatter.load(readme)
        node_id = d.relative_to(tax).as_posix()
        nodes[node_id] = {
            **fm,
            "id": node_id,
            "name": d.name,
            "kind": fm.get("kind"),
            "serves": d.parent.relative_to(tax).as_posix() if len(rel) > 1 else None,
            "path": d,
        }

    # A `carrier: file` last tier: each node is a `.md` file one level under a directory at
    # the second-to-last tier, which the loop above already walked.
    if tiers and tiers[-1].carrier == "file":
        parent_depth = len(tiers) - 1
        for d in sorted(p for p in tax.rglob("*") if p.is_dir()):
            rel = d.relative_to(tax).parts
            if not root_pattern.match(rel[0]) or len(rel) != parent_depth:
                continue
            for leaf in sorted(d.glob("*.md")):
                if leaf.name == "README.md":
                    continue
                fm = _frontmatter.load(leaf)
                leaf_id = (d.relative_to(tax) / leaf.stem).as_posix()
                nodes[leaf_id] = {
                    **fm,
                    "id": leaf_id,
                    "name": leaf.stem,
                    "kind": fm.get("kind"),
                    "serves": d.relative_to(tax).as_posix(),
                    "path": leaf,
                }
    return nodes


def structural_findings(  # pylint: disable=too-many-statements
    data: Path, meta: Path | None = None
) -> tuple[list[str], dict[str, Path], dict[str, list[str]]]:
    """Containment + peer-DAG findings for `data`, graded against `meta`'s declared
    topology (or the permissive fallback).

    Returns `(errors, on_disk, peer_edges)`; `check` and `graph.py` reuse the last two.
    """

    def frontmatter_of(node_path: Path) -> Path:
        """The file carrying `node_path`'s own frontmatter — itself, for a
        `carrier: file` leaf; `README.md` inside it otherwise."""
        return node_path if node_path.is_file() else node_path / "README.md"

    def node_of(carrier: Path) -> Path:
        """The inverse of `frontmatter_of`: the node a frontmatter FILE belongs to
        — its own directory for a `README.md`, or the file itself for a leaf."""
        return carrier.parent if carrier.name == "README.md" else carrier

    def node_id(node_path: Path) -> str:
        """The id `load` gave this node -- its path relative to `data`,
        minus the `.md` suffix for a `carrier: file` leaf."""
        if node_path.is_dir():
            return node_path.relative_to(data).as_posix()
        return (node_path.parent.relative_to(data) / node_path.stem).as_posix()

    declared = load(data, meta)
    on_disk = {nid: n["path"] for nid, n in declared.items()}

    errors: list[str] = []

    for nid, d in on_disk.items():
        readme = frontmatter_of(d)
        if not readme.is_file():
            errors.append(f"{nid}: node directory has no README.md")
            continue
        # One parent, any number of siblings, nothing deeper. A pnode target one level up
        # is the parent (containment); a target at the same depth is a peer (precedence).
        # A target deeper than this node is an error.
        refs = declared[nid].get("pnode") or []
        here = len(d.relative_to(data).parts)
        parents: list[str] = []
        for ref in refs:
            target = (readme.parent / ref).resolve()
            if not target.is_file():
                errors.append(f"{nid}: pnode {ref!r} resolves to nothing")
                continue
            try:
                there = len(node_of(target).relative_to(data).parts)
            except ValueError:
                errors.append(f"{nid}: pnode {ref!r} points outside the corpus")
                continue
            own_parent = (d.parent / "README.md").resolve()
            if there == here - 1:
                # The filesystem parent, not any node one level up: depth alone admits an
                # uncle in another branch.
                if target != own_parent:
                    errors.append(
                        f"{nid}: pnode {ref!r} is one level up but is not this node's "
                        f"parent. A parent edge names the containing directory and "
                        f"nothing else; any other d-1 node is an uncle."
                    )
                else:
                    parents.append(ref)
            elif there == here:
                # Same container: a peer edge joins nodes under one parent.
                if node_of(target).parent != d.parent:
                    errors.append(
                        f"{nid}: peer {ref!r} sits under "
                        f"{node_of(target).parent.name!r}, not {d.parent.name!r} — a "
                        f"peer edge stays inside one container. Crossing one means the "
                        f"two share a parent they have not declared, which is a "
                        f"compound to decompose."
                    )
            else:
                errors.append(
                    f"{nid}: pnode {ref!r} is at depth {there}; this node is at {here}. "
                    f"A parent sits one above, a peer sits level; nothing may sit below."
                )
        if len(parents) != 1:
            errors.append(
                f"{nid}: has {len(parents)} parent pnode(s) at depth {here - 1}; "
                f"exactly one is what makes containment a tree"
            )

    # Peer edges, held acyclic.
    edges: dict[str, list[str]] = {}
    for nid, d in on_disk.items():
        here = len(d.relative_to(data).parts)
        peers = []
        for ref in declared[nid].get("pnode") or []:
            target = (frontmatter_of(d).parent / ref).resolve()
            if not target.is_file():
                continue
            peer = node_of(target)
            # A pnode outside the corpus is reported by the first loop; it has no depth here.
            if peer == data or not peer.is_relative_to(data):
                continue
            if len(peer.relative_to(data).parts) == here:
                peers.append(node_id(peer))
        edges[nid] = peers

    seen: dict[str, int] = {}

    def walk(nid: str, stack: list[str]) -> None:
        if seen.get(nid) == 2:
            return
        if seen.get(nid) == 1:
            errors.append("peer cycle: " + " -> ".join(stack + [nid]))
            return
        seen[nid] = 1
        for t in edges.get(nid, []):
            if t in declared:
                walk(t, stack + [nid])
        seen[nid] = 2

    for nid in sorted(declared):
        walk(nid, [])

    return errors, on_disk, edges


def _partition_of(declared: dict[str, Any] | None) -> str | None:
    """The frontmatter field this corpus splits into projections along, or None.

    Read from the topology's own `partition:` declaration (`ontology._partition_of` reads the
    ontology's). A corpus that declares none has no projections.
    """
    value = declared.get("partition") if declared else None
    return str(value) if value else None


def project(
    nodes: dict[str, dict[str, Any]], field: str | None, domains: list[str]
) -> dict[str, dict[str, Any]]:
    """The sub-corpus whose `field` names any of `domains`: a subgraph, not a filter.

    Keyed by node id over the walked tree. A node claiming several domains appears in each of
    their projections.
    """
    if not field or not domains:
        return nodes
    wanted = set(domains)
    out: dict[str, dict[str, Any]] = {}
    for nid, node in nodes.items():
        raw = node.get(field)
        claimed = {str(raw)} if isinstance(raw, str) else {str(x) for x in (raw or [])}
        if claimed & wanted:
            out[nid] = node
    return out


def check(
    data: Path | None = None,
    meta: Path | None = None,
    domains: list[str] | None = None,
) -> dict[str, Any]:
    """The topology at `data` as a graph, and every way it breaks the declared shape.

    `data` absent is racecar's own `architecture/`; `meta` absent is `<data>/topology`.
    `nodes` maps each node id to the node `load` reads, `contains` maps it to its parent's
    id (None for a root), and `peers` to the ids it declares as peers. `errors` is empty
    when the tier shape holds. Prints nothing: `lib.topology._check` is the verb over it.

    `domains` grades one projection; there is no union pass. A projection stays rooted only
    while each node's domains are a subset of its parent's.
    """
    tax = (data if data is not None else META_ROOT).resolve()
    if not tax.is_dir():
        return {
            "nodes": {},
            "contains": {},
            "peers": {},
            "errors": [f"{tax} is not a directory"],
        }
    meta_path = meta if meta is not None else tax / "topology"

    errors, on_disk, edges = structural_findings(tax, meta_path)
    declared = load(tax, meta_path)

    def _parent_id(d: Path) -> str | None:
        if d.parent == tax:
            return None
        pid = d.parent.relative_to(tax).as_posix()
        return pid if pid in declared else None

    contains = {nid: _parent_id(d) for nid, d in on_disk.items()}

    if domains:
        field = _partition_of(load_topology_spec(meta_path))
        if field is None:
            errors.append(
                f"{_topology_path(meta_path)}: this corpus declares no `partition:`, so it "
                f"has no projections — cannot check --domain {', '.join(domains)}"
            )
            return {"nodes": {}, "contains": {}, "peers": {}, "errors": errors}
        keep = set(project(declared, field, domains))
        for nid in sorted(keep):
            parent = contains.get(nid)
            if parent is not None and parent not in keep:
                errors.append(
                    f"{nid}: present in domain(s) {', '.join(sorted(domains))} but its parent "
                    f"{parent} is not — that leaves this projection rootless at {nid}."
                )
        declared = {k: v for k, v in declared.items() if k in keep}
        contains = {k: v for k, v in contains.items() if k in keep}
        edges = {k: v for k, v in edges.items() if k in keep}

    return {"nodes": declared, "contains": contains, "peers": edges, "errors": errors}
