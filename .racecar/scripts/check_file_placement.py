#!/usr/bin/env python3
"""Mechanical check: every markdown doc is reachable from the resolver chain.

Reference-driven placement (`doc-coherence/README.md`, "Documentation placement"): a doc
is placed when a resolver reaches it. The entry resolvers are the root `README.md`, the
agent-instruction files (`AGENTS.md`, and the `CLAUDE.md` that imports it), and every
`SKILL.md`. Any doc nothing in the chain reaches is an orphan.

Two edge kinds count:

  - a markdown link from a doc already reachable (README-as-manifest), and
  - a `pnode` frontmatter edge (DOC_GRAPH.md) from a reachable parent to this doc.
    The parse is `check_doc_graph.graph_edges`.

The rule holds everywhere, `docs/` included. There is no list of allowed filenames.

Scope: markdown only. The llm-summary briefs under `docs/summary/` are exempt, as in the
doc graph. Hidden directories and `[tool.pylint.MASTER].ignore-paths` are skipped, same as
check_docs.

Exit 0 if clean, 1 if any orphan (or an agent-instruction file without a sibling
README.md) is found.

Usage:
    python3 <path-to>/check_file_placement.py

Complexity: O(V+E), V=repo markdown files, E=md-link+pnode edges (multi-source DFS)
"""

from __future__ import annotations

import sys
from pathlib import Path

from check_doc_graph import graph_edges
from check_docs import ignore_patterns
from lib.shared import _markdown
from lib.shared._agent_docs import AGENT_DOC_NAMES
from lib.shared._files import is_hidden_path, repo_files
from lib.shared._root import find_repo_root

# Entry points: README.md and every agent-instruction name at the repo root (all seed, so
# a pointer file is not orphaned), and SKILL.md at any level, which the harness invokes.
ROOT_SEED_NAMES = ("README.md", *AGENT_DOC_NAMES)
SEED_ANYWHERE = "SKILL.md"

# Exempt from reachability: the llm-summary briefs (as in check_doc_graph's EXCLUDED_PATHS).
EXEMPT_PREFIXES = (Path("docs/summary"),)

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
        if is_hidden_path(rel):
            continue
        if any(pat.search(rel.as_posix()) for pat in IGNORE_PATTERNS):
            continue
        out.append(p)
    return out


def _md_link_targets(md_path: Path, text: str) -> set[Path]:
    """Repo-relative paths of the .md files this doc links to (anchors stripped)."""
    targets: set[Path] = set()
    for link in _markdown.parse(text).links():
        parts = link.target.split()  # drop any "(path \"title\")"
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
    """Declared parent -> this doc edges, from the doc's `pnode` frontmatter."""
    pnode, _ = graph_edges(text)
    child = md_path.relative_to(REPO_ROOT)
    parents = (_resolve(md_path, ref) for ref in pnode or ())
    return [(parent, child) for parent in parents if parent is not None]


def _reachable(md_files: list[Path]) -> set[Path]:
    """The set of repo-relative docs reachable from the seed entry points.

    Each file is read once. Multi-source DFS from every seed; a doc absent from the result
    is an orphan.
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

    # An agent-instruction file requires a sibling README.md.
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
