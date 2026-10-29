"""Grade the CLI tree against `surface.jsonl` — the WHAT, not the HOW.

`arch-python/SURFACES.md` §19 gives a governed package one spec file, one row per
capability, carrying its `cli`, `web`, `rest` and `mcp` bindings side by side.

    check_cli_commands   the HOW — every node has `__main__.py` + `commands()`, the
                         listing matches, the parser is built but not run at import
    check_surface        the WHAT — the verbs that exist are the verbs that were
                         specified, and the specified ones exist

Closure runs in both directions: every declared verb must exist, and every built verb
must be declared. When the two disagree, the tree is reported wrong; the spec is the
source.

It also grades the columns the generated server's rails read: a `kind` outside
`read | write | job`, a `rest` method missing or outside `GET | POST | DELETE`, a `GET` on
a write, a `DELETE` on a read, an exposed row with no `scope`, and a write whose scope tier
is not `write` or `admin`. `racecar.package`'s generator imports those rules from here.

Repos with no `surface.jsonl` are skipped, not failed.

Usage:
    python scripts/check_surface.py                 # discover src/<pkg>
    python scripts/check_surface.py --root <path>   # grade another repo
    python scripts/check_surface.py --json

The `cli` column and each row's `fn` need a package; with none (`server`, `django`,
`unknown`, or several packages with none named) they are skipped with a note ("No package
to check", or "Nothing to check" with no Django either). Every other rule reads the row
alone. With no row served on `rest` or `mcp` or declaring a scope, the scope rule says
"did not check, nothing to check", never OK.

Exit 0 when the spec and the tree agree (or no spec exists), 1 on any divergence,
2 on a usage error.

Complexity: O(n + m), n = surface.jsonl rows, m = CLI tree nodes walked
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from lib.shared import _spec
from lib.shared._python import repo_python
from lib.shared._root import not_present, package_root


class ConfigError(SystemExit):
    """The spec or the tree could not be read, so nothing was graded.

    A `SystemExit`, so the command line aborts with the message. Named, so a caller that
    composes checkers (`racecar check`) can catch it and record "could not grade".
    """


# The walker runs in a subprocess under the repo's own interpreter (`repo_python`), so the
# target's module-scope code never runs inside the checker.
#
# A leaf that declares no `subcommands()` is a verb leaf, and its own bare path is the
# invocation (`CLI.md` §"Noun or verb"). The discriminator is the function's absence, not an
# empty return.
_WALKER = r"""
import importlib, json, sys
pkg = sys.stdin.read()
found = []
queue = [(pkg, importlib.import_module(pkg + ".__main__"))]
while queue:
    path, node = queue.pop()
    for verb, _ in node.subcommands() if hasattr(node, "subcommands") else []:
        found.append("python -m %s %s" % (path, verb))
    children = list(node.commands()) if hasattr(node, "commands") else []
    if not hasattr(node, "subcommands") and not children:
        found.append("python -m %s" % path)
    for name, _ in children:
        child = path + "." + name
        queue.append((child, importlib.import_module(child + ".__main__")))
print(json.dumps(sorted(found)))
"""

# `absent` and `broken` are kept apart: a module that exists but raises on import is
# `broken`, never "not built yet".
_RESOLVER = r"""
import importlib, inspect, json, sys
out = {}
for fn in json.loads(sys.stdin.read()):
    module_name, _, attr = fn.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        absent = missing == module_name or module_name.startswith(missing + ".")
        out[fn] = {"state": "absent" if absent else "broken:%s" % exc}
    except Exception as exc:
        out[fn] = {"state": "broken:%s: %s" % (type(exc).__name__, exc)}
    else:
        # `fn` may name a MODULE (`pkg.api.package.commit`) rather than a callable on
        # one. `hasattr(parent, "commit")` is False for an unimported submodule and True
        # once anything imports it, so testing only the attribute makes the answer depend
        # on what ran first. Import the full path as a fallback and the answer is stable.
        if hasattr(module, attr):
            target = getattr(module, attr)
            try:
                # The api's SIGNATURE is what a surface binds, so it is the answer the spec's
                # `params` column is checked against. Read in the probe, not here: it is the
                # process that already imported the module, and the checker does not import
                # an arbitrary repo's code into itself.
                names = list(inspect.signature(target).parameters)
            except (TypeError, ValueError):
                names = None  # a module rather than a callable, or an unreadable signature
            out[fn] = {"state": "present", "params": names}
        else:
            try:
                importlib.import_module(fn)
            except ModuleNotFoundError:
                out[fn] = {"state": "absent"}
            except Exception as exc:
                out[fn] = {"state": "broken:%s: %s" % (type(exc).__name__, exc)}
            else:
                # A MODULE, not a callable: it has no signature to compare a row against.
                out[fn] = {"state": "present", "params": None}
print(json.dumps(out))
"""


def _params_finding(
    rel: str,
    row: dict[str, object],
    fn: str,
    signature: list[str] | None,
    status: object,
) -> list[str]:
    """Whether the row's `params` is the bound `fn`'s signature, as a finding or nothing.

    `params` is the api callable's parameter list, not one projection's flags; CLI flags
    live on the verb's node under `docs/lexicon/`. Silent when `fn` names a module or the
    row is `proposed`.
    """
    if signature is None or status == "proposed":
        return []
    listed = row.get("params")
    declared = set(listed) if isinstance(listed, list) else set()
    actual = set(signature)
    if declared == actual:
        return []
    missing = ", ".join(f"`{p}`" for p in sorted(actual - declared))
    extra = ", ".join(f"`{p}`" for p in sorted(declared - actual))
    detail = "; ".join(
        part
        for part in (
            (
                f"{fn} takes {missing} and the row does not declare them"
                if missing
                else ""
            ),
            (
                f"the row declares {extra} and {fn} takes no such parameter"
                if extra
                else ""
            ),
        )
        if part
    )
    return [
        f"{rel}: `{row['id']}`'s `params` is not `{fn}`'s signature — {detail}. "
        "A surface is one projection of the api, so this column is the api's parameters; "
        "a flag spelling belongs to the projection that spells it."
    ]


# The write rail's rules. `racecar.package`'s generator imports these three functions, so
# the generator and this checker refuse the same pairs.
KINDS = ("read", "write", "job")
METHODS = ("GET", "POST", "DELETE")
_WRITE_TIERS = ("write", "admin")


def kind_findings(ident: str, kind: object) -> list[str]:
    """A `kind` outside `read | write | job`, as a finding; the write rail has nothing to read."""
    if kind in KINDS:
        return []
    return [f"`{ident}` has kind {kind!r}; a kind is one of {', '.join(KINDS)}"]


def method_findings(ident: str, kind: object, method: object) -> list[str]:
    """A method that is missing, unknown, or contradicts HTTP for this `kind`.

    `POST` with `read` is allowed. `GET` with a write is not, because a GET is safe
    (RFC 9110 §9.2.1) and is served with the write rail off. `DELETE` with a read is not.
    """
    if method not in METHODS:
        return [
            f"`{ident}` has method {method!r}; a method is declared, never defaulted, "
            f"and is one of {', '.join(METHODS)}"
        ]
    if method == "GET" and kind in ("write", "job"):
        return [
            f"`{ident}` is a {kind} bound to GET; a GET is safe by definition, so the "
            "server would serve it with the write rail off"
        ]
    if method == "DELETE" and kind == "read":
        return [f"`{ident}` is a read bound to DELETE"]
    return []


def scope_findings(ident: str, kind: object, scope: object) -> list[str]:
    """A missing scope, or a write or job whose scope tier is not `write` or `admin`.

    The tier is the last `:` segment of `<repo>:<vertical>:<tier>` (AUTH.md), read, never
    computed.
    """
    if not scope:
        return [f"`{ident}` has no scope; a scope is declared, never derived"]
    tier = str(scope).rsplit(":", 1)[-1]
    if kind in ("write", "job") and tier not in _WRITE_TIERS:
        return [
            f"`{ident}` is a {kind} with scope `{scope}`; a {kind} needs a "
            f"{' or '.join(_WRITE_TIERS)} tier"
        ]
    return []


def rail_findings(row: dict[str, object]) -> list[str]:
    """The write-rail rules over one spec row: its kind, each `rest` method, its scope.

    A scope is required once the row is exposed (`rest` or `mcp` populated, §19); a declared
    scope is graded against the kind whether or not the row is exposed.
    """
    ident, kind = str(row["id"]), row.get("kind")
    rest = row.get("rest") or []
    if kind is None and not (rest or row.get("mcp")):
        # A null kind on an unexposed row is not yet known: `lexicon create` writes the
        # row, and the kind arrives with the face that serves it.
        return []
    found = kind_findings(ident, kind)
    for entry in rest if isinstance(rest, list) else []:
        method = entry.get("method") if isinstance(entry, dict) else None
        found += method_findings(ident, kind, method)
    if rest or row.get("mcp") or row.get("scope") is not None:
        found += scope_findings(ident, kind, row.get("scope"))
    return found


def _run(script: str, arg: str, root: Path) -> object:
    """Run one of the probe scripts against the target repo; return its parsed output.

    `arg` travels over the child's stdin, not argv, so a large spec cannot hit `ARG_MAX`.
    Raises `ConfigError` on a timeout, a non-zero exit, or unparseable stdout.
    """
    try:
        proc = subprocess.run(
            [repo_python(root), "-c", script],
            input=arg,
            capture_output=True,
            text=True,
            cwd=str(root),
            env=_env(),
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigError(
            "check_surface: a target module's import hung the probe past 120s "
            f"({exc})"
        ) from exc
    if proc.returncode != 0:
        raise ConfigError(
            f"check_surface: could not walk the CLI tree — {proc.stderr.strip()[:400]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        # Extra stdout came from the repo's own code at import, so nothing was graded.
        raise ConfigError(
            "check_surface: the probe's answer could not be read; something the repo "
            f"imports printed to stdout: {proc.stdout.strip()[:200]!r}"
        ) from exc


def _env() -> dict[str, str]:
    """The parent environment, imported here so the module top stays free of `os`."""
    import os  # pylint: disable=import-outside-toplevel

    return dict(os.environ)


# DFS colours for the `requires` cycle walk. GREY is "on the current path"; a node reached
# twice by different paths is BLACK the second time, which is a diamond, not a cycle.
_WHITE, _GREY, _BLACK = 0, 1, 2


def requires_findings(rows: list[dict[str, object]], rel: object) -> list[str]:
    """Check the `requires` graph: totality, then acyclicity.

    `requires` values are primary keys of this same table (SURFACES.md §19), so the rows
    form a directed graph. An absent key and an explicit `[]` both mean no prerequisites.
    `null` is refused: acyclicity cannot be decided over an unknown edge set. Each cycle is
    reported once, by the row that closes it.
    """
    findings: list[str] = []
    ids = {str(r["id"]) for r in rows}
    edges: dict[str, list[str]] = {}
    for row in rows:
        rid = str(row["id"])
        if "requires" not in row:
            edges[rid] = []
            continue
        value = row["requires"]
        if value is None:
            findings.append(
                f"{rel}: `{rid}` has `requires: null`. Omit the key, or write `[]` — "
                "an unknown edge set is not something acyclicity can be decided over."
            )
            edges[rid] = []
            continue
        if not isinstance(value, list):
            findings.append(f"{rel}: `{rid}` has a non-list `requires`")
            edges[rid] = []
            continue
        targets = [str(v) for v in value]
        for target in targets:
            if target not in ids:
                findings.append(
                    f"{rel}: `{rid}` requires `{target}`, which no row declares. A "
                    "prerequisite naming nothing is a runner that cannot proceed and "
                    "cannot say why."
                )
        edges[rid] = [t for t in targets if t in ids]

    # Iterative DFS with an explicit stack, so a deep chain is not a RecursionError.
    colour = dict.fromkeys(edges, _WHITE)
    for start in sorted(edges):
        if colour[start] != _WHITE:
            continue
        stack: list[tuple[str, list[str]]] = [(start, list(edges[start]))]
        colour[start] = _GREY
        path = [start]
        while stack:
            node, remaining = stack[-1]
            if not remaining:
                colour[node] = _BLACK
                stack.pop()
                path.pop()
                continue
            nxt = remaining.pop(0)
            if colour.get(nxt) == _GREY:
                loop = path[path.index(nxt) :] + [nxt]
                findings.append(
                    f"{rel}: `requires` cycle: {' -> '.join(loop)}. A prerequisite graph "
                    "with a cycle is a runner that never terminates."
                )
                continue
            if colour.get(nxt) == _BLACK:
                continue
            colour[nxt] = _GREY
            path.append(nxt)
            stack.append((nxt, list(edges[nxt])))
    return findings


def _cli_findings(
    root: Path, rel: Path, rows: list[dict[str, object]], pkg: str
) -> tuple[list[str], list[str]]:
    """`(findings, notes)` for the `cli` column: the spec's declared commands against the
    command tree the package's `__main__.py` builds.

    Two set differences: `declared - built - proposed`, then `built - declared`.
    `proposed` is the sanctioned lag between spec and tree.
    """
    # A library with no `__main__.py` has no tree to walk; nothing is built.
    has_cli = (package_root(root) / pkg / "__main__.py").is_file()
    walked: list[str] = (
        _run(_WALKER, pkg, root) if has_cli else []  # type: ignore[assignment]
    )
    built = set(walked)
    # `cli` is `str | null` and not required (§19), so a row binding no command line is
    # skipped rather than stringified to `"None"`.
    declared = {str(r["cli"]) for r in rows if r.get("cli")}
    # A row's `status` decides whether "declared and not built" is a finding at all.
    proposed = {
        str(r["cli"]) for r in rows if r.get("cli") and r.get("status") == "proposed"
    }
    findings: list[str] = []
    notes: list[str] = []

    # A `proposed` row not yet built is not a finding; only a row claiming to be built,
    # and not being, is.
    for verb in sorted(declared - built - proposed):
        findings.append(
            f"{rel}: `{verb}` is declared `exists` and does not. BUILD IT — the spec is "
            "the source; removing the row is not the available fix. (A verb genuinely "
            "not built yet belongs in the spec as `proposed`, which is not a finding.)"
        )
    pending = sorted(proposed - built)
    if pending:
        notes.append(
            f"{len(pending)} verb(s) declared and not yet built "
            f"(info, not a finding): {', '.join(pending)}"
        )
    # A built verb whose row still says `proposed` is a finding, as on the `fn` axis below.
    for verb in sorted(proposed & built):
        findings.append(
            f"{rel}: `{verb}` is marked `proposed` and now exists — the row needs its "
            "status flipped"
        )
    for verb in sorted(built - declared):
        findings.append(
            f"{rel}: `{verb}` exists and is not declared. Either remove the verb, or have "
            "the spec's owner declare it. Widening the spec to match is the same drift "
            "with the evidence erased."
        )
    return findings, notes


def audit(root: Path) -> tuple[list[str], int, list[str]]:
    """Return `(findings, rows graded, notes)`; no findings means the tree conforms.

    `notes` are informational lines, not findings, returned so `main` routes them: stdout
    beside the findings, or stderr under `--json`, where stdout is one JSON document. The
    `cli` column is graded by `_cli_findings` when there is a package; every other rule
    reads the rows alone.
    """
    located = _spec.find_spec(root)
    if located is None:
        return [], 0, ["no surface.jsonl — nothing to grade (info)"]
    spec, pkg = located
    try:
        rows = _spec.read_rows(spec)
    except _spec.SpecError as exc:
        raise ConfigError(f"check_surface: {exc}") from exc
    rel = spec.relative_to(root)
    findings: list[str] = []
    notes: list[str] = []
    if pkg is None:
        # Only the cli column needs a package; every other rule below reads the row alone.
        notes.append(f"{rel}: cli column: {not_present(root)}")
    else:
        findings, notes = _cli_findings(root, rel, rows, pkg)

    findings.extend(requires_findings(rows, rel))
    findings.extend(f"{rel}: {f}" for row in rows for f in rail_findings(row))
    if not any(row.get("rest") or row.get("mcp") or row.get("scope") for row in rows):
        # Scope is graded only on a row served on REST or MCP; with none, say so.
        notes.append(
            f"{rel}: did not check, nothing to check: no row is served on rest or mcp "
            "or declares a scope"
        )

    # A row with no `fn` names no callable yet (`null` until a face builds it).
    named = [r for r in rows if r.get("fn")]
    if named and pkg is None:
        # A row's `fn` is the package's code. With no package (a Django-only repo's
        # functions cannot be imported without starting Django), say which case this is.
        notes.append(
            f"{rel}: functions of {', '.join(str(r['id']) for r in named)}: "
            f"{not_present(root)}"
        )
        named = []
    resolved = (
        _run(_RESOLVER, json.dumps([str(r["fn"]) for r in named]), root)
        if named
        else {}
    )
    for row in named:
        fn, status = str(row["fn"]), row.get("status")
        answer = resolved[fn]  # type: ignore[index]
        state = str(answer["state"])
        if state.startswith("broken:"):
            findings.append(
                f"{rel}: `{fn}` exists and raised on import — {state[len('broken:'):]}. "
                "Neither `exists` nor `proposed` is true of a module that cannot load."
            )
        elif status == "proposed" and state == "present":
            findings.append(
                f"{rel}: `{fn}` is marked `proposed` and now exists — the row needs its "
                "status flipped"
            )
        elif status != "proposed" and state == "absent":
            findings.append(f"{rel}: `{fn}` is marked `{status}` and does not resolve")

        findings += _params_finding(str(rel), row, fn, answer.get("params"), status)

    return findings, len(rows), notes


def render(findings: list[str], graded: int, notes: list[str]) -> str:
    """The report for one audit: each note, each finding, then the tally.

    Notes come first and are never dropped, so a run that checked nothing never prints a
    bare OK.
    """
    lines = [f"check_surface: {note}" for note in notes]
    lines += [f"check_surface: {f}" for f in findings]
    if graded:
        lines.append(f"check_surface: {len(findings)} finding(s) over {graded} row(s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Parse argv, audit the repo, and return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=Path("."), help="Repo to grade (default: CWD)"
    )
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON")
    args = parser.parse_args(argv)

    findings, graded, notes = audit(args.root.resolve())
    if args.json:
        for note in notes:
            print(f"check_surface: {note}", file=sys.stderr)
        print(json.dumps({"findings": findings, "rows": graded}, indent=2))
    else:
        report = render(findings, graded, notes)
        if report:
            print(report)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
