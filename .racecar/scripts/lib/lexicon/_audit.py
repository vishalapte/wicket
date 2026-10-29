"""Reading the TREE: what the CLI offers, what the code declares, where canon is.

Part of `lib.lexicon`, the package `scripts/lexicon.py` is a command line over.

Complexity: O(A + S), A = one CLI audit (subprocess per leaf), S = delivered scripts parsed
"""

from __future__ import annotations

import ast
import contextlib
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from lib import SCRIPTS, load_file
from lib.lexicon._corpora import Lexicon, LexiconError
from lib.lexicon._nodes import (
    SKIP_DIRS,
    VocabularyError,
    param_fields,
)
from lib.shared import _frontmatter
from lib.shared._files import repo_files
from lib.shared._root import not_present, package_dir


def verb_gap(verb: str, offered: set[str] | None, reachable: set[str]) -> str | None:
    """Why a declared verb is not real, or None when it is.

    First, does the command exist? `offered` is what the CLI answers to; `None` means there
    is no CLI to ask (a noun served only by a delivered script), so the question is
    unanswered rather than answered no.

    Second, can anything but the CLI reach it? `reachable` is what the noun's `api.py`
    exposes; a verb the api does not expose can only be run from the command line.
    """
    if offered is not None and verb not in offered:
        return "`python -m {module}` does not offer. The docs list a command that is not there."
    if verb not in reachable:
        return (
            "{api} does not expose. Every surface reaches a noun through its api, so a "
            "verb missing from it can only ever be run from the command line."
        )
    return None


def _cli_gap(verb: str, offered: set[str] | None) -> str | None:
    """`verb_gap` with the api question withheld, for the root, which has no api of its own."""
    if offered is not None and verb not in offered:
        return "`python -m {module}` does not offer. The docs list a command that is not there."
    return None


#: The module-level name an api may use to map a CLI spelling to its binding.
_VERBS = "VERBS"


def api_functions(module: Path) -> set[str]:
    """Every name the vertical's `api` exposes: its own defs, plus what it re-exports.

    Read with `ast`, never imported: a vertical whose `api.py` does not import is a state
    this reports. A re-export (`from .lib._check import check`) counts.
    """
    # A verb's own sub-api beside the noun's counts as reachable: the noun's `__main__.py`
    # dispatches through it.
    names: set[str] = set()
    declared: set[str] = set()
    for source in _api_sources(module):
        tree = _parse(source)
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.ImportFrom):
                names.update(alias.asname or alias.name for alias in node.names)
            else:
                declared |= _verbs_map_keys(node)
    bound = {name for name in names if re.match(r"^[a-z][a-z0-9_]*$", name)}
    return bound | declared


def _api_sources(module: Path) -> list[Path]:
    """The files `api_functions` reads, in the order it reads them."""
    sources = [module / "api.py", module / "api" / "__init__.py"]
    sources += [
        child / "api.py"
        for child in sorted(module.glob("*/"))
        if (child / "api.py").is_file() and not (child / "__main__.py").is_file()
    ]
    return [source for source in sources if source.is_file()]


def _parse(source: Path) -> ast.Module | None:
    """Parse `source`, or None where it does not parse."""
    try:
        return ast.parse(source.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return None


def _verbs_map_keys(node: ast.stmt) -> set[str]:
    """The string keys of a module-level `VERBS = {...}` dict literal.

    `VERBS` maps a CLI spelling to its Python binding, so a verb can be spelled `list` or
    `import`. Optional: without it a repo is graded by binding name alone.
    """
    value = _verbs_value(node)
    if not isinstance(value, ast.Dict):
        return set()
    return {
        key.value
        for key in value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _verbs_value(node: ast.stmt) -> ast.expr | None:
    """The value bound to module-level `VERBS`, annotated or not; None for anything else."""
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        targets: list[ast.expr] = [node.target]
    elif isinstance(node, ast.Assign):
        targets = node.targets
    else:
        return None
    if not any(isinstance(t, ast.Name) and t.id == _VERBS for t in targets):
        return None
    return node.value


def unreadable_verb_maps(module: Path) -> list[str]:
    """Findings for a `VERBS` this checker cannot read (one built at run time)."""
    findings: list[str] = []
    for source in _api_sources(module):
        tree = _parse(source)
        if tree is None:
            continue
        for node in tree.body:
            value = _verbs_value(node)
            if value is None:
                continue
            if not isinstance(value, ast.Dict):
                findings.append(f"{_VERBS} is not a dict literal")
            elif any(
                not (isinstance(k, ast.Constant) and isinstance(k.value, str))
                for k in value.keys
            ):
                findings.append(f"{_VERBS} has a key that is not a string literal")
    return findings


_SCALARS = {"date", "datetime", "string"}

_EXPECTED: dict[str, set[str]] = {
    "boolean": {"bool"},
    "path": {"Path"},
    "integer": {"int"},
    # A flag declared with `choices=` is a closed set; the node can be held to it.
    "enum": {"enum"},
}

#: Which kinds are a stricter version of which other kinds, local kind -> canon kinds it
#: may narrow. An `enum` (argparse `choices=`) narrows canon's `string`. One pair, not a
#: lattice; widening is never allowed.
_NARROWS: dict[str, set[str]] = {"enum": {"string"}}

_DISTINGUISHABLE = {"bool", "Path", "int", "enum"}


@dataclass(frozen=True)
class Node:
    """One flag node, from whichever tree declared it."""

    where: str
    name: str
    kind: str
    canon: bool
    required: bool = False
    defined: bool = False
    position: tuple[str, ...] = ()


def _fields(meta: dict[str, Any]) -> dict[str, Any]:
    """`param_fields`, shaped for a frozen `Node`."""
    fields = param_fields(meta)
    return {**fields, "position": tuple(fields["position"])}


def flag_nodes(lexicon: Lexicon) -> list[Node]:
    """Every param node of the union, in precedence order, each marked canon or not.

    Each node appears once, under its real path: the repo's own `param/` nodes, then an
    explicit `--canon`'s, then the delivered copy's. A node is canon when racecar wrote it,
    or a caller named its home as canon (`Lexicon.is_canon`). A word in two homes is
    returned twice, the local node and the canon one.
    """
    found: list[Node] = []
    for entry in lexicon.nodes(under="param"):
        if entry.filename == "README.md":
            continue
        meta = _frontmatter.load(lexicon.path(entry))
        found.append(
            Node(
                where=f"{entry.directory}/{entry.filename}",
                name=meta.get("name", ""),
                kind=meta.get("type", ""),
                canon=lexicon.is_canon(entry),
                **_fields(meta),
            )
        )
    return found


def flag_clashes(lexicon: Lexicon) -> list[tuple[str, str]]:
    """`(where, what)` for each param node that disagrees on its type with another node for
    the same word.

    - the same side (both canon, or both the repo's own): the second disagrees with the
      first, which is the one in force;
    - the repo's own against canon: a local node may extend the vocabulary and may not
      redefine it, though it may narrow it (`_NARROWS`).

    Reported; a run fails on these only under `--strict`.
    """
    first: dict[tuple[bool, str], Node] = {}
    out: list[tuple[str, str]] = []
    nodes = flag_nodes(lexicon)
    for node in nodes:
        seen = first.setdefault((node.canon, Path(node.where).stem), node)
        if seen is not node and seen.kind != node.kind:
            out.append(
                (
                    node.where,
                    f"says `type: {node.kind}` and {seen.where} says `type: {seen.kind}`: "
                    "one word, two meanings. The first is the one in force.",
                )
            )
    for node in nodes:
        fixed = first.get((True, Path(node.where).stem))
        if node.canon or fixed is None or fixed.kind == node.kind:
            continue
        if fixed.kind in _NARROWS.get(node.kind, set()):
            continue
        out.append(
            (
                node.where,
                f"`type: {node.kind}` contradicts canon, which declares `{fixed.kind}` in "
                f"{fixed.where}. A local node may extend the vocabulary and may not "
                "redefine it — take the disagreement to racecar rather than overriding it "
                "here.",
            )
        )
    return out


def has_cli(root: Path) -> bool:
    """Whether this repo has a CLI the audit can walk: a `__main__.py` in a package (§3).

    The audit walks the repo's one package (`package_dir`); a `__main__.py` elsewhere
    (such as under `.racecar/templates/`) does not count.
    """
    package = package_dir(root)
    return package is not None and any(
        not (set(path.relative_to(root).parts) & SKIP_DIRS)
        for path in repo_files(package, "__main__.py")
    )


def _flat_args(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An arg list with mutually-exclusive groups flattened out of their `oneOf` nesting."""
    out: list[dict[str, Any]] = []
    for entry in group:
        if entry.get("oneOf"):
            out.extend(_flat_args(entry["oneOf"]))
        else:
            out.append(entry)
    return out


@contextlib.contextmanager
def audited(root: Path, package: Path) -> Iterator[None]:
    """Run one in-process CLI audit of `root` and leave the interpreter as it was.

    `package` is the package directory to audit, walked by its name.

    Restored afterwards:

    * the working directory -- the audit resolves its root against it;
    * one `sys.path` entry -- `package.parent`, or `root` itself, because a `src/`
      layout resolves as a namespace package and its nodes import as `src.<pkg>`;
    * whatever `sys.modules` gained -- otherwise a second audit of a different repo with
      the same package name gets the first repo's modules from the cache.

    Only modules this audit added are dropped, never the whole `<pkg>.*` namespace: this
    process may be the audited repo, with its own modules live and referenced. A path
    entry that was already there is left.

    Bytecode writing is off for the audit, so no `__pycache__/` lands in the audited repo.
    """
    entry, top = str(package.parent), package.name
    previous = Path.cwd()
    added = entry if entry not in sys.path else None
    # A copy of the package imported from elsewhere (an installed racecar, another
    # checkout) steps aside for the audit and comes back after.
    foreign = _foreign_copies(package)
    for name in foreign:
        del sys.modules[name]
    before = set(sys.modules)
    bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        os.chdir(root)
        if added is not None:
            sys.path.insert(0, added)
        yield
    finally:
        sys.dont_write_bytecode = bytecode
        os.chdir(previous)
        if added is not None:
            with contextlib.suppress(ValueError):
                sys.path.remove(added)
        for name in [
            m for m in sys.modules if m.split(".")[0] == top and m not in before
        ]:
            sys.modules.pop(name, None)
        sys.modules.update(foreign)


def _foreign_copies(package: Path) -> dict[str, ModuleType]:
    """The modules of `package`'s name already imported from outside `package`."""
    name, home = package.name, package.resolve()
    out: dict[str, ModuleType] = {}
    for key, module in list(sys.modules.items()):
        if key != name and not key.startswith(name + "."):
            continue
        where = getattr(module, "__file__", None)
        if where and not Path(where).resolve().is_relative_to(home):
            out[key] = module
    return out


def _audit_module(root: Path) -> ModuleType:
    """Import the CLI audit from `scripts/` beside this file, as a module private to
    this root.

    Loaded by `load_file` under a name keyed on `root`, so a second repo never gets the
    first one's module. Loading puts nothing on `sys.path`; each call site wraps the walk
    in `audited`.
    """
    here = SCRIPTS
    for directory in (root / "scripts", here):
        source = directory / "check_cli_commands.py"
        if source.is_file():
            name = f"_check_vocabulary_audit__{abs(hash(str(root)))}"
            try:
                return load_file(source, name)
            except ImportError as err:
                raise VocabularyError(
                    f"cannot import the CLI audit from {directory}: {err}"
                ) from err
    raise VocabularyError(
        "no check_cli_commands.py under scripts/ — the flag "
        "check reads the CLI audit rather than re-walking the tree, and cannot "
        "substitute a second opinion about what the CLI declares"
    )


#: The two violations the CLI audit records for a node whose module did not import: a
#: `__main__.py` that raised, and a runnable `.py` module that raised.
_UNREAD = ("raised on import", "not importable: ")


def unread_nodes(node: dict[str, Any]) -> list[str]:
    """Each CLI node the audit could not import, with the reason the audit recorded.

    Such a node reports no verbs and no flags, which would otherwise read as empty.
    """
    out = [
        f"{node['pkg']} ({violation})"
        for violation in node.get("violations") or []
        if any(marker in str(violation) for marker in _UNREAD)
    ]
    for child in node.get("children") or []:
        out += unread_nodes(child)
    return out


def cli_tree(root: Path) -> dict[str, Any]:
    """One CLI audit of `root`, walked by its package's name, refused where it read nothing.

    The one way every reader here enters the audit. Walked bare, `src` is a namespace
    package and each node's `from <pkg>.<noun> import api` may resolve to another
    checkout; walked by name with `src/` first on the path, `<pkg>` is this tree's.

    Closed by default (R-10): a tree with any node the audit could not import is a refusal,
    never a partial answer, as `derive` does.
    """
    pkg = package_dir(root)
    if pkg is None:
        # With no package the audit has no subject.
        raise LexiconError(f"the CLI audit did not run: {not_present(root)}")
    audit = _audit_module(root)
    try:
        with audited(root, pkg), contextlib.redirect_stdout(sys.stderr):
            tree: dict[str, Any] = audit.audit_cli_tree(pkg.name)
    # The audit imports the repo's own code; a failure there, including `sys.exit` at
    # import, is data. Imported modules print to stderr, so `--json` stdout stays one
    # document.
    except (Exception, SystemExit) as err:
        raise LexiconError(f"the CLI audit did not complete: {err}") from err
    unread = unread_nodes(tree)
    if unread:
        raise LexiconError(
            "the CLI audit could not read "
            + "; ".join(unread)
            + ". A node that does not import reports no verbs and no flags, so grading it "
            "would pass on nothing. Run with this repo's own interpreter, the one its code "
            "imports under"
        )
    return tree


def declared_flag_types(root: Path) -> dict[str, set[str]]:
    """Map each flag spelling to the argparse types the CLI audit declares for it."""
    seen: dict[str, set[str]] = {}

    def walk(node: dict[str, Any]) -> None:
        for group in (
            node.get("args") or [],
            *[s.get("args") or [] for s in (node.get("subcommands") or [])],
        ):
            for arg in _flat_args(group):
                for flag in arg.get("flags") or []:
                    kind = arg.get("type") or ("enum" if arg.get("choices") else "str")
                    seen.setdefault(flag, set()).add(kind)
        for child in node.get("children") or []:
            walk(child)

    # An empty result here is a CLI with no flags built yet (`cli_tree` refuses an
    # unreadable one).
    walk(cli_tree(root))
    return seen


def _well_formed(node: Node) -> str | None:
    """The node's own frontmatter, before anything is compared to the code."""
    if not node.name or node.name.startswith("-"):
        return (
            f"{node.where}: frontmatter `name` is the flag's NAME and carries no leading "
            "dashes — `dry-run`, not `--dry-run`. The `--` is the long-option construct, "
            "added when the name is spelled on a command line; argparse agrees, since "
            "`dest` is `dry_run`."
        )
    if node.kind not in _EXPECTED and node.kind not in _SCALARS:
        return (
            f"{node.where}: `type: {node.kind}` is not one of "
            f"{', '.join(sorted(set(_EXPECTED) | _SCALARS))}"
        )
    return None


def flag_sites(root: Path) -> dict[str, set[str]]:
    """Map each flag spelling to the commands that accept it."""
    sites: dict[str, set[str]] = {}

    def walk(node: dict[str, Any], prefix: str = "") -> None:
        pkg = node["pkg"]
        pkg = pkg[len(prefix) :] if prefix and pkg.startswith(prefix) else pkg
        for arg in _flat_args(node.get("args") or []):
            for flag in arg.get("flags") or []:
                sites.setdefault(flag, set()).add(pkg)
        for sub in node.get("subcommands") or []:
            for arg in _flat_args(sub.get("args") or []):
                for flag in arg.get("flags") or []:
                    sites.setdefault(flag, set()).add(f"{pkg} {sub['name']}")
        for child in node.get("children") or []:
            walk(child, prefix)

    # The package, by name, with its parent first on `sys.path` (see `cli_tree`).
    #
    # Sites are keyed without the package prefix -- `api check`, and the root's own verbs as
    # `racecar check` -- where `cli_verbs` keys modules with it. `_status.site_noun` strips
    # it.
    tree = cli_tree(root)
    walk(tree, prefix=f"{tree['pkg']}." if tree.get("children") else "")
    return sites


def script_flag_sites(root: Path) -> dict[str, set[str]]:
    """Each flag a script under `scripts/` accepts, and the scripts that accept it.

    Read with `ast`, never imported: these entry points act at import time. Only literal
    `add_argument("--x")` spellings are seen. Symlinks are skipped, so racecar's own
    delivered checkers linked from `scripts/` are not counted twice.
    """
    sites: dict[str, set[str]] = {}
    directory = root / "scripts"
    if not directory.is_dir():
        return sites
    for path in sorted(directory.glob("*.py")):
        if path.is_symlink():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            named = (
                isinstance(func, ast.Attribute) and func.attr == "add_argument"
            ) or (isinstance(func, ast.Name) and func.id == "add_argument")
            if not named:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.startswith("--"):
                        sites.setdefault(arg.value, set()).add(path.name)
    return sites


def script_flag_types(root: Path) -> dict[str, set[str]]:
    """Each flag a script under `scripts/` declares, mapped to the types it declares it with.

    The same static read as `script_flag_sites`, in `declared_flag_types`' vocabulary so
    the two can be unioned.
    """
    types: dict[str, set[str]] = {}
    directory = root / "scripts"
    if not directory.is_dir():
        return types
    for path in sorted(directory.glob("*.py")):
        if path.is_symlink():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            named = (
                isinstance(func, ast.Attribute) and func.attr == "add_argument"
            ) or (isinstance(func, ast.Name) and func.id == "add_argument")
            if not named:
                continue
            kind = "str"
            for kw in node.keywords:
                if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                    if str(kw.value.value).startswith("store_"):
                        kind = "bool"
                elif kw.arg == "choices":
                    kind = "enum"
                elif kw.arg == "type" and isinstance(kw.value, ast.Name):
                    kind = {"Path": "Path", "int": "int"}.get(kw.value.id, "str")
            for arg in node.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.startswith("--")
                ):
                    types.setdefault(arg.value, set()).add(kind)
    return types


def cli_verbs(root: Path) -> dict[str, set[str]]:
    """Each module's subcommands, from the live CLI audit: the verbs actually implemented.

    Not `api_functions`, which includes helpers.
    """
    verbs: dict[str, set[str]] = {}

    # Keyed by the module's full dotted name, `racecar.api`.
    def walk(node: dict[str, Any]) -> None:
        for sub in node.get("subcommands") or []:
            if sub.get("name"):
                verbs.setdefault(str(node["pkg"]), set()).add(str(sub["name"]))
        for child in node.get("children") or []:
            walk(child)

    walk(cli_tree(root))
    return verbs


def cli_args(root: Path) -> dict[tuple[str, str], tuple[dict[str, Any], ...]]:
    """Each verb's arguments, keyed `(module, verb)` as `cli_verbs` keys its modules.

    From the same live audit, so `required` and position are what argparse builds rather than
    what a source read guesses; a mutually exclusive group's members are listed flat.
    """
    out: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}

    def walk(node: dict[str, Any]) -> None:
        for sub in node.get("subcommands") or []:
            if sub.get("name"):
                out[(str(node["pkg"]), str(sub["name"]))] = tuple(
                    _flat_args(sub.get("args") or [])
                )
        for child in node.get("children") or []:
            walk(child)

    walk(cli_tree(root))
    return out


#: argparse's two group constructors. A flag added to a group made from a verb's parser
#: is that verb's flag.
_GROUP_METHODS = ("add_mutually_exclusive_group", "add_argument_group")


def _hold_groups(tree: ast.AST, holder: dict[str, str]) -> None:
    """Add to `holder` every group made from a verb's parser, mapped to that verb.

    `scope = gen.add_mutually_exclusive_group()` then `scope.add_argument("--all")`: the
    group holds `gen`'s verb. Repeated until a pass adds nothing, so a group made from a
    group resolves. Keyed by bare name across the file, so one group name reused for two
    verbs credits both to one.
    """
    grew = True
    while grew:
        grew = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            made = node.value.func
            if not (isinstance(made, ast.Attribute) and made.attr in _GROUP_METHODS):
                continue
            parent = made.value
            verb_of = holder.get(parent.id) if isinstance(parent, ast.Name) else None
            if verb_of is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in holder:
                    holder[target.id] = verb_of
                    grew = True


def _owner_verb(owner: ast.expr, holder: dict[str, str]) -> str | None:
    """The verb whose parser `owner` adds to: a held name, or a group chain off one.

    The chain covers `gen.add_mutually_exclusive_group().add_argument("--x")`, a group
    never assigned to a name.
    """
    if isinstance(owner, ast.Name):
        return holder.get(owner.id)
    if (
        isinstance(owner, ast.Call)
        and isinstance(owner.func, ast.Attribute)
        and owner.func.attr in _GROUP_METHODS
    ):
        return _owner_verb(owner.func.value, holder)
    return None


def script_surface(root: Path, noun: str) -> dict[str, set[str]]:
    """`{verb: {long flags}}` for a noun backed by `scripts/<noun>.py`, read statically.

    The CLI audit only sees `src/<pkg>/<noun>/`; this covers the delivered-script route
    (`lexicon` is one). Static, as `script_flag_sites` is.
    """
    path = root / "scripts" / f"{noun}.py"
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}

    verbs: dict[str, set[str]] = {}
    holder: dict[str, str] = {}  # local variable name -> the verb it holds
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (
            isinstance(call.func, ast.Attribute) and call.func.attr == "add_parser"
        ):
            continue
        if not (call.args and isinstance(call.args[0], ast.Constant)):
            continue
        verb = str(call.args[0].value)
        verbs.setdefault(verb, set())
        for target in node.targets:
            if isinstance(target, ast.Name):
                holder[target.id] = verb

    _hold_groups(tree, holder)

    def long_flags(call: ast.Call) -> set[str]:
        return {
            arg.value[2:]
            for arg in call.args
            if isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value.startswith("--")
        }

    # A shared options helper (`def _add_common(sub): sub.add_argument("--root", ...)`):
    # collect what each such function adds to its first parameter, then attribute it
    # wherever that function is called with a verb's parser.
    shared: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.args.args:
            continue
        first = node.args.args[0].arg
        flags: set[str] = set()
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "add_argument"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id == first
            ):
                flags |= long_flags(inner)
        if flags:
            shared[node.name] = flags

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in shared and node.args:
            target = node.args[0]
            named = holder.get(target.id) if isinstance(target, ast.Name) else None
            if named is not None:
                verbs[named] |= shared[func.id]
            continue
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        direct = _owner_verb(func.value, holder)
        if direct is not None:
            verbs[direct] |= long_flags(node)
    return verbs


# The loader under a public name: the way into the CLI audit.
audit_module = _audit_module
