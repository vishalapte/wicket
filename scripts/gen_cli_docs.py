#!/usr/bin/env python3
"""Project a repository's ``python -m <pkg>…`` CLI tree to README pages under ``docs/cli/``.

One README per CLI node, its path mirroring the module path (``<pkg>`` is the repo's
own package — discovered, never hard-coded)::

    python -m <pkg>                    -> docs/cli/README.md
    python -m <pkg>.checksum           -> docs/cli/checksum/README.md
    python -m <pkg>.checksum.validate  -> docs/cli/checksum/validate/README.md
    python -m <pkg>.excel.macros       -> docs/cli/excel/macros/README.md   (nests arbitrarily deep)

**A projection, never a second home.** The CLI surface has ONE source of truth: the
``__main__.py`` + ``commands()`` + argparse tree, discovered and enriched by
``check_cli_commands.py`` (the §3 audit,
[architecture/CLI.md](../CLI.md)). This module
REUSES that walk verbatim (``audit_cli_tree``) — it invents no structure of its own — and
writes the tree out as prose. The pages are GENERATED, never hand-edited; a stale page is a
build failure, not a silent lie (run ``--check`` in CI / a test).

What each page carries is DERIVED:

- **Discovery nodes** (``pure-discovery`` / ``discovery+cli``): the invocation, the node's own
  module docstring, and a table of its subcommands, each a relative link to its own README (so the
  tree browses on GitHub).
- **Leaf nodes** (``leaf``): the invocation, the command's description, and its argparse
  **usage + options** — captured from ``python -m <pkg> --help`` under a pinned ``COLUMNS`` so the
  bytes are reproducible whoever regenerates them. A leaf that also declares the optional
  ``example_output()`` contract ([architecture/CLI.md](../CLI.md)) gets a further
  ``## Example output`` section, one labeled subsection per scenario.

**Why capture ``--help`` rather than introspect the parser.** The §3 audit only surfaces a
structured ``args`` view when a leaf exposes a ``parser()`` factory; a leaf that builds its parser
inline has nothing to introspect. The help text is the authoritative, always-present rendering of
the same surface, and pinning ``COLUMNS`` makes it deterministic.

**Why ``example_output()`` is read directly, not through the §3 audit.** The audit's four
contracts (``commands()``, ``subcommands()``, ``parser()``, ``pipeline()``) all shape dispatch or
discovery; ``check_cli_commands.py`` enforces them because getting one wrong breaks running the
CLI. Sample output changes nothing about how the command runs — it exists purely so this
generator has something to render — so it is read the same way the leaf's own module docstring
already is (``node_description``): a direct, best-effort ``importlib.import_module`` plus
``getattr``, never gated, never a violation when absent or malformed.

**Doc-graph citizen.** Every page opens with a ``pnode:`` frontmatter edge
([doc-coherence/DOC_GRAPH.md](../../doc-coherence/DOC_GRAPH.md)): the root joins the nearest
existing parent doc (``docs/ARCHITECTURE.md``, else ``docs/README.md``, else the repo storefront);
a child names its own parent page. So ``check_doc_graph`` sees an acyclic, fully-parented subtree
and every relative link resolves for ``check_docs``.

**Content-blind by construction.** Every byte here is derived from tracked source (docstrings,
argparse help). The pages are exactly as content-blind as the code they mirror
([docs-orchestrator/CONTENT_BLINDNESS.md](../../docs-orchestrator/CONTENT_BLINDNESS.md)).

**Why the racecar check-script family and not a library leaf.** This tool imports
``check_cli_commands`` — dev-only tooling delivered under ``scripts/``, not shipped in the wheel. A
library leaf reaching into ``scripts/`` would make the installed package depend on un-packaged
tooling. So this lives with the checkers it belongs to, is synced into adopters alongside them
(``sync_scripts.CHECK_SCRIPTS``), and offers the same ``--write`` / ``--check`` verbs.

Usage::

    python scripts/gen_cli_docs.py --write    # emit / refresh the docs/cli/** tree
    python scripts/gen_cli_docs.py --check     # exit non-zero if any page is stale or orphaned
    python scripts/gen_cli_docs.py             # print the projected page list to stdout (dry run)
    python scripts/gen_cli_docs.py src/<pkg>   # point at a package (default: auto-discover)

Complexity: O(N), N = CLI nodes in the audited tree -- one DFS pass (project) visits
each node once, summing to O(N) plus one `--help` subprocess spawn per leaf;
stale/orphans/write add one more O(N) pass over the projected pages against disk.
"""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path

from check_packaging_rules._root import find_repo_root

# The no-repo fallback: the shared walk-up returns its starting point (cwd) when there is
# no `.git` above, which for a doc GENERATOR is the wrong place to write. `parents[1]` is
# the repo root from a flat `scripts/`, which is now the shape in racecar and in every
# adopter alike -- this used to need a racecar-specific branch because it was not.
REPO_ROOT = find_repo_root()
if not (REPO_ROOT / ".git").exists():
    REPO_ROOT = Path(__file__).resolve().parents[1]

# A src/ layout (src/<pkg>) is put on the path so `<pkg>` (and the audit's probes) resolve
# without an editable install; a flat layout (package at the repo root) uses the root itself.
SRC = REPO_ROOT / "src" if (REPO_ROOT / "src").is_dir() else REPO_ROOT
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# `check_cli_commands` is a sibling in this same directory, reached by putting that directory on
# the path rather than by naming it two ways. The earlier shim tried `scripts.check_cli_commands`
# first and fell back to the bare name, which is FATAL to mypy in the flat layout every adopter
# gets: with `scripts/check_cli_commands.py` present, both names resolve to one file and mypy
# aborts the whole run with "Source file found twice under different module names" before it
# checks anything. One path in, one name out -- and the bare name resolves under every
# invocation, so the fallback branch was never the one that ran.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# The path insert above must precede this, which is the same shape hooks/session_check_sync.py
# carries; the old form escaped the warning only by hiding the import inside a try block.
from check_cli_commands import (  # noqa: E402 # pylint: disable=wrong-import-position
    Node,
    _default_root,
    _resolve_root,
    audit_cli_tree,
    render_tree,
)

DOCS = REPO_ROOT / "docs" / "cli"
README = REPO_ROOT / "README.md"

# The repo's root package name. Empty until `build()` reads it off the audited tree — the audit is
# the one authority on what the package is called, so nothing here hard-codes it.
ROOT_PKG = ""

# Stable markers delimiting the generated CLI-tree section in the repo-root README. `--write`
# regenerates ONLY the text between them; everything else in README.md is left untouched.
CLI_BEGIN = "<!-- BEGIN cli-tree (generated) -->"
CLI_END = "<!-- END cli-tree -->"

REGENERATE = "python scripts/gen_cli_docs.py --write"
"""The one command that refreshes this tree. Named in every staleness error."""

# argparse renders help at the terminal width; pin it so `--write` and `--check` produce
# byte-identical text no matter who (or what CI runner) regenerates.
HELP_COLUMNS = "80"

_ROLE_BLURB = {
    "pure-discovery": "pure discovery — composes its child sub-CLIs; it has no verbs of its own.",
    "discovery+cli": "discovery + own CLI — composes its children AND parses its own arguments.",
    "leaf": "leaf — an argparse entry point.",
}


def _banner() -> str:
    """The GENERATED — DO NOT EDIT banner every page opens with, named to this repo's package."""
    src_rel = SRC.relative_to(REPO_ROOT) if SRC != REPO_ROOT else Path(".")
    return (
        "<!-- GENERATED — DO NOT EDIT. This page is a projection of the "
        f"`python -m {ROOT_PKG}…` CLI tree\n"
        f"     ({src_rel}/{ROOT_PKG}/**/__main__.py + argparse). The code is the source; "
        "this is derived.\n"
        f"     Regenerate:  {REGENERATE} -->"
    )


# ---------------------------------------------------------------------------------------------
# Node -> filesystem, node -> description, node -> pnode.
# ---------------------------------------------------------------------------------------------
def node_readme(pkg: str) -> Path:
    """The README path mirroring a node's module path (root -> ``docs/cli/README.md``)."""
    parts = pkg.split(".")[1:]  # drop the leading root package
    return DOCS.joinpath(*parts, "README.md")


def _root_pnode() -> str:
    """The doc-graph parent edge for the root ``docs/cli/README.md``.

    Racecar mandates the repo-root README but neither ``docs/ARCHITECTURE.md`` nor
    ``docs/README.md`` (docs-orchestrator/ORCHESTRATION.md), and ``check_doc_graph`` requires a
    ``pnode`` target that resolves to an existing file. So point at the nearest existing parent:
    the architecture overview if the repo keeps one, else a docs index, else the storefront (always
    present). Paths are relative to ``docs/cli/``.
    """
    for candidate in ("../ARCHITECTURE.md", "../README.md", "../../README.md"):
        if (DOCS / candidate).resolve().is_file():
            return f"pnode: [{candidate}]"
    return "pnode: [../../README.md]"


def _pnode(pkg: str) -> str:
    """The doc-graph parent edge: the root joins an existing parent, a child its own parent page."""
    return _root_pnode() if pkg == ROOT_PKG else "pnode: [../README.md]"


def _frontmatter(node: Node) -> list[str]:
    """The page's machine-readable identity block.

    ``command`` (first) is the literal invocation, so the page IS keyed by the CLI node it documents
    — a grep/tool can map a page to its command without parsing the body. ``pattern`` is the audit's
    role label (``leaf`` / ``pure-discovery`` / ``discovery+cli``). ``pnode`` is the doc-graph
    parent edge. All three are derived from the same audit that shapes the tree.
    """
    pkg = node["pkg"]
    return [
        "---",
        f"command: python -m {pkg}",
        f"pattern: {node.get('role', 'unknown')}",
        _pnode(pkg),
        "---",
    ]


def _docstring_module(node: Node) -> str:
    """The module whose docstring describes this node.

    A discovery node's CLI lives in ``<pkg>.__main__``; a leaf IS its own module.
    """
    pkg = node["pkg"]
    return pkg if node["kind"] == "module" else f"{pkg}.__main__"


def node_description(node: Node) -> str:
    """The node's own description: the lead paragraph of its module docstring.

    Falls back to the parent's ``commands()`` blurb when the module will not import (a
    concurrently-edited leaf still yields a usable page rather than crashing the run).
    """
    try:
        module = importlib.import_module(_docstring_module(node))
        doc = (module.__doc__ or "").strip()
    except Exception:  # pylint: disable=broad-exception-caught
        doc = ""
    text = _first_paragraph(doc) if doc else ""
    return text or (node.get("description") or "").strip()


def _first_paragraph(text: str) -> str:
    """The lead paragraph of a docstring — everything up to the first blank line."""
    lines: list[str] = []
    for line in text.splitlines():
        if not line.strip() and lines:
            break
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------------------------
# A leaf's optional, hand-authored sample output (arch-python/CLI.md, "example_output()").
# ---------------------------------------------------------------------------------------------
def node_example_output(node: Node) -> list[tuple[str, str, str, int]]:
    """The leaf's fabricated ``example_output()`` entries, validated and normalized.

    Mirrors ``node_description``'s own read: import the same module, look for the optional
    symbol, and degrade to "nothing here" rather than raise on any of absence, an import
    failure, a non-callable, a raise, or a malformed return. The contract is documentation
    data an author writes once — never executed, never gating — so a leaf that gets the
    shape wrong loses its example section rather than breaking the build; the reader will
    notice a missing section long before a checker would.

    Each surviving entry is ``(scenario, invocation, transcript, exit_code)`` — a plain
    4-tuple, the same shape ``commands()`` and ``subcommands()`` already use for their
    2-tuples, so a leaf author reaches for no shared type across the ``src``/``scripts``
    boundary (``src`` may not import from ``scripts`` — see the module docstring).
    """
    try:
        module = importlib.import_module(_docstring_module(node))
    except Exception:  # pylint: disable=broad-exception-caught
        return []
    fn = getattr(module, "example_output", None)
    if not callable(fn):
        return []
    try:
        raw = fn()
    except Exception:  # pylint: disable=broad-exception-caught
        return []
    if not isinstance(raw, list):
        return []
    examples: list[tuple[str, str, str, int]] = []
    for entry in raw:
        if not (isinstance(entry, tuple) and len(entry) == 4):
            continue
        scenario, invocation, transcript, exit_code = entry
        if (
            isinstance(scenario, str)
            and isinstance(invocation, str)
            and isinstance(transcript, str)
            and isinstance(exit_code, int)
        ):
            examples.append((scenario, invocation, transcript, exit_code))
    return examples


# ---------------------------------------------------------------------------------------------
# Capturing a leaf's argparse surface, deterministically.
# ---------------------------------------------------------------------------------------------
def capture_help(pkg: str) -> str:
    """``python -m <pkg> --help`` under a pinned ``COLUMNS`` — the leaf's usage + options.

    Deterministic by construction: the width is fixed, so the wrapped text is the same bytes every
    run. Returns a short placeholder rather than raising if the probe fails (a concurrently-broken
    module still produces a page).
    """
    env = os.environ.copy()
    env["COLUMNS"] = HELP_COLUMNS
    env["LINES"] = "50"
    prior = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), *([prior] if prior else [])])
    try:
        result = subprocess.run(
            [sys.executable, "-m", pkg, "--help"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            cwd=str(REPO_ROOT),
        )
    except OSError as exc:  # pragma: no cover - defensive
        return f"(could not capture --help: {exc})"
    out = (result.stdout or result.stderr or "").rstrip()
    return out or "(no --help output)"


# ---------------------------------------------------------------------------------------------
# Rendering one node's page.
# ---------------------------------------------------------------------------------------------
def _subcommand_table(node: Node) -> list[str]:
    """A table of a discovery node's children, each a relative link to its own README."""
    lines = ["## Subcommands", "", "| Command | What it does |", "| --- | --- |"]
    for child in node["children"]:
        child_pkg = child["pkg"]
        leaf = child_pkg.rsplit(".", 1)[-1]
        desc = (child.get("description") or "").strip().replace("|", "\\|")
        lines.append(f"| [`python -m {child_pkg}`]({leaf}/README.md) | {desc} |")
    lines.append("")
    return lines


def _example_output_section(node: Node) -> list[str]:
    """A leaf's ``## Example output``: labeled, fabricated scenarios, or nothing at all.

    Kept as its own section below ``## Usage`` rather than folded into it, and opened with
    an explicit disclaimer line, so the fabricated transcripts can never be mistaken for the
    live ``--help`` capture immediately above — the two are adjacent on the page but never
    blended into one block.
    """
    examples = node_example_output(node)
    if not examples:
        return []
    lines = [
        "## Example output",
        "",
        "_Illustrative only. Fabricated by the command's own author to show the range of "
        "outcomes; never executed by this generator, and not a captured run._",
        "",
    ]
    for scenario, invocation, transcript, exit_code in examples:
        lines.append(f"### {scenario}")
        lines.append("")
        lines.append(f"Invocation: `{invocation}`")
        lines.append("")
        lines.append("```text")
        lines.append(transcript.rstrip("\n"))
        lines.append("```")
        lines.append("")
        lines.append(f"Exit code: `{exit_code}`")
        lines.append("")
    return lines


def render_page(node: Node) -> str:
    """The full Markdown for one node — frontmatter first, then discovery table or leaf usage."""
    pkg = node["pkg"]
    role = node.get("role", "unknown")

    parts: list[str] = [*_frontmatter(node), "", _banner(), ""]
    if pkg != ROOT_PKG:
        parts.append("[← parent](../README.md)")
        parts.append("")
    parts.append(f"# `python -m {pkg}`")
    parts.append("")
    parts.append(f"> {_ROLE_BLURB.get(role, role)}")
    parts.append("")

    description = node_description(node)
    if description:
        parts.append(description)
        parts.append("")

    if node["children"]:
        parts.extend(_subcommand_table(node))
    else:
        parts.append("## Usage")
        parts.append("")
        parts.append("```text")
        parts.append(capture_help(pkg))
        parts.append("```")
        parts.append("")
        parts.extend(_example_output_section(node))

    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------------------------
# Walking the audited tree into {path: content}, honouring skips.
# ---------------------------------------------------------------------------------------------
def _is_broken(node: Node) -> bool:
    """A node the audit could not import cleanly — skip it, do not emit a page."""
    if node["kind"] == "missing":
        return True
    return any(
        "raised on import" in message or "not importable" in message
        for message in node["violations"]
    )


def project(tree: Node) -> tuple[dict[Path, str], list[str]]:
    """The whole tree projected: ``{README path: content}`` plus the skipped node notes.

    A broken (concurrently-edited, un-importable) node — or a §3 orphan the parent does not register
    — is skipped and reported, but its subtree IS still descended (matching
    ``check_cli_commands._descend_broken``'s own recovery): a node breaking must not delete the
    generated pages of descendants that were never touched and remain importable. The coordinator
    re-runs once the tree stabilises.
    """
    pages: dict[Path, str] = {}
    skipped: list[str] = []

    def walk(node: Node) -> None:
        # A broken or orphaned node's OWN page is skipped, but its children are still
        # descended -- `check_cli_commands._descend_broken` already recovered them onto
        # `node["children"]` during the audit, and a parent's breakage must not delete
        # the generated pages of descendants that were never touched and remain
        # perfectly importable.
        if node["orphan"]:
            skipped.append(f"{node['pkg']} (orphan — not registered by its parent)")
        elif _is_broken(node):
            skipped.append(f"{node['pkg']} (not importable — concurrently edited?)")
        else:
            pages[node_readme(node["pkg"])] = render_page(node)
        for child in node["children"]:
            walk(child)

    walk(tree)
    return pages, skipped


def _existing_pages() -> set[Path]:
    """Every ``docs/cli/**/README.md`` currently on disk."""
    if not DOCS.exists():
        return set()
    return set(DOCS.rglob("README.md"))


def build(root: str | None = None) -> tuple[dict[Path, str], list[str], Node]:
    """Audit the live tree ONCE and project it: ``(pages, skipped, tree)``.

    The one place the audit is invoked. ``root`` defaults to the same discovery
    ``check_cli_commands`` uses (a ``src/`` package, else the cwd); the root package's own name is
    read off the audited tree — never hard-coded — and cached in ``ROOT_PKG`` for the renderers. The
    raw ``tree`` is returned too so the README CLI-tree section renders from the SAME audit that
    shaped the pages — no second, possibly-divergent walk.
    """
    global ROOT_PKG  # pylint: disable=global-statement
    # `_resolve_root` turns a `src/<pkg>` path or a bare `src`/`.` into the package name and adds
    # its enclosing dir to sys.path, so both the audit and `node_description`'s importlib resolve.
    tree = audit_cli_tree(_resolve_root(root or _default_root()))
    ROOT_PKG = tree["pkg"]
    pages, skipped = project(tree)
    return pages, skipped, tree


# ---------------------------------------------------------------------------------------------
# The repo-root README's generated CLI-tree section (the whole surface, on one screen).
# ---------------------------------------------------------------------------------------------
def render_cli_section(tree: Node) -> str:
    """The marker-delimited ``## CLI`` block: the audited tree, rendered EXACTLY as the §3 checker
    prints it (``render_tree`` — the indented ``python -m … [Pattern N (role)] OK`` lines).

    Reusing ``render_tree`` means the README shows byte-for-byte what ``make arch`` audits; there is
    no second renderer to drift.
    """
    lines = [
        CLI_BEGIN,
        "## CLI",
        "",
        f"<!-- GENERATED — DO NOT EDIT between the markers. Regenerate:  {REGENERATE} -->",
        "",
        "```text",
        *render_tree(tree),
        "```",
        CLI_END,
    ]
    return "\n".join(lines)


def readme_with_section(existing: str, tree: Node) -> str:
    """``existing`` README with its CLI-tree section replaced in place (or appended if absent).

    Only the bytes between ``CLI_BEGIN`` and ``CLI_END`` move; the rest of the README is untouched.
    Idempotent: a second pass over the result reproduces it exactly.
    """
    section = render_cli_section(tree)
    if CLI_BEGIN in existing and CLI_END in existing:
        start = existing.index(CLI_BEGIN)
        end = existing.index(CLI_END) + len(CLI_END)
        return existing[:start] + section + existing[end:]
    return existing.rstrip() + "\n\n" + section + "\n"


def readme_stale(tree: Node) -> bool:
    """True when the README's CLI-tree section is missing or out of date with the audited tree."""
    existing = README.read_text(encoding="utf-8") if README.is_file() else ""
    return readme_with_section(existing, tree) != existing


def write_readme(tree: Node) -> bool:
    """Refresh the README's CLI-tree section if stale. Returns whether it moved."""
    existing = README.read_text(encoding="utf-8") if README.is_file() else ""
    updated = readme_with_section(existing, tree)
    if updated != existing:
        README.write_text(updated, encoding="utf-8")
        return True
    return False


# ---------------------------------------------------------------------------------------------
# The verbs.
# ---------------------------------------------------------------------------------------------
def stale(pages: dict[Path, str]) -> list[Path]:
    """Projected pages whose bytes on disk differ (missing counts as stale)."""
    return [
        path
        for path, content in sorted(pages.items())
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    ]


def orphans(pages: dict[Path, str]) -> list[Path]:
    """READMEs under ``docs/cli/`` the tree no longer projects — docs that would rot."""
    return sorted(_existing_pages() - set(pages))


def _prune_empty_dirs() -> None:
    """Remove now-empty directories under ``docs/cli`` left by a removed orphan."""
    if not DOCS.exists():
        return
    for directory in sorted(DOCS.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def write(pages: dict[Path, str]) -> tuple[list[Path], list[Path]]:
    """Write every stale page and delete every orphan. Returns ``(written, removed)``."""
    written: list[Path] = []
    for path in stale(pages):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pages[path], encoding="utf-8")
        written.append(path)
    removed: list[Path] = []
    for path in orphans(pages):
        path.unlink()
        removed.append(path)
    _prune_empty_dirs()
    return written, removed


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/gen_cli_docs.py",
        description="Project the repo's `python -m <pkg>…` CLI tree to docs/cli/**/README.md.",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="package or src path to document (default: a src/ package, else the cwd)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="emit / refresh the docs/cli/** tree (writes stale pages, removes orphans)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when any page is stale or orphaned (writes nothing)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Emit, refresh, or check the docs/cli tree AND the README's CLI-tree section."""
    args = _parser().parse_args(argv)
    pages, skipped, tree = build(args.root)

    if args.check:
        bad = stale(pages)
        gone = orphans(pages)
        readme_bad = readme_stale(tree)
        if bad or gone or readme_bad:
            print(
                "STALE — the CLI tree has moved and its docs have not:", file=sys.stderr
            )
            for path in bad:
                print(f"  stale:  {_rel(path)}", file=sys.stderr)
            for path in gone:
                print(f"  orphan: {_rel(path)}", file=sys.stderr)
            if readme_bad:
                print("  stale:  README.md (## CLI section)", file=sys.stderr)
            print(f"\nRegenerate:  {REGENERATE}", file=sys.stderr)
            return 1
        print(f"docs/cli is current ({len(pages)} pages) + README.md CLI section")
        for note in skipped:
            print(f"  skipped: {note}")
        return 0

    if args.write:
        written, removed = write(pages)
        for path in written:
            print(f"wrote   {_rel(path)}")
        for path in removed:
            print(f"removed {_rel(path)}")
        if write_readme(tree):
            print("wrote   README.md (## CLI section)")
        if not written and not removed:
            print("docs/cli already current")
        for note in skipped:
            print(f"skipped: {note}")
        return 0

    # Dry run: print the projected page list, the README target, and any skips.
    for path in sorted(pages):
        print(_rel(path))
    print("README.md (## CLI section)")
    for note in skipped:
        print(f"skipped: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
