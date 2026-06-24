#!/usr/bin/env python3
"""Advisory faces detector (arch-coherence/FACES.md §7).

FACES.md doctrine: one library exposed through N thin faces (`lib -> api ->
{cli, mcp, web/django}`). Orchestration policy (resolve inputs, seed credentials,
default, dispatch) has ONE home: `api`. Faces translate transport input, call
`api`, render output. The face->worker rule is a NAMED CONVENTION with an advisory
detector, NOT a wall: gate genuine defects (the import-linter `layers` contract),
surface choices (this script). See FACES.md §3-§5.

This script is the surface, not the gate. It is ADVISORY: exit 0 by default,
`--strict` to exit 1 on any Finding. It does two deterministic things:

  1. Role identification (FACES.md §5), declare-then-verify, three tiers:
       Tier 1  canonical names: lib.py / api.py / mcp.py / __main__.py.
       Tier 2  [tool.racecar.faces] manifest: declare role -> module per vertical.
       Tier 3  structural inference: api = the articulation point (cut vertex)
               every face routes through to reach the lib; lib = the in-vertical
               sink. A declared `api` that is NOT the cut vertex is a Finding; a
               vertical with no single cut vertex (faces touch the lib directly,
               faces > 1) is NON-CLASSIFIABLE -- and the non-classifiability is the
               drift finding. Single-face verticals collapse api==lib legitimately.
       All tiers deterministic; ambiguity is resolved by the owner adding one
       manifest line, never by a model (LLM-last; DRIFT.md entropy rule).

  2. Restated orchestration: extract each face's api-call sequence as a normalized
     token stream and flag a sequence appearing in two or more faces -- one
     orchestration policy with two homes, the signal it belongs in `api`.

Every output is a Finding ("should this live in api?"), never a Blocker.

Pure stdlib (tomllib + ast). Shape comes from check_packaging.detect_shape; the
source-root resolution and package walk (`_src_roots` / `_top_packages` / `_dotted`)
are local helpers below. The library pyproject is found by shape detection. No-ops
(exit 0) on a project with no verticals.

Usage (invoked by `make arch`):
    python scripts/check_face_orchestration.py [--root <dir>] [--threshold N] [--strict]

--root defaults to CWD. Exit 0 always unless --strict and a Finding was reported.
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from check_packaging import detect_shape

# Minimum length of a repeated api-call sequence to flag as restated orchestration.
# A single shared call is legitimate (each face calls its api entry once); two or
# more api calls in the same order across faces is the restatement signal.
DEFAULT_THRESHOLD = 2

# Canonical per-vertical role file names (FACES.md §2). A vertical is the lib+api
# worker pair; __main__ and mcp are faces (a __main__ may stand alone as a CLI node).
CANON_LIB = "lib"
CANON_API = "api"
CANON_MAIN = "__main__"
CANON_MCP = "mcp"
FACE_NAMES = {CANON_MAIN, CANON_MCP}
# Directories that are never verticals.
NON_VERTICAL_DIRS = {"shared", "tests", "test", "migrations", "__pycache__"}
# Transport signatures that mark a module as a face (Tier 3, FACES.md §5).
TRANSPORT_IMPORTS = ("mcp", "flask", "fastapi", "django", "starlette", "aiohttp")


@dataclass
class Vertical:
    """One feature submodule and the roles racecar identified within it."""

    name: str
    prefix: str  # dotted package prefix, e.g. "athena.prices"
    modules: dict[str, Path]  # short module name -> file path (in-vertical)
    lib: str | None = None  # short module name of the lib role
    api: str | None = None  # short module name of the api role
    faces: list[str] = field(default_factory=list)  # short names of face modules
    tier: str = "structural"  # how roles were identified: name|manifest|structural


@dataclass
class Finding:
    """A single face-orchestration violation: which vertical, which rule, why."""

    vertical: str
    rule: str
    message: str


# --- pyproject + shape discovery (shape via check_packaging.detect_shape;
# --- source roots + package walk are the local helpers below) ----------------


def _library_pyproject(root: Path) -> Path | None:
    shape, _ = detect_shape(root)
    pyproject = shape.library_pyproject
    if pyproject is None or not pyproject.is_file():
        return None
    return pyproject


def _manifest(pyproject: Path) -> list[dict]:
    """Return the `[[tool.racecar.faces.vertical]]` entries (may be empty)."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    faces = data.get("tool", {}).get("racecar", {}).get("faces", {})
    verticals = faces.get("vertical", [])
    return [v for v in verticals if isinstance(v, dict)]


def _src_roots(root: Path, shape_name: str) -> list[Path]:
    """Directories under which top-level importable packages live, per shape."""
    roots: list[Path] = []
    if shape_name == "src":
        roots.append(root / "src")
    elif shape_name in ("pypkg", "pypkg+djapp"):
        roots.append(root / "pypkg" / "src")
        if shape_name == "pypkg+djapp":
            roots.append(root / "djapp")
    elif shape_name == "djapp":
        roots.append(root / "djapp")
    roots.append(root)
    return [r for r in roots if r.is_dir()]


def _top_packages(src_roots: list[Path]) -> list[Path]:
    """Directories that are importable top-level packages (have __init__.py)."""
    pkgs: list[Path] = []
    seen: set[Path] = set()
    for src_root in src_roots:
        for child in sorted(src_root.iterdir()):
            if child in seen:
                continue
            if child.is_dir() and (child / "__init__.py").is_file():
                seen.add(child)
                pkgs.append(child)
    return pkgs


def _dotted(pkg_root: Path, directory: Path) -> str:
    """Dotted module name of `directory` relative to its top package's parent."""
    rel = directory.relative_to(pkg_root.parent)
    return ".".join(rel.parts)


# --- vertical discovery ------------------------------------------------------


def _discover_verticals(src_roots: list[Path]) -> list[Vertical]:
    """A vertical is a package dir co-locating role files (FACES.md §3).

    Identified structurally: any package directory (not shared/tests/...) holding at
    least one canonical role file (lib.py / api.py / mcp.py / __main__.py) or two+
    plain modules alongside a __main__. Filenames are not required to be canonical --
    the manifest (Tier 2) can rename -- but the presence of a role file is the
    discovery signal.
    """
    verticals: list[Vertical] = []
    seen: set[Path] = set()
    for pkg in _top_packages(src_roots):
        for directory in [pkg, *sorted(p for p in pkg.rglob("*") if p.is_dir())]:
            if directory in seen or directory.name in NON_VERTICAL_DIRS:
                continue
            if not (directory / "__init__.py").is_file() and directory != pkg:
                continue
            py = {
                p.stem: p
                for p in sorted(directory.glob("*.py"))
                if p.stem != "__init__"
            }
            has_worker = any(name in py for name in (CANON_LIB, CANON_API, CANON_MCP))
            # A package whose ONLY role file is __main__.py (no co-located worker) is a
            # CLI node, not a faces vertical: a CLI.md Pattern 1 discovery root that
            # composes children, or a single-file tool. There is no lib->api structure to
            # classify, so it is out of the faces detector's scope. A worker named other
            # than lib/api/mcp still counts via the 2+-sibling-modules signal (FACES.md §3).
            non_main_modules = [name for name in py if name != CANON_MAIN]
            if not (has_worker or len(non_main_modules) >= 2):
                continue
            seen.add(directory)
            verticals.append(
                Vertical(
                    name=directory.name, prefix=_dotted(pkg, directory), modules=py
                )
            )
    return verticals


# --- import graph (intra-vertical) -------------------------------------------


def _intra_imports(path: Path, prefix: str, members: set[str]) -> set[str]:
    """Short names of in-vertical modules that `path` imports.

    Resolves absolute imports under `prefix` and relative imports at level 1.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return set()
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefix + "."):
                    tail = alias.name[len(prefix) + 1 :].split(".")[0]
                    if tail in members:
                        edges.add(tail)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:  # from .X import ...  /  from . import X
                if node.module:
                    head = node.module.split(".")[0]
                    if head in members:
                        edges.add(head)
                else:
                    for alias in node.names:
                        if alias.name in members:
                            edges.add(alias.name)
            elif node.module and node.module.startswith(prefix + "."):
                tail = node.module[len(prefix) + 1 :].split(".")[0]
                if tail in members:
                    edges.add(tail)
            elif node.module == prefix:
                for alias in node.names:
                    if alias.name in members:
                        edges.add(alias.name)
    return edges


def _graph(v: Vertical) -> dict[str, set[str]]:
    members = set(v.modules)
    return {
        name: _intra_imports(path, v.prefix, members)
        for name, path in v.modules.items()
    }


def _reachable(
    graph: dict[str, set[str]], src: str, dst: str, blocked: str | None
) -> bool:
    """Can `dst` be reached from `src` following import edges, skipping `blocked`?"""
    if src == dst:
        return True
    stack, seen = [src], {src}
    while stack:
        cur = stack.pop()
        for nxt in graph.get(cur, ()):
            if nxt == blocked or nxt in seen:
                continue
            if nxt == dst:
                return True
            seen.add(nxt)
            stack.append(nxt)
    return False


def _is_face(name: str, path: Path) -> bool:
    """Face by canonical name (__main__/mcp) or transport signature (FACES.md §5)."""
    if name in FACE_NAMES:
        return True
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if 'if __name__ == "__main__"' in src or "if __name__ == '__main__'" in src:
        if "argparse" in src:
            return True
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    return any(t in imported for t in TRANSPORT_IMPORTS)


# --- role identification -----------------------------------------------------


def _sink(graph: dict[str, set[str]], candidates: set[str]) -> str | None:
    """The in-vertical sink among `candidates`: imports no other member."""
    sinks = [n for n in candidates if not graph.get(n, set()) & set(graph)]
    return sinks[0] if len(sinks) == 1 else None


def _identify(v: Vertical, manifest_by_prefix: dict[str, dict]) -> list[Finding]:
    """Fill v.lib / v.api / v.faces and return any role Findings."""
    findings: list[Finding] = []
    graph = _graph(v)

    # Tier 2: manifest (authority when present).
    entry = manifest_by_prefix.get(v.prefix) or manifest_by_prefix.get(v.name)
    if entry:
        v.tier = "manifest"
        v.lib = _short(entry.get("lib"), v.prefix)
        v.api = _short(entry.get("api"), v.prefix)
        v.faces = [
            s for s in (_short(f, v.prefix) for f in entry.get("faces", [])) if s
        ]
    else:
        # Tier 1: canonical names.
        v.faces = sorted(n for n, p in v.modules.items() if _is_face(n, p))
        if CANON_LIB in v.modules:
            v.lib = CANON_LIB
            v.tier = "name"
        if CANON_API in v.modules:
            v.api = CANON_API
            v.tier = "name"

    # Tier 3: structural inference for whatever names/manifest did not pin.
    non_faces = set(v.modules) - set(v.faces)
    if v.lib is None:
        v.lib = _sink(graph, non_faces)
        if v.lib is not None and v.tier == "structural":
            v.tier = "structural"

    if not v.faces:
        return findings  # no face: a plain library vertical, nothing to route.

    if v.lib is None:
        # FD1: a top-level Pattern-1 pure-discovery root, whose sole face is a __main__
        # that composes child verticals by name and reaches no in-vertical sibling,
        # co-residing with a shared layer (auth/config/domains/...), is out of faces
        # scope, not a defective vertical. The siblings are a shared layer the root does
        # not route through, so there is no lib->api->face structure to classify.
        # Suppress the finding for it (FACES.md §7 / FD1; advisory-only, never gating).
        if v.faces == [CANON_MAIN] and not graph.get(CANON_MAIN):
            return findings
        findings.append(
            Finding(
                v.name,
                "non-classifiable",
                "no in-vertical lib sink found; declare [tool.racecar.faces] for "
                f"'{v.name}' or co-locate a lib.py",
            )
        )
        return findings

    # Verify / infer the api as the articulation point between faces and lib.
    if v.api is not None:
        bypassers = [f for f in v.faces if _reachable(graph, f, v.lib, blocked=v.api)]
        if bypassers and v.api != v.lib:
            findings.append(
                Finding(
                    v.name,
                    "api-not-cut-vertex",
                    f"declared api '{v.api}' is not the articulation point: face(s) "
                    f"{bypassers} reach the lib '{v.lib}' without passing through it",
                )
            )
    else:
        findings.extend(_infer_api(v, graph))
    return findings


def _infer_api(v: Vertical, graph: dict[str, set[str]]) -> list[Finding]:
    """Infer api as the cut vertex; emit the non-classifiability finding if none."""
    assert v.lib is not None
    if len(v.faces) == 1:
        # Single face: api==lib collapse is legitimate (FACES.md §1, §5). If a single
        # mediator exists, name it; otherwise the face imports lib directly -- fine.
        cut = _cut_vertices(v, graph)
        v.api = cut[0] if len(cut) == 1 else v.lib
        return []
    cut = _cut_vertices(v, graph)
    if len(cut) == 1:
        v.api = cut[0]
        return []
    if not cut:
        return [
            Finding(
                v.name,
                "non-classifiable",
                f"faces {v.faces} reach the lib '{v.lib}' with no single mediating api "
                "module; introduce an api.py or declare [tool.racecar.faces]",
            )
        ]
    return [
        Finding(
            v.name,
            "ambiguous-api",
            f"multiple candidate api modules {cut} mediate faces->lib; declare the "
            "intended one in [tool.racecar.faces]",
        )
    ]


def _cut_vertices(v: Vertical, graph: dict[str, set[str]]) -> list[str]:
    """Non-face, non-lib modules whose removal disconnects EVERY face from the lib."""
    assert v.lib is not None
    candidates = set(v.modules) - set(v.faces) - {v.lib}
    cut: list[str] = []
    for node in sorted(candidates):
        reaches = [f for f in v.faces if _reachable(graph, f, v.lib, blocked=None)]
        if not reaches:
            continue
        if all(not _reachable(graph, f, v.lib, blocked=node) for f in reaches):
            cut.append(node)
    return cut


def _short(dotted: object, prefix: str) -> str | None:
    """Reduce a manifest dotted module to its in-vertical short name."""
    if not isinstance(dotted, str) or not dotted:
        return None
    if dotted.startswith(prefix + "."):
        return dotted[len(prefix) + 1 :].split(".")[0]
    return dotted.split(".")[-1]


# --- restated-orchestration detection ----------------------------------------


def _api_aliases(tree: ast.AST, api_dotted: str) -> set[str]:
    """Local names that, when called, count as api calls in this face."""
    aliases: set[str] = set()
    short = api_dotted.split(".")[-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == api_dotted:
                    aliases.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module == api_dotted:
                # from <pkg>.<verb>.api import f, g  -> f, g are api calls
                for alias in node.names:
                    aliases.add(alias.asname or alias.name)
            else:
                # from <pkg>.<verb> import api  /  from . import api  (module None)
                for alias in node.names:
                    if alias.name == short:
                        aliases.add(alias.asname or alias.name)
    return aliases


def _api_sequence(tree: ast.AST, aliases: set[str]) -> list[str]:
    seq: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in aliases:
                    seq.append(f"{func.value.id}.{func.attr}")
            elif isinstance(func, ast.Name) and func.id in aliases:
                seq.append(func.id)
    return seq


def _windows(seq: list[str], size: int) -> set[tuple[str, ...]]:
    return {tuple(seq[i : i + size]) for i in range(len(seq) - size + 1)}


def _restated(verticals: list[Vertical], threshold: int) -> list[Finding]:
    """Flag api-call windows that appear across two or more faces of a vertical."""
    findings: list[Finding] = []
    for v in verticals:
        if not v.api or not v.faces:
            continue
        api_dotted = f"{v.prefix}.{v.api}" if "." not in v.api else v.api
        per_face: dict[str, set[tuple[str, ...]]] = {}
        for face in v.faces:
            path = v.modules.get(face)
            if path is None:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            aliases = _api_aliases(tree, api_dotted)
            if aliases:
                per_face[face] = _windows(_api_sequence(tree, aliases), threshold)
        shared: dict[tuple[str, ...], list[str]] = {}
        for face, windows in per_face.items():
            for window in windows:
                shared.setdefault(window, []).append(face)
        for window, faces in sorted(shared.items(), key=lambda x: (-len(x[0]), x[0])):
            if len(faces) >= 2:
                findings.append(
                    Finding(
                        v.name,
                        "restated-orchestration",
                        f"api-call sequence [{' -> '.join(window)}] appears in faces "
                        f"{faces}: one policy with two homes -- move it into api",
                    )
                )
    return findings


# --- entry point -------------------------------------------------------------


def main(argv: list[str]) -> int:
    """Validate each declared vertical's face orchestration; return an exit code."""
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--strict", action="store_true", help="exit 1 on any Finding")
    args = parser.parse_args(argv)

    pyproject = _library_pyproject(args.root)
    if pyproject is None:
        print("check_face_orchestration: pyproject.toml not found; nothing to check")
        return 0
    shape, _ = detect_shape(args.root)
    src_roots = _src_roots(args.root, shape.name)

    verticals = _discover_verticals(src_roots)
    if not verticals:
        print("check_face_orchestration: no faces verticals found; nothing to check")
        return 0

    manifest = _manifest(pyproject)
    manifest_by_prefix: dict[str, dict] = {}
    for entry in manifest:
        key = entry.get("name") or ""
        manifest_by_prefix[str(key)] = entry

    findings: list[Finding] = []
    for v in verticals:
        findings.extend(_identify(v, manifest_by_prefix))
    findings.extend(_restated(verticals, args.threshold))

    multi = [v for v in verticals if v.faces]
    print(
        f"check_face_orchestration: {len(verticals)} vertical(s), "
        f"{len(multi)} with faces"
    )
    if not findings:
        print("check_face_orchestration: OK (advisory)")
        return 0

    print(
        "check_face_orchestration: Findings (advisory; ask 'should this live in api?'):"
    )
    for f in findings:
        print(f"  - [{f.vertical}] {f.rule}: {f.message}")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
