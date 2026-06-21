#!/usr/bin/env python3
"""Mechanical check for markdown documentation placement.

Enforces the placement rule defined in `doc-coherence/README.md`, "Documentation
placement":

  - Repo top level: the only markdown docs are README.md, CLAUDE.md, PLAN.md,
    TODO.md, CHANGELOG.md. Every other narrative doc lives under docs/.
  - Inside a subdirectory: the only markdown outside `<subdir>/docs/` is
    README.md and CLAUDE.md. Everything else goes in that subdir's docs/.
  - CLAUDE.md may not exist in a directory that has no README.md (README is the
    precondition for CLAUDE).

The rule governs markdown documentation only; build/config files
(pyproject.toml, Makefile, .pre-commit-config.yaml, requirements.txt, ...) are
not documentation and are out of scope. Files anywhere under a `docs/`
directory are always fine. Hidden directories are skipped.

Nothing here is repo-specific: the script discovers the repo root at runtime.

Exit 0 if clean, 1 if any misplaced doc is found.

Usage:
    python3 <path-to>/check_file_placement.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from check_docs import ignore_patterns

ROOT_ALLOWED = {"README.md", "CLAUDE.md", "PLAN.md", "TODO.md", "CHANGELOG.md"}
SUBDIR_ALLOWED = {"README.md", "CLAUDE.md"}


def _find_repo_root() -> Path:
    start = Path.cwd()
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return start


REPO_ROOT = _find_repo_root()

# Same `[tool.pylint.MASTER].ignore-paths` patterns check_docs honors, so a repo
# can scope out a data/ payload tree of markdown with one ignore-paths
# declaration that also covers pylint and check_docs.
IGNORE_PATTERNS = ignore_patterns(REPO_ROOT)


class Finding:
    def __init__(self, rel: str, message: str) -> None:
        self.rel = rel
        self.message = message

    def render(self) -> str:
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


def check_placement() -> list[Finding]:
    findings: list[Finding] = []
    dirs_with_claude: set[Path] = set()

    for path in _markdown_files():
        rel = path.relative_to(REPO_ROOT)
        parts = rel.parts
        name = path.name

        # Anything under a docs/ directory is fine.
        if "docs" in parts[:-1]:
            continue

        if len(parts) == 1:
            # Repo top level.
            if name not in ROOT_ALLOWED:
                findings.append(
                    Finding(
                        rel.as_posix(),
                        f"only {sorted(ROOT_ALLOWED)} allowed at the repo top level; "
                        f"move to docs/{name}",
                    )
                )
        else:
            # Inside a subdirectory, not under docs/.
            if name not in SUBDIR_ALLOWED:
                subdir = "/".join(parts[:-1])
                findings.append(
                    Finding(
                        rel.as_posix(),
                        f"only README.md / CLAUDE.md allowed outside docs/; "
                        f"move to {subdir}/docs/{name}",
                    )
                )

        if name == "CLAUDE.md":
            dirs_with_claude.add(path.parent)

    # CLAUDE.md requires a sibling README.md.
    for d in sorted(dirs_with_claude):
        if not (d / "README.md").is_file():
            rel = (d / "CLAUDE.md").relative_to(REPO_ROOT).as_posix()
            findings.append(Finding(rel, "CLAUDE.md present without a sibling README.md (README is required first)"))

    return findings


def main() -> int:
    findings = check_placement()
    if not findings:
        print("file-placement: OK")
        return 0
    print(f"file-placement: {len(findings)} misplaced doc(s)")
    for f in findings:
        print(f.render())
    return 1


if __name__ == "__main__":
    sys.exit(main())
