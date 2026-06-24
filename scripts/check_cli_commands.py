#!/usr/bin/env python3
"""Enforce arch-coherence/PYTHON.md §3: the `__main__.py` + `commands()` CLI contract.

Scope is deliberately narrow: this script checks commands only. It does not
reimplement §1 (Module Structure) or §2 (Imports) — those are owned by
`import-linter` (acyclicity, direction) and `check_upward_imports.py` (upward
imports from business modules). Run those alongside this one.

At every node in the CLI tree rooted at `<pkg>` the walker confirms:

1. The package exposes `commands() -> list[tuple[str, str]]` with direct-child
   names (no dots).
2. Symbol presence matches one of the three patterns:
     - `commands()` non-empty + no `main()`    → Pattern 1 (pure discovery)
     - `commands()` non-empty +  `main()`      → Pattern 2 (discovery + own CLI)
     - `commands()` empty     +  `main()`      → Pattern 3 (leaf)
   Patterns 1 and 2 must also expose `_print_commands()` — every intermediate
   owns its own print layer, no inheritance.
3. Subprocess behaviour matches the pattern:
     - `python -m <pkg>` with no args exits 0 at every node.
     - For non-empty `commands()`, the no-args output lists exactly one
       `python -m <pkg>.<name>   <desc>` line per entry and stdout is non-empty.
     - `python -m <pkg> --help` exits 0 at every node.
4. Each listed child `<pkg>.<name>` is a real, runnable entry point — either a
   sub-package with its own `__main__.py` (recurse into it) or a `.py` module
   with an `if __name__ == "__main__":` guard.
5. Registration symmetry: the filesystem under `pkg` is scanned for any
   direct-child sub-package with a `__main__.py`, or any `.py` module with an
   `if __name__ == "__main__":` guard, that is NOT in `commands()`. These are
   orphan CLIs — hidden capabilities that no parent names. §3 is explicit:
   registration is manual, not dynamic, so an unregistered deeper `__main__.py`
   is a violation. Orphan sub-packages are descended into so their own subtrees
   are audited too.

# Importable API

    from scripts.check_cli_commands import audit_cli_tree
    tree = audit_cli_tree("fubar")        # enriched Node, JSON-serialisable

Every node in the returned tree carries both the audit affordances AND
the agent-facing resolutions, side by side:

    {
      # --- audit affordances (§3 enforcement) ---
      "pkg":         str,                           # dotted package name
      "kind":        "package" | "module" | "missing",
      "pattern":     "pattern-1" | "pattern-2" | "pattern-3" | "unknown",
      "commands":    [[str, str], ...] | null,      # raw commands() entries
      "orphan":      bool,                          # registered by parent?
      "violations":  [str, ...],                    # messages at this node
      "children":    [<node>, ...],                 # registered + orphan kids

      # --- agent-facing resolutions (enrichment, computed once here) ---
      "command":     "python -m <pkg>",             # literal invocation
      "role":        "pure-discovery"               # composes children only
                   | "discovery+cli"                # composes AND own argparse
                   | "leaf",                        # own argparse, no children
      "description": str | null,                    # parent's commands() blurb
                                                    # (null on top-level root)
      "subcommands": [                              # null if subcommands() not declared
        {                                           # else enriched argparse subparsers
          "name":        str,                       # argparse subparser name
          "command":     "python -m <pkg> <name>",  # literal invocation
          "description": str,                       # subcommands() blurb
        }, ...
      ] | null,
    }

This is the canonical CLI surface. It is the SINGLE source of enrichment
— `command` strings, short `role` labels, and parent-supplied
`description` fields are computed here so downstream consumers don't
reimplement them. Downstream is free to SUMMARIZE (drop fields, suppress
empties) for its own needs; downstream does NOT enrich.

# CLI

    python scripts/check_cli_commands.py <root.package> [<root.package> ...]
    python scripts/check_cli_commands.py --json <root.package>

Default output is the walked tree plus a violations summary. With
`--json`, emits the enriched tree (single dict for one root, list of
dicts for multiple) to stdout.

Exits 0 if clean, 1 if any violation is found.
"""

# This checker is deliberately one module. A package split was tried and reverted
# as over-reach: the length is the honest cost of a single file aggregating the
# whole CLI-surface audit, accepted rather than dissolved into ceremony.
# pylint: disable=too-many-lines

from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

# Default thread-pool size for parallel subprocess probes. Each probe is a
# cold `python -m <pkg> ...` spawn that spends most of its wall time blocked
# in the kernel, so we oversubscribe relative to CPU count. Capped at 32 to
# avoid FD-table pressure on large trees.
_DEFAULT_MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)

_LINE_RE = re.compile(r"^\s+python -m (?P<path>\S+)\s{2,}(?P<desc>.+?)\s*$")

_PATTERN_LABEL = {
    "pattern-1": "Pattern 1 (pure discovery)",
    "pattern-2": "Pattern 2 (discovery + own CLI)",
    "pattern-3": "Pattern 3 (leaf)",
    "unknown": "?",
}

# Short, role-describing pattern labels used in the agent-facing brief
# (the `--json` output). The raw `pattern-N` codes are internal to the
# audit machinery; agents reading the surface want labels that describe
# the role of the node, not arbitrary numbers.
_BRIEF_PATTERN = {
    "pattern-1": "pure-discovery",
    "pattern-2": "discovery+cli",
    "pattern-3": "leaf",
    "unknown": "unknown",
}

Node = dict[str, Any]


# ---------- discovery helpers -------------------------------------------- #


# Cache of `_import_main` results so a `__main__.py` that crashes at import
# time doesn't get its side effects replayed every time we ask about it. The
# structural walk is single-threaded, so no lock is needed. The cache lives
# for the life of the process — fine because the tool runs one-shot per
# invocation and each `audit_cli_tree` call sees a stable filesystem.
_IMPORT_MAIN_CACHE: dict[str, tuple[ModuleType | None, str | None]] = {}


def _import_main(pkg: str) -> tuple[ModuleType | None, str | None]:
    """Import `<pkg>.__main__` and report what happened.

    Returns `(module, error)`:

    - `(module, None)` — import succeeded.
    - `(None, None)` — no `__main__.py` file (clean `ModuleNotFoundError`).
    - `(None, "<TypeName>: <msg>")` — `__main__.py` exists but raised
      during import. Any non-`ModuleNotFoundError` exception is captured
      as a structural violation rather than killed the audit. This shows
      up when a `__main__.py` does real work at module scope (a separate
      §3 anti-pattern — code must live behind `main()`, guarded by
      `if __name__ == "__main__":`).
    """
    if pkg in _IMPORT_MAIN_CACHE:
        return _IMPORT_MAIN_CACHE[pkg]
    try:
        result: tuple[ModuleType | None, str | None] = (
            importlib.import_module(f"{pkg}.__main__"),
            None,
        )
    except ModuleNotFoundError:
        result = (None, None)
    # Importing an adopter's __main__ runs their code; any failure is data, not
    # a reason to crash the audit.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Anything else — ValueError, ImportError-from-sub-import, KeyError
        # in module-scope dict lookups, FileNotFoundError from reading config
        # at import, etc. Record and continue; the violation lands on this
        # node and recursion proceeds into siblings.
        result = (None, f"{type(exc).__name__}: {exc}")
    _IMPORT_MAIN_CACHE[pkg] = result
    return result


def _read_commands(mod: ModuleType) -> tuple[list[tuple[str, str]] | None, list[str]]:
    fn = getattr(mod, "commands", None)
    if fn is None:
        return None, ["missing `commands()` function in __main__.py"]
    try:
        result = fn()
    # Adopter-supplied callable; record any failure rather than abort the audit.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, [f"`commands()` raised {type(exc).__name__}: {exc}"]
    if not isinstance(result, list) or not all(
        isinstance(p, tuple) and len(p) == 2 and all(isinstance(s, str) for s in p)
        for p in result
    ):
        return None, [f"`commands()` must return list[tuple[str, str]]; got {result!r}"]
    bad_names = [n for n, _ in result if "." in n or not n]
    if bad_names:
        return result, [
            f"`commands()` entries must be direct child names, not dotted: {bad_names}"
        ]
    return result, []


def _stringify_default(value: Any) -> Any:
    """Make an argparse default JSON-serialisable. Plain scalars pass
    through; enum members, lists of enum members, and callables become
    `repr`-style strings."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_stringify_default(v) for v in value]
    return repr(value)


def _stringify_type(t: Any) -> str:
    """Best-effort name for an argparse `type` callable.
    Builtins (str/int/Path) get their __name__; custom callables and
    enum classes get __name__; everything else falls back to repr."""
    return getattr(t, "__name__", None) or repr(t)


# Auditing a parser means reading argparse's private action classes; there is no
# public surface for it.
# pylint: disable=protected-access
def _describe_action(action: argparse.Action) -> dict[str, Any]:
    """Render one argparse Action as a structured arg entry.

    Surface: `flags` (list, empty for positional), `dest`, optional
    `type`, `default`, `choices`, `required`, `help`, `action`, `nargs`.
    Fields are present only when non-default to keep the surface lean.
    """
    entry: dict[str, Any] = {"dest": action.dest}
    if action.option_strings:
        entry["flags"] = list(action.option_strings)
    else:
        entry["flags"] = []  # positional
    if action.help and action.help is not argparse.SUPPRESS:
        entry["help"] = action.help
    if action.required:
        entry["required"] = True
    if action.choices is not None:
        entry["choices"] = [str(c) for c in action.choices]
    if action.default is not None and action.default is not argparse.SUPPRESS:
        entry["default"] = _stringify_default(action.default)
    if action.type is not None:
        entry["type"] = _stringify_type(action.type)
    elif isinstance(action, argparse._StoreTrueAction):
        entry["type"] = "bool"
        entry["action"] = "store_true"
    elif isinstance(action, argparse._StoreFalseAction):
        entry["type"] = "bool"
        entry["action"] = "store_false"
    elif isinstance(action, argparse._CountAction):
        entry["type"] = "int"
        entry["action"] = "count"
    elif isinstance(action, argparse._AppendAction):
        entry["action"] = "append"
    if action.nargs not in (None, 0):
        entry["nargs"] = str(action.nargs)
    return entry


def _args_from_parser(parser_obj: argparse.ArgumentParser) -> list[dict[str, Any]]:
    """Walk parser._actions, returning structured entries for every
    non-help, non-subparser, non-SUPPRESSed action.

    Mutually exclusive groups (constructed via
    `parser.add_mutually_exclusive_group()`) are emitted as nested
    `{"oneOf": [...]}` entries — a recognized JSON-Schema/OpenAPI
    construct — at the position of the group's first member in argparse
    order. A `required: true` field is attached only when the group is
    argparse-required (exactly one must be passed); absence means "at
    most one." Non-mutex `_ArgumentGroup` (visual-grouping only)
    instances are NOT surfaced — they have no semantic meaning for an
    agent picking flags.
    """
    action_to_group: dict[int, argparse._MutuallyExclusiveGroup] = {}
    for group in parser_obj._mutually_exclusive_groups:
        for member in group._group_actions:
            action_to_group[id(member)] = group

    def _filter_member(a: argparse.Action) -> bool:
        return (
            not isinstance(a, argparse._HelpAction) and a.help is not argparse.SUPPRESS
        )

    out: list[dict[str, Any]] = []
    seen_groups: set[int] = set()
    for action in parser_obj._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            continue
        if action.help is argparse.SUPPRESS:
            continue

        group = action_to_group.get(id(action))
        if group is None:
            out.append(_describe_action(action))
            continue
        if id(group) in seen_groups:
            continue
        seen_groups.add(id(group))
        members = [
            _describe_action(a) for a in group._group_actions if _filter_member(a)
        ]
        if not members:
            continue
        if len(members) == 1:
            # Degenerate single-member group — flatten to a plain arg.
            out.append(members[0])
            continue
        entry: dict[str, Any] = {"oneOf": members}
        if group.required:
            entry["required"] = True
        out.append(entry)
    return out


def _introspect_parser(mod: ModuleType) -> tuple[
    list[dict[str, Any]] | None,
    dict[str, list[dict[str, Any]]] | None,
    list[str],
]:
    """Call the module's optional `parser()` factory and walk the result.

    Returns (top_level_args, subparser_args, violations). When `parser()`
    is not defined this is (None, None, []) — absence is not a violation
    in itself; this prototype is opportunistic. If `parser()` exists but
    raises or returns the wrong type, violations are populated.
    """
    fn = getattr(mod, "parser", None)
    if fn is None or not callable(fn):
        return None, None, []
    try:
        p = fn()
    # Adopter-supplied factory; record any failure rather than abort the audit.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, None, [f"`parser()` raised {type(exc).__name__}: {exc}"]
    if not isinstance(p, argparse.ArgumentParser):
        return (
            None,
            None,
            [f"`parser()` must return argparse.ArgumentParser; got {type(p).__name__}"],
        )
    top_args = _args_from_parser(p)
    sub_args: dict[str, list[dict[str, Any]]] = {}
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub_parser in action.choices.items():
                sub_args[name] = _args_from_parser(sub_parser)
    return top_args, sub_args, []


# pylint: enable=protected-access


def _read_subcommands(
    mod: ModuleType,
) -> tuple[list[tuple[str, str]] | None, list[str]]:
    """Read the optional `subcommands()` function (Pattern 2 + 3 only).

    Returns (entries, violations). `entries` is None when the function is
    absent (which is fine — absence means no argparse subparsers). A
    present-but-broken function yields violations.
    """
    fn = getattr(mod, "subcommands", None)
    if fn is None:
        return None, []
    if not callable(fn):
        return None, [f"`subcommands` is not callable; got {type(fn).__name__}"]
    try:
        result = fn()
    # Adopter-supplied callable; record any failure rather than abort the audit.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, [f"`subcommands()` raised {type(exc).__name__}: {exc}"]
    if not isinstance(result, list) or not all(
        isinstance(p, tuple) and len(p) == 2 and all(isinstance(s, str) for s in p)
        for p in result
    ):
        return None, [
            f"`subcommands()` must return list[tuple[str, str]]; got {result!r}"
        ]
    bad_names = [
        n for n, _ in result if not n or any(c in n for c in (".", " ", "\t", "/"))
    ]
    if bad_names:
        return result, [
            "`subcommands()` entries must be plain argparse names "
            f"(no dots, spaces, slashes): {bad_names}"
        ]
    return result, []


def _run(pkg: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", pkg, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _parse_listing(stdout: str) -> list[tuple[str, str]]:
    return [
        (m["path"], m["desc"])
        for line in stdout.splitlines()
        if (m := _LINE_RE.match(line))
    ]


# ---------- subprocess probes (parallelised) ----------------------------- #
#
# The audit's wall-clock cost is dominated by cold `python -m <pkg> ...`
# spawns: every node needs `--help`, every node with non-empty `commands()`
# needs a no-args listing probe, and every entry in `subcommands()` needs a
# `<sub> --help` probe. Serially that's O(N) cold interpreter starts.
#
# To overlap them we split the audit into two passes:
#
#   1. Structural walk (`_audit_package`) — imports, AST scans, classify,
#      orphan scan, child recursion. No `_run` calls. Wherever a
#      subprocess-derived violation would land, the walk drops a `_Probe`
#      sentinel into the node's `violations` list and registers the same
#      sentinel for the fan-out step.
#   2. Fan-out + resolve (`audit_cli_tree`) — every probe is submitted to a
#      shared `ThreadPoolExecutor` at once; results come back in any order;
#      a final tree walk replaces each `_Probe` with the actual violation
#      strings it produced.
#
# Doing the swap in-place inside `violations` preserves the per-node ordering
# of the original implementation (subcommand-help cluster → parser-introspect
# violations → AST-scan violations → `--help` violation → no-args cluster →
# orphan violations), so existing exact-equality tests against the audit
# tree are unaffected.


class _Probe:
    """Placeholder for a subprocess-derived violation cluster.

    Inserted in a node's `violations` list during the structural walk; the
    fan-out step swaps it for zero or more violation strings produced from
    the resolved subprocess result. Identity (not value) is what matters —
    each instance maps 1:1 to one probe.
    """

    __slots__ = ("kind", "pkg", "extra", "future")

    def __init__(self, kind: str, pkg: str, extra: Any = None) -> None:
        self.kind = kind
        self.pkg = pkg
        self.extra = extra
        self.future: Any = None  # set by audit_cli_tree before resolution

    def args(self) -> tuple[str, ...]:
        """Positional arguments for `_run` — i.e. CLI args after the package."""
        if self.kind == "help":
            return ("--help",)
        if self.kind == "noargs":
            return ()
        if self.kind == "sub_help":
            return (self.extra, "--help")
        raise AssertionError(f"unknown probe kind {self.kind!r}")


def _violations_from_help(
    pkg: str, result: subprocess.CompletedProcess[str]
) -> list[str]:
    if result.returncode != 0:
        return [
            f"`python -m {pkg} --help` exited {result.returncode}; "
            f"stderr: {result.stderr.strip()[:200]}"
        ]
    return []


def _violations_from_sub_help(
    pkg: str, sub_name: str, result: subprocess.CompletedProcess[str]
) -> list[str]:
    if result.returncode != 0:
        return [
            f"`subcommands()` lists `{sub_name}` but "
            f"`python -m {pkg} {sub_name} --help` exited "
            f"{result.returncode}; stderr: {result.stderr.strip()[:200]}"
        ]
    return []


def _violations_from_noargs(
    pkg: str,
    commands: list[tuple[str, str]],
    noargs: subprocess.CompletedProcess[str],
) -> list[str]:
    out: list[str] = []
    if noargs.returncode != 0:
        out.append(
            f"`python -m {pkg}` (no args) exited {noargs.returncode}; "
            f"stderr: {noargs.stderr.strip()[:200]}"
        )
        return out
    if not noargs.stdout.strip():
        out.append(f"`python -m {pkg}` (no args) exited 0 but produced no stdout")
        if noargs.stderr.strip():
            out.append(
                "  (stderr was non-empty — listing may be going to the wrong stream)"
            )
        return out
    printed = _parse_listing(noargs.stdout)
    expected = [(f"{pkg}.{name}", desc) for name, desc in commands]
    printed_set = {p for p, _ in printed}
    expected_set = {p for p, _ in expected}
    for path, _desc in expected:
        if path not in printed_set:
            out.append(
                f"`commands()` claims `{path}` but no-args output does not list it"
            )
    for path, _desc in printed:
        if path not in expected_set:
            out.append(
                f"no-args output lists `{path}` but `commands()` does not claim it"
            )
    printed_desc = dict(printed)
    for path, desc in expected:
        if path in printed_desc and printed_desc[path] != desc:
            out.append(
                f"description mismatch for `{path}`: "
                f"commands()={desc!r}, printed={printed_desc[path]!r}"
            )
    return out


def _violations_from_probe(probe: _Probe) -> list[str]:
    result = probe.future.result()
    if probe.kind == "help":
        return _violations_from_help(probe.pkg, result)
    if probe.kind == "sub_help":
        return _violations_from_sub_help(probe.pkg, probe.extra, result)
    if probe.kind == "noargs":
        return _violations_from_noargs(probe.pkg, probe.extra, result)
    raise AssertionError(f"unknown probe kind {probe.kind!r}")


def _collect_probes(node: Node) -> list[_Probe]:
    """Walk the tree gathering every `_Probe` placeholder, pre-order."""
    probes: list[_Probe] = []
    for v in node["violations"]:
        if isinstance(v, _Probe):
            probes.append(v)
    for child in node["children"]:
        probes.extend(_collect_probes(child))
    return probes


def _resolve_probes(node: Node) -> None:
    """Replace every `_Probe` in this subtree's violations with the
    violation strings it produced. Runs after futures are settled."""
    resolved: list[str] = []
    for v in node["violations"]:
        if isinstance(v, _Probe):
            resolved.extend(_violations_from_probe(v))
        else:
            resolved.append(v)
    node["violations"] = resolved
    for child in node["children"]:
        _resolve_probes(child)


def _classify(
    mod: ModuleType, commands: list[tuple[str, str]]
) -> tuple[str, list[str]]:
    has_main = callable(getattr(mod, "main", None))
    has_print = callable(getattr(mod, "_print_commands", None))
    violations: list[str] = []
    if commands:
        pattern = "pattern-2" if has_main else "pattern-1"
        if not has_print:
            violations.append(
                f"{_PATTERN_LABEL[pattern]}: missing `_print_commands()` "
                "(every intermediate node owns its print layer)"
            )
    else:
        pattern = "pattern-3"
        if not has_main:
            violations.append(
                "Pattern 3 (leaf): missing `main()` — argparse entry point required"
            )
    return pattern, violations


def _has_main_guard(source: str) -> bool:
    return (
        'if __name__ == "__main__"' in source or "if __name__ == '__main__'" in source
    )


class _ArgparseScan(NamedTuple):
    """Result of statically scanning a __main__.py source for argparse usage."""

    uses_argument_parser: bool
    """True iff the AST contains any `argparse.ArgumentParser(...)` or bare
    `ArgumentParser(...)` constructor call. The signal that this leaf
    constructs a parser at all, which the spec requires `parser()` to
    expose for introspection."""

    uses_subparsers: bool
    """True iff the AST contains any `add_subparsers(...)` call. The signal
    that the leaf has structural subcommands which the spec requires
    `subcommands()` to declare."""

    literal_names: set[str]
    """Every `add_parser("<name>", ...)` first-arg string literal found.
    Cross-checked against `subcommands()` to detect missing entries."""

    dynamic_markers: set[str]
    """Every `add_parser(...)` call whose first argument is not a string
    literal (a variable, an f-string, etc.). Non-empty means the static
    check can't enumerate every subparser; surfaced as a soft warning."""


def _scan_argparse_source(module_file: str) -> _ArgparseScan:
    """Statically scan a __main__.py source for argparse usage.

    The scan parses only the leaf's own source. Names declared via helper
    functions, imported registries, or dynamic loops are out of reach —
    they're also out of reach for static declaration in `subcommands()`,
    so the limitation is symmetric.
    """
    empty = _ArgparseScan(False, False, set(), set())
    try:
        source = Path(module_file).read_text(encoding="utf-8")
    except OSError:
        return empty
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return empty

    uses_argument_parser = False
    uses_subparsers = False
    literal_names: set[str] = set()
    dynamic_markers: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match `argparse.ArgumentParser(...)` and bare `ArgumentParser(...)`.
        if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
            uses_argument_parser = True
            continue
        if isinstance(func, ast.Name) and func.id == "ArgumentParser":
            uses_argument_parser = True
            continue
        # Match `<something>.add_subparsers(...)` and bare `add_subparsers(...)`.
        if isinstance(func, ast.Attribute) and func.attr == "add_subparsers":
            uses_subparsers = True
            continue
        if isinstance(func, ast.Name) and func.id == "add_subparsers":
            uses_subparsers = True
            continue
        # Match `<something>.add_parser("name", ...)`.
        is_add_parser = (
            isinstance(func, ast.Attribute) and func.attr == "add_parser"
        ) or (isinstance(func, ast.Name) and func.id == "add_parser")
        if not is_add_parser or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            literal_names.add(first.value)
        else:
            try:
                dynamic_markers.add(ast.unparse(first))
            # Parsing arbitrary adopter source; unparse failure must not abort.
            except Exception:  # pylint: disable=broad-exception-caught
                dynamic_markers.add("<dynamic>")

    return _ArgparseScan(
        uses_argument_parser=uses_argument_parser,
        uses_subparsers=uses_subparsers,
        literal_names=literal_names,
        dynamic_markers=dynamic_markers,
    )


def _list_direct_subpackages(pkg: str) -> list[str]:
    """All direct-child sub-packages (dirs with __init__.py), regardless of
    whether they have __main__.py. Alphabetical."""
    try:
        mod = importlib.import_module(pkg)
    except ModuleNotFoundError:
        return []
    paths = getattr(mod, "__path__", None)
    if not paths:
        return []
    names: list[str] = []
    seen_names: set[str] = set()
    for base in paths:
        base_path = Path(base)
        if not base_path.is_dir():
            continue
        for entry in sorted(base_path.iterdir()):
            if entry.name.startswith((".", "_")):
                continue
            if (
                entry.is_dir()
                and (entry / "__init__.py").is_file()
                and entry.name not in seen_names
            ):
                seen_names.add(entry.name)
                names.append(entry.name)
    return names


def _scan_disk_children(pkg: str) -> tuple[list[str], list[str]]:
    """Return (sub_packages_with_main_py, modules_with_main_guard) as direct-child names."""
    try:
        mod = importlib.import_module(pkg)
    except ModuleNotFoundError:
        return [], []
    paths = getattr(mod, "__path__", None)
    if not paths:
        return [], []
    sub_packages: list[str] = []
    modules: list[str] = []
    seen_names: set[str] = set()
    for base in paths:
        base_path = Path(base)
        if not base_path.is_dir():
            continue
        for entry in sorted(base_path.iterdir()):
            if entry.name.startswith((".", "_")):
                continue
            if entry.is_dir() and (entry / "__init__.py").is_file():
                name = entry.name
                if name in seen_names:
                    continue
                seen_names.add(name)
                if (entry / "__main__.py").is_file():
                    sub_packages.append(name)
            elif (
                entry.is_file()
                and entry.suffix == ".py"
                and entry.name != "__main__.py"
            ):
                name = entry.stem
                if name in seen_names:
                    continue
                try:
                    source = entry.read_text(encoding="utf-8")
                except OSError:
                    continue
                if _has_main_guard(source):
                    seen_names.add(name)
                    modules.append(name)
    return sub_packages, modules


def _make_node(pkg: str, *, orphan: bool) -> Node:
    return {
        "pkg": pkg,
        "kind": "missing",
        "pattern": "unknown",
        "commands": None,
        "subcommands": None,
        "args": None,
        "orphan": orphan,
        "violations": [],
        "children": [],
    }


def _descend_broken(node: Node, pkg: str, seen: set[str]) -> None:
    """Recover traversal when a node is broken (no __main__.py or no valid
    commands()). Every direct-child sub-package on disk is audited so hidden
    CLI entries further down still become visible."""
    for name in _list_direct_subpackages(pkg):
        child_pkg = f"{pkg}.{name}"
        node["children"].append(_audit_package(child_pkg, orphan=False, seen=seen))
    _, disk_modules = _scan_disk_children(pkg)
    for name in disk_modules:
        child_pkg = f"{pkg}.{name}"
        orphan_node = _make_node(child_pkg, orphan=True)
        orphan_node["kind"] = "module"
        orphan_node["pattern"] = "pattern-3"
        node["violations"].append(
            f"§3 orphan runnable module: `{child_pkg}` has main guard but "
            f"parent `{pkg}` cannot register it (no valid commands())"
        )
        node["children"].append(orphan_node)


# A single deterministic per-node audit; its linear shape is clearest unfactored.
# pylint: disable=too-many-locals,too-many-statements
def _audit_package(pkg: str, *, orphan: bool, seen: set[str]) -> Node:
    """Audit one package node and recurse into its children. Returns a Node dict."""
    node = _make_node(pkg, orphan=orphan)

    if pkg in seen:
        node["violations"].append("already visited — cycle in CLI tree")
        return node
    seen.add(pkg)

    mod, import_err = _import_main(pkg)
    if mod is None:
        if import_err is None:
            node["violations"].append(
                "no __main__.py (not importable as `python -m ...`)"
            )
        else:
            node["violations"].append(
                f"__main__.py exists but raised on import: {import_err} "
                "(move work behind `main()` — module scope should be import-safe)"
            )
        _descend_broken(node, pkg, seen)
        return node
    node["kind"] = "package"

    commands, cmd_violations = _read_commands(mod)
    node["violations"].extend(cmd_violations)
    if commands is None:
        _descend_broken(node, pkg, seen)
        return node
    node["commands"] = [[name, desc] for name, desc in commands]

    pattern, classify_violations = _classify(mod, commands)
    node["pattern"] = pattern
    node["violations"].extend(classify_violations)

    # Optional argparse-subparser declaration (Pattern 2 + Pattern 3 only).
    subcommands, subcmd_violations = _read_subcommands(mod)
    node["violations"].extend(subcmd_violations)
    if subcommands is not None:
        if pattern == "pattern-1":
            node["violations"].append(
                "`subcommands()` declared on Pattern 1 node — argparse subparsers "
                "require `main()`; subcommands() is for Patterns 2 + 3 only"
            )
        node["subcommands"] = [[name, desc] for name, desc in subcommands]
        for sub_name, _sub_desc in subcommands:
            node["violations"].append(_Probe("sub_help", pkg, sub_name))

    # Opportunistic argparse-parser introspection (prototype). When the
    # leaf factors its parser construction into a `parser()` factory, walk
    # the constructed ArgumentParser and emit a structured `args` view on
    # the node + per-subparser. Authors that haven't refactored to expose
    # parser() see no `args` field — no violation; this is currently an
    # opt-in capability while the surface is being prototyped.
    top_args, sub_args, parser_violations = _introspect_parser(mod)
    node["violations"].extend(parser_violations)
    if top_args is not None:
        node["args"] = top_args
    if sub_args:
        # Stashed for _enrich to fold into the enriched subcommands list.
        # The underscore prefix marks it as enrichment-internal; _enrich pops it.
        node["_subparser_args"] = sub_args

    # No-silent-omission enforcement. The spec binds two contracts that
    # have to stay in sync with the actual argparse code, and both are
    # detectable from the __main__.py AST:
    #
    #   1. Any Pattern 2/3 node that constructs `argparse.ArgumentParser(...)`
    #      MUST expose a `parser()` factory — otherwise the audit can't
    #      introspect the argument surface and the agent reads only
    #      command/description, blind to flags.
    #   2. Any Pattern 2/3 node that calls `add_subparsers(...)` MUST
    #      declare `subcommands()` with one entry per `add_parser(<name>, ...)`
    #      literal. Missing the function entirely or omitting a literal
    #      both leave the agent's view of the subparser surface impoverished.
    #
    # Dynamic `add_parser(<expr>, ...)` calls (variables, loops, f-strings)
    # surface as a soft warning — neither subcommands() nor the audit can
    # statically enumerate them; the limitation is symmetric.
    mod_file = getattr(mod, "__file__", None)
    if mod_file and pattern in ("pattern-2", "pattern-3"):
        scan = _scan_argparse_source(mod_file)

        if scan.uses_argument_parser and top_args is None:
            # parser() factory missing (or returned the wrong type — that
            # error is already in parser_violations above). When the
            # introspection succeeded, top_args is a list (possibly empty),
            # not None.
            if not parser_violations:
                node["violations"].append(
                    "`main()` constructs `argparse.ArgumentParser(...)` but no "
                    "`parser()` factory is exposed — silent omission of the "
                    "parser-factory contract; the audit cannot introspect "
                    "flags/types/defaults. Factor parser construction into "
                    "`def parser() -> argparse.ArgumentParser` and have "
                    "`main()` call `parser().parse_args()`"
                )

        if scan.uses_subparsers:
            if subcommands is None:
                node["violations"].append(
                    "`main()` uses `argparse.add_subparsers()` but no "
                    "`subcommands()` declared — silent omission of the §3 "
                    "subcommand contract; declare one entry per "
                    "`add_parser(...)` name"
                )
            else:
                declared_names = {name for name, _ in subcommands}
                missing = sorted(scan.literal_names - declared_names)
                if missing:
                    node["violations"].append(
                        f"`subcommands()` is missing entries for `add_parser(...)` "
                        f"name(s) {missing} found in `main()` — silent omission "
                        "of part of the subcommand contract"
                    )
            if scan.dynamic_markers:
                node["violations"].append(
                    f"`add_parser(...)` called with non-literal name(s) "
                    f"{sorted(scan.dynamic_markers)} — static audit cannot verify "
                    "`subcommands()` covers these; refactor to literal names "
                    "so the contract is machine-checkable"
                )

    # Uniform: --help exits 0 everywhere.
    node["violations"].append(_Probe("help", pkg))

    if commands:
        # Carry a snapshot of the commands() entries on the probe; the
        # resolver needs them to diff the no-args listing.
        node["violations"].append(_Probe("noargs", pkg, list(commands)))

    # Registered children.
    registered = set()
    for name, _desc in commands:
        registered.add(name)
        child_pkg = f"{pkg}.{name}"
        child_mod, child_import_err = _import_main(child_pkg)
        if child_mod is not None or child_import_err is not None:
            # `__main__.py` exists (cleanly imported or raised on import).
            # Either way it's a package node; recurse so the audit logs the
            # import failure on the child rather than mis-classifying it as
            # a missing `__main__.py`. The cache prevents replaying the
            # crashing import a second time.
            node["children"].append(_audit_package(child_pkg, orphan=False, seen=seen))
            continue
        # No __main__.py. Is the child a package (missing __main__.py) or a
        # runnable .py module?
        try:
            child_mod = importlib.import_module(child_pkg)
        except ModuleNotFoundError:
            child_node = _make_node(child_pkg, orphan=False)
            child_node["violations"].append(
                f"`commands()` lists `{name}` but `{child_pkg}` is not importable"
            )
            node["children"].append(child_node)
            continue
        if hasattr(child_mod, "__path__"):
            # Package without __main__.py; _audit_package handles that case.
            node["children"].append(_audit_package(child_pkg, orphan=False, seen=seen))
            continue
        # .py module candidate — check for main guard.
        child_node = _make_node(child_pkg, orphan=False)
        source_file = getattr(child_mod, "__file__", None)
        if not source_file:
            child_node["violations"].append(
                f"`{child_pkg}` has no source file; cannot verify main guard"
            )
            node["children"].append(child_node)
            continue
        try:
            source = Path(source_file).read_text(encoding="utf-8")
        except OSError as exc:
            child_node["violations"].append(
                f"cannot read `{child_pkg}` source ({source_file}): {exc}"
            )
            node["children"].append(child_node)
            continue
        if not _has_main_guard(source):
            child_node["kind"] = "module"
            child_node["pattern"] = "pattern-3"
            child_node["violations"].append(
                f"`commands()` lists `{name}` but `{child_pkg}` has no "
                '`if __name__ == "__main__":` guard'
            )
            node["children"].append(child_node)
            continue
        child_node["kind"] = "module"
        child_node["pattern"] = "pattern-3"
        node["children"].append(child_node)

    # Orphan scan — direct-child CLI entries on disk not in commands().
    disk_subpkgs, disk_modules = _scan_disk_children(pkg)
    for name in disk_subpkgs:
        if name in registered:
            continue
        child_pkg = f"{pkg}.{name}"
        node["violations"].append(
            f"§3 orphan sub-package CLI: `{child_pkg}` has __main__.py "
            "but is not in parent's commands()"
        )
        node["children"].append(_audit_package(child_pkg, orphan=True, seen=seen))
    for name in disk_modules:
        if name in registered:
            continue
        child_pkg = f"{pkg}.{name}"
        orphan_node = _make_node(child_pkg, orphan=True)
        orphan_node["kind"] = "module"
        orphan_node["pattern"] = "pattern-3"
        node["violations"].append(
            f'§3 orphan runnable module: `{child_pkg}` has `if __name__ == "__main__":` '
            f"but is not in parent's commands()"
        )
        node["children"].append(orphan_node)

    return node


# ---------- public API --------------------------------------------------- #


def audit_cli_tree(root: str, *, max_workers: int | None = None) -> Node:
    """Walk the CLI tree rooted at `root` and return the enriched audit
    as a nested dict.

    Every node carries both the audit affordances (`pkg`, `kind`,
    `pattern`, `commands`, `orphan`, `violations`, `children`) and the
    resolved agent-facing fields (`command`, `role`, `description`).
    See `_enrich` and the module docstring for the schema. The returned
    Node is JSON-serialisable.

    `max_workers` caps concurrency for the subprocess fan-out; defaults
    to `_DEFAULT_MAX_WORKERS`. Pass `1` to force sequential probes (useful
    when debugging or reproducing pre-parallel behaviour).
    """
    tree = _audit_package(root, orphan=False, seen=set())
    probes = _collect_probes(tree)
    if probes:
        workers = max_workers if max_workers is not None else _DEFAULT_MAX_WORKERS
        # Cap at probe count — no point spinning up idle threads.
        workers = max(1, min(workers, len(probes)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for probe in probes:
                probe.future = ex.submit(_run, probe.pkg, *probe.args())
            # Context-manager exit blocks until every submitted task finishes,
            # so all futures are settled before we move on to resolution.
        _resolve_probes(tree)
    _enrich(tree, parent_descriptions=None)
    return tree


def collect_violations(node: Node) -> list[tuple[str, str]]:
    """Flatten a tree into a list of (pkg, message) pairs in pre-order."""
    out: list[tuple[str, str]] = [(node["pkg"], msg) for msg in node["violations"]]
    for child in node["children"]:
        out.extend(collect_violations(child))
    return out


def _enrich(node: Node, parent_descriptions: dict[str, str] | None) -> None:
    """In-place enrichment of an audit Node with agent-facing fields.

    Augments — never replaces — the raw audit fields. After enrichment,
    every node carries both the audit affordances (`pkg`, `kind`,
    `pattern`, `commands`, `subcommands`, `orphan`, `violations`,
    `children`) AND the resolved fields downstream consumers actually
    want to read:

      - `command`: the literal `python -m <pkg>` invocation.
      - `role`:    short pattern label — `pure-discovery`,
                   `discovery+cli`, or `leaf`.
      - `description`: the parent's `commands()` blurb for this child,
                       or `None` on the top-level root (no parent).
      - `subcommands` (when declared via `subcommands()`): rewritten from
                   raw `[name, desc]` tuples into enriched dicts with
                   `name`, `command` (`python -m <pkg> <name>`), and
                   `description`. Consumers reading the agent-facing
                   subparsers don't have to construct the invocation.

    This is the SINGLE place enrichment happens, by design. Downstream
    consumers (SessionStart hook, doc generators, CI) take the
    enriched tree and summarize for their use case; they do not
    re-derive these fields themselves.
    """
    pkg = node["pkg"]
    node["command"] = f"python -m {pkg}"
    node["role"] = _BRIEF_PATTERN.get(node.get("pattern", "unknown"), node["pattern"])
    leaf_name = pkg.rsplit(".", 1)[-1]
    node["description"] = (
        parent_descriptions.get(leaf_name) if parent_descriptions is not None else None
    )
    raw_subs = node.get("subcommands")
    if raw_subs:
        sub_args_map = node.pop("_subparser_args", None) or {}
        enriched_subs: list[dict[str, Any]] = []
        for name, desc in raw_subs:
            entry: dict[str, Any] = {
                "name": name,
                "command": f"python -m {pkg} {name}",
                "description": desc,
            }
            if name in sub_args_map:
                entry["args"] = sub_args_map[name]
            enriched_subs.append(entry)
        node["subcommands"] = enriched_subs
    else:
        # Drop the enrichment-internal field if it was set without raw_subs
        # (shouldn't happen in practice, but keep _enrich idempotent).
        node.pop("_subparser_args", None)
    own_descriptions = dict(node.get("commands") or [])
    for child in node.get("children") or []:
        _enrich(child, own_descriptions)


# ---------- rendering ---------------------------------------------------- #


def _node_status(node: Node) -> str:
    if node["violations"]:
        return f"FAIL ({len(node['violations'])})"
    return "OK"


def render_tree(node: Node, depth: int = 0) -> list[str]:
    """Render a tree as indented human-readable lines (list, so caller joins)."""
    indent = "  " * depth
    pattern_label = _PATTERN_LABEL.get(node["pattern"], node["pattern"])
    kind_suffix = ""
    if node["kind"] == "module":
        kind_suffix = " runnable module"
    if node["orphan"]:
        kind_suffix += " (orphan)"
    line = f"{indent}python -m {node['pkg']}   [{pattern_label}{kind_suffix}] {_node_status(node)}"
    lines = [line]
    for child in node["children"]:
        lines.extend(render_tree(child, depth + 1))
    return lines


# ---------- CLI ---------------------------------------------------------- #


def _resolve_root(arg: str) -> str:
    """Turn `src/gfem` or `./src/gfem` into `gfem`, with the enclosing directory
    added to sys.path so the package becomes importable. Dotted names pass
    through unchanged."""
    path = Path(arg)
    looks_like_path = ("/" in arg) or ("\\" in arg) or path.exists()
    if not looks_like_path:
        return arg
    abs_path = path.resolve()
    if not abs_path.is_dir():
        raise SystemExit(f"check_cli_commands: `{arg}` is not a directory")
    if not (abs_path / "__init__.py").is_file():
        raise SystemExit(
            f"check_cli_commands: `{arg}` has no __init__.py; not a Python package"
        )
    parent = str(abs_path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return abs_path.name


def main(argv: list[str]) -> int:
    """Parse argv, audit each root package, and return the process exit code."""
    parser = argparse.ArgumentParser(
        description="Audit the CLI tree rooted at one or more Python packages.",
    )
    parser.add_argument(
        "roots",
        nargs="+",
        help=(
            "Dotted package names (e.g. `gfem`) or filesystem paths "
            "to the package directory (e.g. `src/gfem`)."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the audit tree as JSON."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Max threads for the subprocess fan-out. "
            f"Defaults to {_DEFAULT_MAX_WORKERS} (min(32, cpus*4)). "
            "Use 1 to force sequential probes."
        ),
    )
    args = parser.parse_args(argv)

    roots = [_resolve_root(r) for r in args.roots]
    trees = [audit_cli_tree(root, max_workers=args.workers) for root in roots]
    all_violations: list[tuple[str, str]] = []
    for tree in trees:
        all_violations.extend(collect_violations(tree))

    if args.json:
        payload = trees[0] if len(trees) == 1 else trees
        print(json.dumps(payload, indent=2))
    else:
        for root, tree in zip(roots, trees):
            print(f"\n=== {root} ===")
            for line in render_tree(tree):
                print(line)
        if all_violations:
            print("\n--- violations ---")
            for pkg, msg in all_violations:
                print(f"{pkg}: {msg}")
        else:
            print("\nAll checks passed.")

    return 1 if all_violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
