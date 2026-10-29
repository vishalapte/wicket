#!/usr/bin/env python3
"""Advisory surfaces detector (arch-python/SURFACES.md §7).

One library is exposed through thin surfaces (`lib -> api -> {cli, mcp, web/django}`).
Orchestration policy (resolve inputs, seed credentials, default, dispatch) lives in `api`;
surfaces translate transport input, call `api`, and render output (SURFACES.md §3-§5).

This script is advisory: exit 0 by default, `--strict` to exit 1 on any Finding. It is
surface-rooted and identifies roles by name or mapping only.

  1. Surfaces are the only analysis anchors (SURFACES.md §7):
       - `cli`     a package's `__main__.py`.
       - `mcp`     a module `mcp.py` OR an `mcp/` package.
       - `django`  the presence of `server/manage.py` (the whole server is one surface).
     A package with no surface is a library and is not analyzed.

     A package whose only surface is a `__main__` that never imports deeper than its own
     directory (`_main_imports_deeper`) is a dispatcher, not a vertical, and is skipped. A
     `sources/<protocol>` adapter has no `__main__` and is likewise skipped.

  2. Role identification, by name or mapping only (SURFACES.md §5):
       - `api` = a module `api.py` OR a package `api/`; OR the module named in
         `[tool.racecar.roles]`.
       - `lib` = a module `lib.py` OR a package `lib/`; OR mapped.
     Nothing is inferred from the import graph. An ambiguity is resolved by one manifest line.

  3. Findings (advisory):
       - `api-without-lib`: a unit with a surface whose `api` is named/mapped but has
         no `lib` (named/mapped) and fronts no delivered script. An api fronts a script
         under `.racecar/scripts/` when its own source names it, as a filename
         (`docs_orchestrate.py`) or a package path (`lib.lexicon`), and the name resolves
         there (SURFACES.md §3).
       - `restated-orchestration`: an api-call window appearing across two or more
         surfaces of the same unit; move it into `api`.
     A unit with a surface but no `api` is silent.

Pure stdlib (tomllib + ast). Shape comes from check_packaging.detect_shape. The library's
verticals are walked inside its one package, `package_dir` (lib/shared/_root.py); with no
package there is nothing to check, and the reason says so (`not_present`).

Usage (invoked by `make arch`):
    python scripts/check_surface_orchestration.py [--root <dir>] [--threshold N] [--strict]

--root defaults to CWD. Exit 0 always unless --strict and a Finding was reported.

Complexity: O(F)
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from check_packaging import Shape, detect_shape
from lib.shared._constants import DELIVERY_DIR
from lib.shared._root import not_present, package_dir

# Minimum length of a repeated api-call sequence to flag as restated orchestration.
# One shared call is normal; two or more in the same order across surfaces is a restatement.
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
    fronts: list[str] = field(
        default_factory=list
    )  # delivered scripts an api-only unit fronts


@dataclass
class Finding:
    """A single surface-orchestration finding: which unit, which rule, why."""

    vertical: str
    rule: str
    message: str


# --- pyproject + shape discovery (shape via check_packaging.detect_shape;
# --- source roots + package walk are the local helpers below) ----------------


def _library_pyproject(shape: Shape) -> Path | None:
    pyproject = shape.library_pyproject
    if pyproject is None or not pyproject.is_file():
        return None
    return pyproject


def _manifest(pyproject: Path) -> list[dict[str, Any]]:
    """Return the `[[tool.racecar.roles.vertical]]` entries (may be empty)."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    roles = data.get("tool", {}).get("racecar", {}).get("roles", {})
    verticals = roles.get("vertical", [])
    return [v for v in verticals if isinstance(v, dict)]


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


def _dir_files_and_subpackages(directory: Path) -> tuple[dict[str, Path], set[str]]:
    """`(files, subpackages)` from one `iterdir()` pass."""
    files: dict[str, Path] = {}
    subpkgs: set[str] = set()
    for p in sorted(directory.iterdir()):
        if p.is_dir():
            if (p / "__init__.py").is_file():
                subpkgs.add(p.name)
        elif p.suffix == ".py" and p.stem != "__init__":
            files[p.stem] = p
    return files, subpkgs


def _has_role(name: str, files: dict[str, Path], subpkgs: set[str]) -> bool:
    """A canonical role is present as a module (`name.py`) OR a package (`name/`)."""
    return name in files or name in subpkgs


def _discover_verticals(packages: list[Path]) -> list[Vertical]:
    """A unit is a package that owns a surface (SURFACES.md §7).

    Surfaces are detected by name: `cli` = `__main__.py`; `mcp` = `mcp.py` or an `mcp/`
    package. A package with no surface is a library and is not discovered. Roles are
    recognized by canonical name (module or package form); the manifest (`_identify`) can
    rename them.

    A package whose only surface is a `__main__` that names no role and never imports deeper
    than its own directory is a dispatcher, not a vertical, and is skipped.
    """
    verticals: list[Vertical] = []
    seen: set[Path] = set()
    for pkg in packages:
        for directory in [pkg, *sorted(p for p in pkg.rglob("*") if p.is_dir())]:
            if directory in seen or directory.name in NON_VERTICAL_DIRS:
                continue
            if not (directory / "__init__.py").is_file() and directory != pkg:
                continue
            files, subpkgs = _dir_files_and_subpackages(directory)

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

            # The __main__-depth test: a package whose only surface is a __main__ that names
            # no role and never descends into a deeper in-package layer is a dispatcher.
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


def _django_verticals(root: Path, shape: Shape) -> list[Vertical]:
    """One django surface per Django project the shape locates (SURFACES.md §7).

    Each top-level folder holding a `manage.py` is a vertical named for that folder, or `.`
    for a root `manage.py` (the flat `django` shape). Each project is one surface. It carries
    no modules and stays silent unless `[tool.racecar.roles]` names an `api`.
    """
    return [
        Vertical(name=home, prefix=home, modules={}, surfaces=[CANON_DJANGO])
        for home in (
            folder.relative_to(root).as_posix() for folder in shape.django_dirs
        )
    ]


def _import_reaches_subpackage(node: ast.AST, prefix: str, subpkgs: set[str]) -> bool:
    """True if a single import ``node`` reaches a subpackage in ``subpkgs``.

    Relative and absolute forms are mutually exclusive per node, so the order of the early
    returns does not matter.
    """
    if isinstance(node, ast.ImportFrom):
        if node.level == 1 and node.module and node.module.split(".")[0] in subpkgs:
            return True  # from .subpkg[...] import ...
        if node.level == 1 and node.module is None:
            return any(
                alias.name in subpkgs for alias in node.names
            )  # from . import subpkg
        return bool(  # absolute import into a subpackage
            node.module
            and node.module.startswith(prefix + ".")
            and node.module[len(prefix) + 1 :].split(".")[0] in subpkgs
        )
    if isinstance(node, ast.Import):
        return any(
            alias.name.startswith(prefix + ".")
            and alias.name[len(prefix) + 1 :].split(".")[0] in subpkgs
            for alias in node.names
        )
    return False


def _main_imports_deeper(directory: Path, prefix: str) -> bool:
    """True if ``directory/__main__.py`` imports a module nested below ``directory``.

    This separates a vertical from a dispatcher: only a vertical's ``__main__`` imports its
    own subpackage. A source adapter has no ``__main__``, and a ``data`` dispatcher reaches
    only same-dir modules and sibling/parent packages, so both return False.
    """
    main_py = directory / "__main__.py"
    if not main_py.is_file():
        return False
    subpkgs = _subpackages(directory)
    if not subpkgs:
        return False
    try:
        tree = ast.parse(main_py.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return False
    return any(
        _import_reaches_subpackage(node, prefix, subpkgs) for node in ast.walk(tree)
    )


# --- role identification: name or mapping only -------------------------------


#: What a delivered reference looks like in an api's source: a bare script filename, or a
#: dotted package path of two or more segments (`load_package`'s import-path form).
_SCRIPT_NAME = re.compile(r"^[A-Za-z0-9_][\w.-]*\.(?:py|sh)$")
_PACKAGE_PATH = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")


def _api_sources(v: Vertical) -> list[Path]:
    """The api's own source files: `api.py`, or every `.py` under an `api/` package."""
    if not v.modules:
        return []
    directory = next(iter(v.modules.values())).parent
    module = directory / f"{v.api}.py"
    if module.is_file():
        return [module]
    package = directory / str(v.api)
    return sorted(package.rglob("*.py")) if package.is_dir() else []


def _string_constants(source: str) -> list[str]:
    """Every string constant in `source` except docstrings."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _delivered(root: Path, name: str) -> bool:
    """Whether `name`, a script filename or a package path, is under `DELIVERY_DIR`.

    A dotted path resolves as `load_package` resolves it: its first segment is a package
    there, and the rest names a module or package under it.
    """
    delivery = root / DELIVERY_DIR
    if _SCRIPT_NAME.match(name):
        return (delivery / name).is_file()
    parts = name.split(".")
    if not (delivery / parts[0] / "__init__.py").is_file():
        return False
    base = delivery.joinpath(*parts)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _delivered_references(v: Vertical, root: Path) -> tuple[list[str], list[str]]:
    """(resolved, unresolved script names) among the strings an api's source carries.

    Every non-docstring constant is read, not only a literal at a dispatch call, because an
    api may pass its scripts through a table (`arch` does). A string naming a delivered
    script without dispatching to it therefore clears the finding. Unresolved package paths
    are not returned, since any dotted string (`os.path`) has that shape.
    """
    resolved: set[str] = set()
    unresolved: set[str] = set()
    for path in _api_sources(v):
        try:
            constants = _string_constants(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        for value in constants:
            if _SCRIPT_NAME.match(value):
                (resolved if _delivered(root, value) else unresolved).add(value)
            elif _PACKAGE_PATH.match(value) and _delivered(root, value):
                resolved.add(value)
    return sorted(resolved), sorted(unresolved)


def _identify(
    v: Vertical, manifest_by_prefix: dict[str, dict[str, Any]], root: Path
) -> list[Finding]:
    """Apply any manifest mapping over the name-detected roles; return Findings.

    Roles are already set from canonical names during discovery. The manifest, when present,
    can rename `lib`/`api` and re-declare `surfaces`. Nothing is inferred from the import graph.
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
        # A surface with no api named or mapped: nothing to verify (SURFACES.md §7).
        return []
    if v.lib is None:
        resolved, unresolved = _delivered_references(v, root)
        if resolved:
            v.fronts = resolved
            return []
        named = (
            f"; it names {', '.join(unresolved)}, which resolves nowhere under "
            f"{DELIVERY_DIR}/"
            if unresolved
            else ""
        )
        return [
            Finding(
                v.name,
                "api-without-lib",
                f"api '{v.api}' fronts no lib and no delivered script{named}; add "
                "lib.py/lib/ or declare it in [tool.racecar.roles]",
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


def _api_name(call: ast.Call, aliases: set[str]) -> str | None:
    """`api.verb` or `verb` when this call reaches `api` through one of `aliases`."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in aliases:
            return f"{func.value.id}.{func.attr}"
    elif isinstance(func, ast.Name) and func.id in aliases:
        return func.id
    return None


def _own_calls(stmt: ast.stmt, aliases: set[str]) -> list[str]:
    """The api calls `stmt` itself makes, in source order.

    A call in a nested statement list (an `if` body, a loop body, a function body) or in
    a `lambda` belongs to that inner code, not to `stmt`. A call in an `if` test or a
    `with` item runs where the `if` or `with` stands, so it is `stmt`'s own.
    """
    found: list[tuple[int, int, str]] = []
    stack = [
        child
        for child in ast.iter_child_nodes(stmt)
        if not isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case))
    ]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.Call):
            name = _api_name(node, aliases)
            if name:
                found.append((node.lineno, node.col_offset, name))
        stack.extend(
            c for c in ast.iter_child_nodes(node) if not isinstance(c, ast.stmt)
        )
    return [name for _, _, name in sorted(found)]


def _api_sequences(tree: ast.AST, aliases: set[str]) -> list[list[str]]:
    """One api-call sequence per statement list, each in source order.

    A sequence is the calls one statement list's own statements make: a function body, an
    `if` body or its `else`, a loop, `with`, `try`, handler or `finally` body. Calls in
    different branches never run in one invocation, so they are never joined.
    """
    sequences: list[list[str]] = []
    for node in ast.walk(tree):
        for attr in ("body", "orelse", "finalbody"):
            block = getattr(node, attr, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                seq = [name for stmt in block for name in _own_calls(stmt, aliases)]
                if seq:
                    sequences.append(seq)
    return sequences


def _windows(seq: list[str], size: int) -> set[tuple[str, ...]]:
    return {tuple(seq[i : i + size]) for i in range(len(seq) - size + 1)}


def _restated(verticals: list[Vertical], threshold: int) -> list[Finding]:
    """Flag api-call windows that appear across two or more surfaces of a vertical.

    Each surface's call sequence is reduced to its set of fixed-size sliding windows
    (`_windows`, w-shingling); a window shared by two or more surfaces is flagged.
    """
    out: list[Finding] = []
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
                per_face[surface] = {
                    window
                    for seq in _api_sequences(tree, aliases)
                    for window in _windows(seq, threshold)
                }
        shared: dict[tuple[str, ...], list[str]] = {}
        for surface, windows in per_face.items():
            for window in windows:
                shared.setdefault(window, []).append(surface)
        for window, surfaces in sorted(
            shared.items(), key=lambda x: (-len(x[0]), x[0])
        ):
            if len(surfaces) >= 2:
                out.append(
                    Finding(
                        v.name,
                        "restated-orchestration",
                        f"api-call sequence [{' -> '.join(window)}] appears in surfaces "
                        f"{surfaces}: one policy with two homes -- move it into api",
                    )
                )
    return out


# --- entry point -------------------------------------------------------------


def _survey(root: Path) -> tuple[list[Vertical], dict[str, dict[str, Any]], str | None]:
    """Find the verticals to analyse and the manifest that renames their roles.

    The third value is a reason to stop, or `None` to carry on.
    """
    shape, _ = detect_shape(root)
    pyproject = _library_pyproject(shape)
    if pyproject is None:
        return [], {}, "pyproject.toml not found; nothing to check"
    # The library's verticals live in its one package (`package_dir`); the Django verticals
    # are found where `django_roots` says Django lives.
    package = package_dir(root)
    verticals = _discover_verticals([package] if package is not None else [])
    verticals += _django_verticals(root, shape)
    if not verticals:
        if package is None:
            return [], {}, not_present(root)
        return [], {}, "no surfaces verticals found; nothing to check"

    # Keyed by both spellings, `prefix` and `name`, because the lookup below tries both.
    manifest_by_prefix: dict[str, dict[str, Any]] = {}
    for entry in _manifest(pyproject):
        for key in (entry.get("name"), entry.get("prefix")):
            if key:
                manifest_by_prefix[str(key)] = entry
    return verticals, manifest_by_prefix, None


def _findings(
    verticals: list[Vertical],
    manifest_by_prefix: dict[str, dict[str, Any]],
    threshold: int,
    root: Path,
) -> list[Finding]:
    """Both kinds of finding over an already-discovered set of verticals."""
    out: list[Finding] = []
    for vertical in verticals:
        out.extend(_identify(vertical, manifest_by_prefix, root))
    out.extend(_restated(verticals, threshold))
    return out


def findings(root: Path, threshold: int = DEFAULT_THRESHOLD) -> list[Finding]:
    """Every advisory finding in `root`. Prints nothing.

    An empty list means either a clean tree or nothing to check; `skipped` tells them apart.
    """
    verticals, manifest_by_prefix, skip = _survey(root)
    if skip is not None:
        return []
    return _findings(verticals, manifest_by_prefix, threshold, root)


def skipped(root: Path) -> str | None:
    """Why there was nothing to grade here, or None when there was something.

    `findings` returns an empty list both for a clean tree and for one with nothing to check.
    A caller that must not print OK for the second asks this.
    """
    return _survey(root)[2]


def render(records: list[Finding]) -> str:
    """The findings report `main` prints, built from `findings`.

    Empty when there is nothing to report. The vertical count `main` prints is not part
    of it.
    """
    if not records:
        return ""
    lines = [
        "check_surface_orchestration: Findings "
        "(advisory; ask 'should this live in api?'):"
    ]
    lines += [f"  - [{f.vertical}] {f.rule}: {f.message}" for f in records]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """Validate each discovered unit's surface orchestration; return an exit code."""
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--strict", action="store_true", help="exit 1 on any Finding")
    args = parser.parse_args(argv)

    verticals, manifest_by_prefix, skip = _survey(args.root)
    if skip is not None:
        print(f"check_surface_orchestration: {skip}")
        return 0

    found = _findings(verticals, manifest_by_prefix, args.threshold, args.root)

    multi = [v for v in verticals if v.surfaces]
    print(
        f"check_surface_orchestration: {len(verticals)} vertical(s), "
        f"{len(multi)} with surfaces"
    )
    if not found:
        print("check_surface_orchestration: OK (advisory)")
        return 0

    print(render(found))
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
