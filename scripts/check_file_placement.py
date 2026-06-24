#!/usr/bin/env python3
"""Mechanical check: every markdown doc is reachable from the resolver chain.

Reference-driven placement (`doc-coherence/README.md`, "Documentation placement"):
a doc is correctly placed when a resolver points to it, not when its filename
matches a fixed allowlist. The README of a directory is that directory's manifest;
the root `README.md` and `CLAUDE.md` are the entry resolvers. This check builds the
link graph over the repo's markdown, seeds it with those two roots, and flags any
doc nothing in the chain references — an orphan no reader can navigate to. That is
the one mechanical thing that keeps the resolver honest: a doc you forget to link
fails the gate instead of rotting unreferenced.

No fixed taxonomy: there is no hardcoded list of allowed filenames or required
sections. A repo declares what its docs are by linking them from its READMEs.

Scope: markdown only; build/config files are out of scope. Anything under a `docs/`
directory is exempt (the overflow area, not part of the navigable resolver surface).
Hidden directories and `[tool.pylint.MASTER].ignore-paths` are skipped, same as
check_docs. Nothing here is repo-specific; the repo root is discovered at runtime.

Exit 0 if clean, 1 if any orphan (or a CLAUDE.md without a sibling README.md) is found.

Usage:
    python3 <path-to>/check_file_placement.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from check_docs import ignore_patterns

# Entry points: reachability is seeded from these. README.md is the human resolver
# and CLAUDE.md the agent baseline/resolver (force-loaded), both at the repo root.
# SKILL.md is an entry point at ANY level — the harness invokes it as a slash
# command, so it is reached by invocation, not by a README link, and it pulls in its
# own content (its "Load <doc>" line). A doc is legitimately placed when the chain
# from one of these reaches it.
ROOT_SEED_NAMES = ("README.md", "CLAUDE.md")
SEED_ANYWHERE = "SKILL.md"

# Inline markdown link target: the `(path)` of `[text](path)`.
_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _find_repo_root() -> Path:
    start = Path.cwd()
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return start


REPO_ROOT = _find_repo_root()

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
    for p in sorted(REPO_ROOT.rglob("*.md")):
        rel = p.relative_to(REPO_ROOT)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if any(pat.search(rel.as_posix()) for pat in IGNORE_PATTERNS):
            continue
        out.append(p)
    return out


def _md_link_targets(md_path: Path) -> set[Path]:
    """Repo-relative paths of the .md files this doc links to (anchors stripped)."""
    targets: set[Path] = set()
    text = md_path.read_text(encoding="utf-8")
    for match in _LINK_RE.finditer(text):
        raw = match.group(1).strip().split()[0]  # drop any "(path \"title\")"
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


def _reachable(md_files: list[Path]) -> set[Path]:
    """The set of repo-relative docs reachable from the seed entry points."""
    graph = {p.relative_to(REPO_ROOT): _md_link_targets(p) for p in md_files}
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


def _under_docs(rel: Path) -> bool:
    return "docs" in rel.parts[:-1]


def check_placement() -> list[Finding]:
    """Find misplaced or unreachable markdown and orphan CLAUDE.md files."""
    md_files = _markdown_files()
    reachable = _reachable(md_files)
    findings: list[Finding] = []
    dirs_with_claude: set[Path] = set()

    for path in md_files:
        rel = path.relative_to(REPO_ROOT)
        if path.name == "CLAUDE.md":
            dirs_with_claude.add(path.parent)
        if _under_docs(rel) or rel in reachable:
            continue
        findings.append(
            Finding(
                rel.as_posix(),
                "orphan: no resolver links to it — reference it from a README "
                "(or the root CLAUDE.md), or move it under docs/",
            )
        )

    # A CLAUDE.md still requires a sibling README.md: agent context does not stand
    # without a human landing (the README/CLAUDE pair, not a filename allowlist).
    for d in sorted(dirs_with_claude):
        if not (d / "README.md").is_file():
            rel = (d / "CLAUDE.md").relative_to(REPO_ROOT).as_posix()
            findings.append(
                Finding(
                    rel,
                    "CLAUDE.md present without a sibling README.md "
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
