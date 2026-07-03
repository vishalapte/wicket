#!/usr/bin/env python3
"""Advisory surfaces detector (arch-coherence/SURFACES.md §7).

SURFACES.md doctrine: one library exposed through N thin surfaces (`lib -> api ->
{cli, mcp, web/django}`). Orchestration policy (resolve inputs, seed credentials,
default, dispatch) has ONE home: `api`. Surfaces translate transport input, call
`api`, render output. The surface->worker rule is a NAMED CONVENTION with an advisory
detector, NOT a wall: gate genuine defects (the import-linter `layers` contract),
surface choices (this script). See SURFACES.md §3-§5.

This script is the surface, not the gate. It is ADVISORY: exit 0 by default,
`--strict` to exit 1 on any Finding. It is **surface-rooted** and identifies roles by
**name or mapping only** -- there is no structural guessing.

  1. Surfaces are the only analysis anchors (SURFACES.md §7):
       - `cli`     a package's `__main__.py`.
       - `mcp`     a module `mcp.py` OR an `mcp/` package.
       - `django`  the presence of `server/manage.py` (the whole server is one surface).
     A package with NO surface is a **library** and is not analyzed at all -- silent.
     "No surface, nothing to check" is the load-bearing tolerance gate.

     Discovery is further tolerant (the `__main__`-depth test, `_main_imports_deeper`):
     a package whose only surface is a `__main__` that never imports deeper than its
     own directory -- a dispatcher composing same-dir siblings and sibling/parent
     packages, the `data/` + `sources/` ingestion shape -- names no role and is not a
     classifiable vertical, so it is skipped (silent). A `sources/<protocol>` adapter
     has no `__main__` at all and is likewise silent. A new shape under `src/<pkg>/`
     is not a defect.

  2. Role identification -- NAME OR MAPPING ONLY (SURFACES.md §5):
       - `api` = a module `api.py` OR a package `api/`; OR the module named in
         `[tool.racecar.roles]`.
       - `lib` = a module `lib.py` OR a package `lib/`; OR mapped.
     No reachability, no cut-vertex, no sink inference. The name is the declaration
     (the Django autodiscovery model); the manifest renames it. Ambiguity is resolved
     by the owner adding one manifest line, never by a model (LLM-last; DRIFT.md).

  3. Findings (advisory):
       - `api-without-lib`: a unit with a surface whose `api` is named/mapped but has
         no `lib` (named/mapped). The api fronts nothing -- add a lib or declare it.
       - `restated-orchestration`: an api-call window appearing across two or more
         surfaces of the same unit -- one policy with two homes, move it into `api`.
     A unit with a surface but NO `api` is SILENT: the api is the anchor; with none
     named/mapped there is nothing to verify, and the detector does not nag.

Pure stdlib (tomllib + ast). Shape comes from check_packaging.detect_shape; the
source-root resolution and package walk (`_src_roots` / `_top_packages` / `_dotted`)
are local helpers below. The library pyproject is found by shape detection.

Usage (invoked by `make arch`):
    python scripts/check_surface_orchestration.py [--root <dir>] [--threshold N] [--strict]

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
# A single shared call is legitimate (each surface calls its api entry once); two or
# more api calls in the same order across surfaces is the restatement signal.
DEFAULT_THRESHOLD = 2

# Canonical per-vertical role names (SURFACES.md §2). `lib`/`api` are the worker pair;
# `__main__` (cli) and `mcp` are surfaces. Each may be a module (`x.py`) or a package
# (`x/` with __init__.py) -- both forms are recognized by name.
CANON_LIB = "lib"
CANON_API = "api"
CANON_MAIN = "__main__"
CANON_MCP = "mcp"
CANON_DJANGO = "django"
# Directories that are never verticals.
NON_VERTICAL_DIRS = {"shared", "tests", "test", "migrations", "__pycache__"}


@dataclass
class Vertical:
    """One unit (a package with a surface) and the roles racecar identified within it."""

    name: str
    prefix: str  # dotted package prefix, e.g. "athena.prices"
    modules: dict[str, Path]  # short module name -> file path (in-vertical)
    lib: str | None = None  # short name of the lib role (module or package)
    api: str | None = None  # short name of the api role (module or package)
    surfaces: list[str] = field(default_factory=list)  # short names of surface modules
    tier: str = "name"  # how roles were identified: name|manifest


@dataclass
class Finding:
    """A single surface-orchestration finding: which unit, which rule, why."""

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
    """Return the `[[tool.racecar.roles.vertical]]` entries (may be empty)."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    roles = data.get("tool", {}).get("racecar", {}).get("roles", {})
    verticals = roles.get("vertical", [])
    return [v for v in verticals if isinstance(v, dict)]


def _src_roots(root: Path, shape_name: str) -> list[Path]:
    """Directories under which top-level importable packages live, per shape.

    `server/` is NOT walked for units: the whole server is one django surface (§7),
    discovered separately by `_django_vertical`, not a bag of per-app verticals.
    """
    roots: list[Path] = []
    if shape_name in ("src", "src+server"):
        roots.append(root / "src")
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


# --- unit discovery ----------------------------------------------------------


def _subpackages(directory: Path) -> set[str]:
    """Names of immediate subpackages (dirs holding __init__.py)."""
    return {
        p.name
        for p in directory.iterdir()
        if p.is_dir() and (p / "__init__.py").is_file()
    }


def _has_role(name: str, files: dict[str, Path], subpkgs: set[str]) -> bool:
    """A canonical role is present as a module (`name.py`) OR a package (`name/`)."""
    return name in files or name in subpkgs


def _discover_verticals(src_roots: list[Path]) -> list[Vertical]:
    """A unit is a package that OWNS a surface (SURFACES.md §7).

    Surfaces are the only anchors, detected by name: `cli` = `__main__.py`; `mcp` =
    `mcp.py` or an `mcp/` package. A package with no surface is a library and is not
    discovered -- silent. Roles are recognized by canonical name (module or package
    form); the manifest (Tier 2, `_identify`) can rename them. Nothing is inferred.

    Tolerant (the `__main__`-depth test): a package whose only surface is a `__main__`
    that names no role and never imports deeper than its own directory (composing
    same-dir siblings and sibling/parent packages -- the `data/` + `sources/` ingestion
    shape) is a dispatcher, not a `lib -> api -> surface` vertical, and is skipped. A
    source adapter has no `__main__` at all and is likewise not a vertical.
    """
    verticals: list[Vertical] = []
    seen: set[Path] = set()
    for pkg in _top_packages(src_roots):
        for directory in [pkg, *sorted(p for p in pkg.rglob("*") if p.is_dir())]:
            if directory in seen or directory.name in NON_VERTICAL_DIRS:
                continue
            if not (directory / "__init__.py").is_file() and directory != pkg:
                continue
            files = {
                p.stem: p
                for p in sorted(directory.glob("*.py"))
                if p.stem != "__init__"
            }
            subpkgs = _subpackages(directory)

            # Surfaces by name -- the only analysis anchors. No surface -> library.
            surfaces: list[str] = []
            if CANON_MAIN in files:
                surfaces.append(CANON_MAIN)
            if _has_role(CANON_MCP, files, subpkgs):
                surfaces.append(CANON_MCP)
            if not surfaces:
                continue

            api = CANON_API if _has_role(CANON_API, files, subpkgs) else None
            lib = CANON_LIB if _has_role(CANON_LIB, files, subpkgs) else None

            # Tolerant discovery -- the __main__-depth test. A package whose only surface
            # is a __main__, that names no api or lib and never descends into a deeper
            # in-package layer, is a dispatch/composition surface (the data/ + sources/
            # ingestion shape), not a vertical. A genuine vertical's __main__ caps an
            # in-package stack it reaches deeper into, or the package names a role.
            if (
                set(surfaces) <= {CANON_MAIN}
                and api is None
                and lib is None
                and not _main_imports_deeper(directory, _dotted(pkg, directory))
            ):
                continue

            modules = dict(files)
            if CANON_MCP in subpkgs and CANON_MCP not in modules:
                init = directory / CANON_MCP / "__init__.py"
                if init.is_file():
                    modules[CANON_MCP] = init

            seen.add(directory)
            verticals.append(
                Vertical(
                    name=directory.name,
                    prefix=_dotted(pkg, directory),
                    modules=modules,
                    lib=lib,
                    api=api,
                    surfaces=sorted(surfaces),
                )
            )
    return verticals


def _django_vertical(root: Path) -> Vertical | None:
    """The single django surface: `server/manage.py` present (SURFACES.md §7).

    The whole server is ONE surface, not a bag of per-app verticals. It carries no
    modules and, absent a `[tool.racecar.roles]` mapping that names an `api`, it stays
    silent (a surface with no api anchor -- nothing to verify).
    """
    if not (root / "server" / "manage.py").is_file():
        return None
    return Vertical(name="server", prefix="server", modules={}, surfaces=[CANON_DJANGO])


def _main_imports_deeper(directory: Path, prefix: str) -> bool:
    """True if ``directory/__main__.py`` imports a module nested BELOW ``directory`` (a
    subpackage / deeper module).

    The discriminator between a vertical and a dispatcher. A source adapter
    (``sources/<protocol>/``) has no ``__main__`` at all (a pure library, invoked by the
    ``data`` parent) -> False. A ``data`` dispatcher's ``__main__`` reaches only same-dir
    command modules and sibling/parent packages, never deeper -> False. Only a genuine
    vertical's ``__main__`` caps an in-package stack by importing its own subpackage ->
    True.
    """
    main = directory / "__main__.py"
    if not main.is_file():
        return False
    subpkgs = _subpackages(directory)
    if not subpkgs:
        return False
    try:
        tree = ast.parse(main.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module and node.module.split(".")[0] in subpkgs:
                return True  # from .subpkg[...] import ...
            if node.level == 1 and node.module is None:
                if any(alias.name in subpkgs for alias in node.names):
                    return True  # from . import subpkg
            if (
                node.module
                and node.module.startswith(prefix + ".")
                and node.module[len(prefix) + 1 :].split(".")[0] in subpkgs
            ):
                return True  # absolute import into a subpackage
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    alias.name.startswith(prefix + ".")
                    and alias.name[len(prefix) + 1 :].split(".")[0] in subpkgs
                ):
                    return True
    return False


# --- role identification: name or mapping only -------------------------------


def _identify(v: Vertical, manifest_by_prefix: dict[str, dict]) -> list[Finding]:
    """Apply any manifest mapping over the name-detected roles; return Findings.

    Roles are already set from canonical names during discovery. The manifest (Tier 2)
    is authority when present: it can rename `lib`/`api` and re-declare `surfaces`.
    There is no Tier 3 -- nothing is inferred from the import graph.
    """
    entry = manifest_by_prefix.get(v.prefix) or manifest_by_prefix.get(v.name)
    if entry:
        v.tier = "manifest"
        lib = _short(entry.get("lib"), v.prefix)
        api = _short(entry.get("api"), v.prefix)
        if lib is not None:
            v.lib = lib
        if api is not None:
            v.api = api
        surfaces = [
            s for s in (_short(f, v.prefix) for f in entry.get("surfaces", [])) if s
        ]
        if surfaces:
            v.surfaces = surfaces

    if v.api is None:
        # A surface with no api named or mapped: the api is the anchor, and with none
        # there is nothing to verify. Silent -- do not nag (SURFACES.md §7).
        return []
    if v.lib is None:
        return [
            Finding(
                v.name,
                "api-without-lib",
                f"api '{v.api}' fronts no lib; add lib.py/lib/ or declare it in "
                "[tool.racecar.roles]",
            )
        ]
    return []


def _short(dotted: object, prefix: str) -> str | None:
    """Reduce a manifest dotted module to its in-vertical short name."""
    if not isinstance(dotted, str) or not dotted:
        return None
    if dotted.startswith(prefix + "."):
        return dotted[len(prefix) + 1 :].split(".")[0]
    return dotted.split(".")[-1]


# --- restated-orchestration detection ----------------------------------------


def _api_aliases(tree: ast.AST, api_dotted: str) -> set[str]:
    """Local names that, when called, count as api calls in this surface."""
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
    """Flag api-call windows that appear across two or more surfaces of a vertical."""
    findings: list[Finding] = []
    for v in verticals:
        if not v.api or not v.surfaces:
            continue
        api_dotted = f"{v.prefix}.{v.api}" if "." not in v.api else v.api
        per_face: dict[str, set[tuple[str, ...]]] = {}
        for surface in v.surfaces:
            path = v.modules.get(surface)
            if path is None:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            aliases = _api_aliases(tree, api_dotted)
            if aliases:
                per_face[surface] = _windows(_api_sequence(tree, aliases), threshold)
        shared: dict[tuple[str, ...], list[str]] = {}
        for surface, windows in per_face.items():
            for window in windows:
                shared.setdefault(window, []).append(surface)
        for window, surfaces in sorted(shared.items(), key=lambda x: (-len(x[0]), x[0])):
            if len(surfaces) >= 2:
                findings.append(
                    Finding(
                        v.name,
                        "restated-orchestration",
                        f"api-call sequence [{' -> '.join(window)}] appears in surfaces "
                        f"{surfaces}: one policy with two homes -- move it into api",
                    )
                )
    return findings


# --- entry point -------------------------------------------------------------


def main(argv: list[str]) -> int:
    """Validate each discovered unit's surface orchestration; return an exit code."""
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--strict", action="store_true", help="exit 1 on any Finding")
    args = parser.parse_args(argv)

    pyproject = _library_pyproject(args.root)
    if pyproject is None:
        print("check_surface_orchestration: pyproject.toml not found; nothing to check")
        return 0
    shape, _ = detect_shape(args.root)
    src_roots = _src_roots(args.root, shape.name)

    verticals = _discover_verticals(src_roots)
    django = _django_vertical(args.root)
    if django is not None:
        verticals.append(django)
    if not verticals:
        print("check_surface_orchestration: no surfaces verticals found; nothing to check")
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

    multi = [v for v in verticals if v.surfaces]
    print(
        f"check_surface_orchestration: {len(verticals)} vertical(s), "
        f"{len(multi)} with surfaces"
    )
    if not findings:
        print("check_surface_orchestration: OK (advisory)")
        return 0

    print(
        "check_surface_orchestration: Findings (advisory; ask 'should this live in api?'):"
    )
    for f in findings:
        print(f"  - [{f.vertical}] {f.rule}: {f.message}")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
