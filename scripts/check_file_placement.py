#!/usr/bin/env python3
"""Mechanical check: every markdown doc is reachable from the resolver chain.

Reference-driven placement (`doc-coherence/README.md`, "Documentation placement"):
a doc is correctly placed when a resolver reaches it, not when its filename matches
a fixed allowlist. The README of a directory is that directory's manifest; the root
`README.md` and the agent-instruction file (`AGENTS.md`, or `CLAUDE.md` in a repo
that has not migrated) are the entry resolvers. This check builds the resolver
graph over the repo's markdown, seeds it with those roots, and flags any doc
nothing in the chain reaches — an orphan no reader can navigate to. That is the one
mechanical thing that keeps the resolver honest: a doc you forget to register fails
the gate instead of rotting unreferenced.

Two edge kinds count, because a repo has two ways to declare where a doc hangs:

  - a markdown link from a doc already reachable (README-as-manifest), and
  - a `pnode` frontmatter edge (DOC_GRAPH.md) from a reachable parent to this doc.
    The parse is `check_doc_graph.graph_edges`, so the edge has one definition.

The second is what a `docs/` tree normally uses: a generated page (`docs/cli/**`)
declares its parent rather than waiting for a hand-written link, and a hand-written
`docs/ARCHITECTURE.md` declares `pnode: [../README.md]`. Either way the rule is the
same everywhere in the repo — every doc resolves back to the root README, directly
or through a chain. `docs/` is not exempt and is not an overflow area; it is a
normal part of the surface whose docs are usually registered by `pnode`.

No fixed taxonomy: there is no hardcoded list of allowed filenames or required
sections. Extra docs are welcome — the required-docs manifest
(`docs-orchestrator/ORCHESTRATION.md`) is a floor, never a ceiling. A repo declares
what its docs are by registering them.

Scope: markdown only; build/config files are out of scope. The llm-summary briefs
under `docs/summary/` are exempt — a generated artifact with its own frontmatter
schema and lifecycle, excluded from the doc graph for the same reason. Hidden
directories and `[tool.pylint.MASTER].ignore-paths` are skipped, same as check_docs.
Nothing here is repo-specific; the repo root is discovered at runtime.

Exit 0 if clean, 1 if any orphan (or an agent-instruction file without a sibling
README.md) is found.

Usage:
    python3 <path-to>/check_file_placement.py

Complexity: O(V+E), V=repo markdown files, E=md-link+pnode edges (multi-source DFS)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from check_doc_graph import graph_edges
from check_docs import AGENT_DOC_NAMES, ignore_patterns
from check_packaging_rules._files import repo_files
from check_packaging_rules._root import find_repo_root

# Entry points: reachability is seeded from these. README.md is the human resolver
# and the agent-instruction file (AGENTS.md, or CLAUDE.md in a repo that has not
# migrated) is the agent baseline/resolver (force-loaded), both at the repo root.
# BOTH names seed, not just the primary: a repo where one points at the other has two
# real entry points, and seeding only the winner would orphan the pointer.
# SKILL.md is an entry point at ANY level — the harness invokes it as a slash
# command, so it is reached by invocation, not by a README link, and it pulls in its
# own content (its "Load <doc>" line). A doc is legitimately placed when the chain
# from one of these reaches it.
ROOT_SEED_NAMES = ("README.md", *AGENT_DOC_NAMES)
SEED_ANYWHERE = "SKILL.md"

# Exempt from reachability: the llm-summary briefs. They are generated single-file
# packages with their own frontmatter schema, and check_doc_graph excludes them from
# the doc graph for the same reason (its EXCLUDED_PATHS).
EXEMPT_PREFIXES = (Path("docs/summary"),)

# Inline markdown link target: the `(path)` of `[text](path)`.
_LINK_RE = re.compile(r"\]\(([^)]+)\)")
REPO_ROOT = find_repo_root()

# Same ignore-paths check_docs honors, so one declaration scopes out a data/ tree.
IGNORE_PATTERNS = ignore_patterns(REPO_ROOT)


class Finding:
    """A single file-placement violation at a relative path."""

    def __init__(self, rel: str, message: str) -> None:
        self.rel = rel
        self.message = message

    def render(self) -> str:
        """Render the finding as a `  path  message` report line."""
        return f"  {self.rel}  {self.message}"


def _markdown_files() -> list[Path]:
    out: list[Path] = []
    for p in sorted(repo_files(REPO_ROOT, "*.md")):
        rel = p.relative_to(REPO_ROOT)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if any(pat.search(rel.as_posix()) for pat in IGNORE_PATTERNS):
            continue
        out.append(p)
    return out


def _md_link_targets(md_path: Path, text: str) -> set[Path]:
    """Repo-relative paths of the .md files this doc links to (anchors stripped)."""
    targets: set[Path] = set()
    for match in _LINK_RE.finditer(text):
        parts = match.group(1).strip().split()  # drop any "(path \"title\")"
        if not parts:  # `[text]( )` — empty target; check_docs reports it
            continue
        raw = parts[0]
        raw = raw.split("#", 1)[0]  # drop the #anchor
        if not raw.endswith(".md") or raw.startswith(
            ("http://", "https://", "mailto:")
        ):
            continue
        resolved = (md_path.parent / raw).resolve()
        try:
            targets.add(resolved.relative_to(REPO_ROOT))
        except ValueError:
            continue  # link escapes the repo; not our concern
    return targets


def _resolve(md_path: Path, ref: str) -> Path | None:
    """A ref relative to the doc's own directory, as a repo-relative path (None if outside)."""
    try:
        return (md_path.parent / ref).resolve().relative_to(REPO_ROOT)
    except ValueError:
        return None


def _pnode_edges(md_path: Path, text: str) -> list[tuple[Path, Path]]:
    """Declared parent -> this doc edges, from the doc's `pnode` frontmatter.

    The reverse of what the doc graph stores: `pnode` names the parent, and
    reachability walks downward, so each declared parent gains an edge to this child.
    """
    pnode, _ = graph_edges(text)
    child = md_path.relative_to(REPO_ROOT)
    parents = (_resolve(md_path, ref) for ref in pnode or ())
    return [(parent, child) for parent in parents if parent is not None]


def _reachable(md_files: list[Path]) -> set[Path]:
    """The set of repo-relative docs reachable from the seed entry points.

    Each file is read once and the text handed to both `_md_link_targets` and
    `_pnode_edges` -- they used to each call `read_text` independently, doubling the
    I/O for every doc in the tree for no reason, since both readers want the same bytes.

    The walk below (`stack` + `seen`) is textbook multi-source DFS reachability:
    push every seed, pop-mark-expand until the stack empties. A doc absent from
    `seen` afterward is unreachable -- the graph-theoretic definition of an orphan.
    """
    texts = {p: p.read_text(encoding="utf-8") for p in md_files}
    graph = {p.relative_to(REPO_ROOT): _md_link_targets(p, texts[p]) for p in md_files}
    for p in md_files:
        for parent, child in _pnode_edges(p, texts[p]):
            graph.setdefault(parent, set()).add(child)
    stack = [Path(n) for n in ROOT_SEED_NAMES if (REPO_ROOT / n).is_file()]
    stack += [rel for rel in graph if rel.name == SEED_ANYWHERE]
    seen: set[Path] = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(t for t in graph.get(cur, ()) if t not in seen)
    return seen


def _exempt(rel: Path) -> bool:
    """Generated trees outside the navigable surface (the llm-summary briefs)."""
    return any(prefix in rel.parents for prefix in EXEMPT_PREFIXES)


def check_placement() -> list[Finding]:
    """Find misplaced or unreachable markdown and orphan agent-instruction files."""
    md_files = _markdown_files()
    reachable = _reachable(md_files)
    findings: list[Finding] = []
    agent_docs: list[Path] = []

    for path in md_files:
        rel = path.relative_to(REPO_ROOT)
        if path.name in AGENT_DOC_NAMES:
            agent_docs.append(path)
        if _exempt(rel) or rel in reachable:
            continue
        findings.append(
            Finding(
                rel.as_posix(),
                "orphan: no resolver reaches it — link it from a README (or the "
                "root agent-instruction file), or declare its `pnode` parent "
                "(DOC_GRAPH.md)",
            )
        )

    # An agent-instruction file still requires a sibling README.md: agent context does
    # not stand without a human landing (the README/agent-doc pair, not a filename
    # allowlist).
    for doc in sorted(agent_docs):
        if not (doc.parent / "README.md").is_file():
            findings.append(
                Finding(
                    doc.relative_to(REPO_ROOT).as_posix(),
                    f"{doc.name} present without a sibling README.md "
                    "(README is required first)",
                )
            )

    return findings


def main() -> int:
    """Run the file-placement check and return an exit code."""
    findings = check_placement()
    if not findings:
        print("file-placement: OK")
        return 0
    print(f"file-placement: {len(findings)} orphan doc(s)")
    for f in findings:
        print(f.render())
    return 1


if __name__ == "__main__":
    sys.exit(main())
