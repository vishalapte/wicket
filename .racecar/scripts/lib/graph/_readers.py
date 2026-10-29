"""One reader per graph: its nodes, its edges, and where both were read from.

A reader returns the graph, not a measurement: every README count is `len()` of a
collection this produced, and the `(src, dst)` edge pairs feed the acyclicity check
(P-01).

Complexity: O(n) in tracked markdown plus one CLI-tree audit; the audit dominates.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The flat delivered checkers are siblings in `scripts/`, which `lib/__init__.py`
# puts on the path before any module here is imported.
from check_doc_graph import graph_edges, in_scope
from lib.graph._declared import declarations, reader_problem, resolve
from lib.lexicon._audit import cli_tree
from lib.lexicon._corpora import LexiconError, lexicon_corpora
from lib.shared._files import repo_files
from lib.shared._imports import closure, import_edges
from lib.shared._root import package_dir, package_root
from lib.topology import _walk

#: One edge: `(from, to)`, both node identifiers as that graph names them.
Edge = tuple[str, str]


class ReadError(Exception):
    """A reader reached its source and could not read it.

    Distinct from a reader returning None, which means the source is not there, so a
    broken tree never reads as a missing one.
    """


@dataclass(frozen=True)
class Read:
    """What a reader returns: one graph's nodes and edges, or None when its source is absent.

    `edges is None` means the edge set cannot be derived (the skill graph: prose links
    nothing declares). An empty tuple is a derived zero. An edge-based invariant runs
    against `()` and must refuse `None`.
    """

    nodes: tuple[str, ...]
    edges: tuple[Edge, ...] | None


@dataclass(frozen=True)
class Graph:
    """One graph as the README renders it: the authored shape plus the derived sizes.

    `edges` is `None` for a graph whose edge set cannot be derived; see `Read`.
    """

    name: str
    node: str
    edge: str
    nodes: int
    edges: int | None
    source: str


def _rel(path: Path, root: Path) -> str:
    """`path` as a repo-relative posix string, or absolute when it sits outside `root`."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _lines(path: Path) -> list[str]:
    """Non-empty lines of a jsonl file; `[]` when it is absent."""
    if not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _pnode_graph(nodes: list[Path], root: Path) -> Read:
    """A `pnode` containment graph over `nodes`: one edge per declared parent.

    Shared by the doc graph and the nomenclature graph. `graph_edges` is
    `check_doc_graph`'s own frontmatter parser; a doc may name several parents or none.
    """
    edges: list[Edge] = []
    for doc in nodes:
        parents, _see_also = graph_edges(
            doc.read_text(encoding="utf-8", errors="ignore")
        )
        for parent in parents or []:
            edges.append((_rel(doc, root), _rel(Path(parent), root)))
    return Read(tuple(sorted(_rel(p, root) for p in nodes)), tuple(sorted(edges)))


def _source(data: dict[str, Any]) -> tuple[str, str]:
    """A declaration's `source:` split into a path and an optional `#fragment`.

    The fragment says what within the file to read:

        pyproject.toml#tool.importlinter.contracts    a dotted table path
        surface.jsonl#cli>requires                    a node key, then an edge key
        racecar-manifest.jsonl#dest                   a node key, no edges

    Explicit, never inferred.
    """
    path, _, fragment = str(data.get("source") or "").strip().partition("#")
    return path.rstrip("/"), fragment


def read_pnode(root: Path, data: dict[str, Any]) -> Read | None:
    """Any `pnode` containment graph, its corpus named by the declaration's `source:`.

    `source:` selects the corpus: the repo's lexicon home means the lexicon, the union
    of every home (`lexicon_corpora`); any other directory means every `*.md` beneath
    it; anything else is a glob against the repo. Only files `in_scope` for
    `check_doc_graph` are counted.
    """
    source, _ = _source(data)
    if not source:
        return None
    corpora = lexicon_corpora(root)
    if (root / source).resolve() == corpora.own.resolve():
        # The source is the repo's lexicon, and the lexicon is the union of its homes,
        # never one folder of it.
        found = [corpora.path(entry) for entry in corpora.nodes()]
    elif (root / source).is_dir():
        found = sorted((root / source).rglob("*.md"))
    else:
        found = [p for p in repo_files(root, "*.md") if p.match(source)]
    docs = [p for p in found if in_scope(p, root)]
    if not docs:
        return None
    return _pnode_graph(docs, root)


def read_files(root: Path, data: dict[str, Any]) -> Read | None:
    """Files matching the declaration's `source:`, with no derivable edge set.

    For a graph whose nodes are files and whose edges nothing declares, such as the
    skill graph: `edges` is `None`.
    """
    source, _ = _source(data)
    if not source:
        return None
    pattern = source.rsplit("/", 1)[-1]
    found = [p for p in repo_files(root, pattern) if p.is_file() and p.match(source)]
    if not found:
        return None
    return Read(tuple(sorted(_rel(p, root) for p in found)), None)


def read_argparse(root: Path, data: dict[str, Any]) -> Read | None:
    """A command tree, introspected from the `__main__.py` modules under `source:`.

    Works for any CLI following the `commands()`/`subcommands()` contract; it imports
    the modules and reads the live argparse objects. An edge is a `subcommands()` entry:
    node to verb. Containment edges are not included.

    `source:` names the package root to walk (`src/racecar/**/__main__.py`), glob tail
    stripped, resolved against `root` only. A missing directory is absent.
    """
    source, _ = _source(data)
    top = source.split("*", 1)[0].rstrip("/") or "src"
    target = root / top
    if not target.is_dir():
        return None
    tree = _audit(root, target)

    # A `Node` is a dict whose children are under `children`, not `commands`.
    # `render_tree` is the authority on how a node descends.
    def walk(node: dict[str, Any]) -> list[dict[str, Any]]:
        return [node, *[d for k in node.get("children", []) or [] for d in walk(k)]]

    nodes = walk(tree)
    edges = tuple((_node_name(n), verb) for n in nodes for verb in _verb_names(n))
    return Read(tuple(sorted(_node_name(n) for n in nodes)), tuple(sorted(edges)))


def _audit(root: Path, target: Path) -> dict[str, Any]:
    """The CLI audit tree of the repo's package, through the lexicon's one route to it.

    `source:` must name the repo's one package or the package root (`src/`). Any other
    directory, no single package, or an incomplete audit raises `ReadError`.
    """
    where = _rel(target, root)
    package = package_dir(root)
    if package is None:
        raise ReadError(f"the CLI audit of `{where}` finds no single package")
    if target not in (package, package_root(root)):
        raise ReadError(f"the CLI audit reads `{_rel(package, root)}`, not `{where}`")
    try:
        return cli_tree(root)
    except LexiconError as err:
        raise ReadError(f"the CLI audit of `{where}`: {err}") from err


def _node_name(node: dict[str, Any]) -> str:
    """A CLI node's identity: its package path, which is what the tree is keyed on.

    `pkg`, not `command`, which carries the invocation prefix (`python -m
    racecar.graph`).
    """
    return str(node.get("pkg") or node.get("command") or "?")


def _verb_names(node: dict[str, Any]) -> list[str]:
    """The verb names a CLI node declares, however `subcommands()` shaped them.

    `audit_cli_tree` reports a subcommand as a `(name, blurb)` pair for most nodes and as
    a bare string for some.
    """
    out = []
    for entry in node.get("subcommands", []) or []:
        if isinstance(entry, (list, tuple)) and entry:
            out.append(str(entry[0]))
        elif isinstance(entry, dict):
            out.append(str(entry.get("name") or entry.get("verb") or "?"))
        else:
            out.append(str(entry))
    return out


def read_jsonl(root: Path, data: dict[str, Any]) -> Read | None:
    """Rows of a jsonl file as nodes, and an optional array field as edges.

    `source:` is `path#node_key>edge_key`, or `path#node_key` for a graph with no edge
    type. `edges` is `None` when no edge key is declared, and `()` when one is declared
    and unused.
    """
    source, fragment = _source(data)
    node_key, _, edge_key = fragment.partition(">")
    if not node_key:
        return None
    nodes: list[str] = []
    edges: list[Edge] = []
    for row in _lines(root / source):
        try:
            record = json.loads(row)
        except json.JSONDecodeError:
            continue
        rid = str(record.get(node_key, "?"))
        nodes.append(rid)
        for target in (record.get(edge_key) or []) if edge_key else []:
            edges.append((rid, str(target)))
    if not nodes:
        return None
    return Read(tuple(sorted(nodes)), tuple(sorted(edges)) if edge_key else None)


def read_toml(root: Path, data: dict[str, Any]) -> Read | None:
    """A list of tables in a TOML file as nodes, addressed by `source:`'s fragment.

    `source: "pyproject.toml#tool.importlinter.contracts"`: the fragment is the dotted
    path to the list, and each entry's `name` is the node. Edges are `None`: a table
    list carries membership, not relation.
    """
    source, fragment = _source(data)
    target = root / source
    if not target.is_file() or not fragment:
        return None
    with target.open("rb") as handle:
        loaded = tomllib.load(handle)
    for key in fragment.split("."):
        loaded = loaded.get(key, {}) if isinstance(loaded, dict) else {}
    if not isinstance(loaded, list):
        return None
    names = tuple(
        str(entry.get("name", f"entry {i}")) if isinstance(entry, dict) else str(entry)
        for i, entry in enumerate(loaded)
    )
    return Read(tuple(sorted(names)), None)


def read_topology(root: Path, data: dict[str, Any]) -> Read | None:
    """A corpus graded against its own DECLARED topology: nodes on disk, plus peer edges.

    Works for any corpus carrying `ontology/` and `topology/` beside its data; `source:`
    names the corpus root. Edges are the containment and peer edges from
    `_walk.structural_findings`; `read_pnode` would see containment only.
    `lib.topology` is imported directly: library wraps delivered, never the reverse.
    """
    source, _ = _source(data)
    meta = root / source
    if not meta.is_dir():
        return None
    _errors, on_disk, edges = _walk.structural_findings(meta, meta / "topology")
    pairs = tuple((src, dst) for src, targets in edges.items() for dst in targets)
    return Read(tuple(sorted(on_disk)), tuple(sorted(pairs)))


def read_imports(root: Path, data: dict[str, Any]) -> Read | None:
    """The transitive `@path` import closure from the entry file `source:` names.

    For any instruction-file chain (`CLAUDE.md`, `AGENTS.md`). The walk lives in
    `lib.shared._imports`, shared with `racecar.root.lib.selfcheck._baseline`, so the
    README row and its check read the same set.
    """
    source, _ = _source(data)
    entry = root / source
    if not entry.is_file():
        return None
    reached, _broken = closure(entry, root)
    return Read(
        tuple(sorted(_rel(p, root) for p in reached)), import_edges(reached, root)
    )


def _read(root: Path, data: dict[str, Any]) -> Read | None:
    """One declaration's `Read`, with a `ReadError` renamed for the node that asked.

    The node's file name is prefixed, the `<node>.md: ...` shape `malformed` and
    `unresolvable` use.
    """
    try:
        found: Read | None = resolve(data["reader"])(root, data)
    except ReadError as exc:
        raise ReadError(f"{data['_node'].name}: {exc}") from exc
    return found


def build(root: Path) -> list[Graph]:
    """One `Graph` per DECLARED graph: the authored shape, plus sizes from its reader.

    The declaration says what a node is and where the graph is read from; the reader
    produces the nodes and edges. Every size is `len()` of what the reader returned.

    A reader returning None means the source is absent, and the row reads `absent`. A
    reader that cannot read its source raises `ReadError`, and no row is built.
    """
    graphs: list[Graph] = []
    for data in declarations(root):
        # An unresolvable reader is the caller's finding (`unresolvable`); skip its row
        # so the other graphs still build.
        if reader_problem(str(data.get("reader") or "")) is not None:
            continue
        read = _read(root, data)
        graphs.append(
            Graph(
                data["name"],
                data["node"],
                data["edge"],
                len(read.nodes) if read is not None else 0,
                None if read is None or read.edges is None else len(read.edges),
                data["source"] if read is not None else "absent",
            )
        )
    return graphs


def read_all(root: Path) -> dict[str, Read]:
    """Every declared graph's `Read`, keyed by declared name; absent sources omitted.

    The data path for invariants; `build` is the rendering path.
    """
    out: dict[str, Read] = {}
    for data in declarations(root):
        if reader_problem(str(data.get("reader") or "")) is not None:
            continue
        read = _read(root, data)
        if read is not None:
            out[str(data["name"])] = read
    return out
