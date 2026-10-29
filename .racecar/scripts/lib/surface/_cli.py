"""The cli surface, built from the lexicon: render a noun, insert a verb, and say what changed.

`python -m racecar.surface create --surface cli --noun N [--verb V --param P ...]` lands
here. It creates N's lexicon entries (and V's, and its params') first, get-or-create,
through the delivered `lib.lexicon` package, then brings the code up to the declaration,
but only where that is safe:

- a package or noun with none of the form's files yet is FRESH: it is rendered whole from
  `templates/cli/` (`package/` and `tests/`, or `noun/` and `noun-tests/`);
- one that CONFORMS to the canonical form gains what it lacks: a row in its parent's
  `commands()`, and for each verb `VERBS` does not bind yet, one fragment from `verb/` per
  piece at a fixed position, a row in each of `subcommands()`, `VERBS` and the test's
  `SAMPLES`, and the imports its result type needs;
- one that exists and does not conform is somebody's code. Nothing is written to it; each
  way it differs is returned under `refused`, and the cli exits 1. Bringing it into line is
  `upgrade`'s work.

Every gate is read before anything is written, so a refused `create` writes nothing, the
lexicon included. A noun or verb the cli already offers (read through the lexicon's CLI
audit) is `present` and the call succeeds, even in code that does not conform.

Where the repo keeps a spec (`surface.jsonl`), each verb this call builds
or finds gets a row there if it has none: its function from `VERBS`, its params from that
function's signature, its record from the result type's fields, `layer` `tangible`, and
the `kind` the caller passes. A row that exists is never touched, and a repo with no spec
is not given one.

Whether a piece is already there is read from the code itself (the names a module
defines, the keys a literal holds, the verbs `parser()` adds), so a second run with the
same arguments changes nothing. A noun is checked again after it is written. The files
touched are run through the repo's own isort and black.

Params are stubs. A param becomes `--<name>`, typed from its param node (`integer` is
`int`, `boolean` is a switch, anything else a string), with the node's summary as its help;
the author decides positional, repeatable or defaulted. A flag that exists is never
rewritten.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from lib.shared import _spec
from lib.shared._root import package_root
from lib.shared._templates import format_python, render_tree

from ._edit import _funcs, _keys, _literal, _tree, add_entry
from ._error import SurfaceError
from ._form import (
    TEMPLATE,
    Noun,
    _code_text,
    _lexicon,
    _quoted,
    _render_noun,
    _verb_pieces,
    copy_error_package,
    noun_of,
    noun_state,
    offered,
    package_of,
    package_state,
)
from ._invocations import OWED, SEED, kinds, seed

#: What a spec row's `kind` may be (arch-python/SURFACES.md, "The spec").
KINDS = ("read", "write", "job")

#: The refusal's last line when a canonical file is missing: `upgrade` writes every one.
UPGRADE_HINT = (
    "a canonical file is missing; `python3 .racecar/scripts/surface.py upgrade "
    "--surface cli` writes it"
)


def create(
    root: Path,
    surface: str,
    noun: str,
    verb: str | None = None,
    params: list[str] | None = None,
    *,
    kind: str | None = None,
) -> dict[str, list[str]]:
    """Declare, then build: `{"declared", "notes", "refused"}`, each a list of lines.

    A refused call writes nothing. See the module docstring for fresh, conforming and
    non-conforming code.
    """
    if surface != "cli":
        raise SurfaceError(f"--surface {surface}: this module builds the cli face only")
    if kind is not None and kind not in KINDS:
        raise SurfaceError(f"--kind {kind}: a row's kind is one of {', '.join(KINDS)}")
    lexicon = _lexicon(root)
    package = package_of(root)
    result: dict[str, list[str]] = {"declared": [], "notes": [], "refused": []}
    breaks = _gates(root, package, noun)
    if breaks and not _present(root, package, noun, verb):
        # The hint is advice, not one more way the code differs, so it is not counted.
        result["refused"] = [line for line in breaks if line != UPGRADE_HINT]
        result["notes"] = [line for line in breaks if line == UPGRADE_HINT]
        return result
    n = noun_of(root, package, noun)
    # No `--kind` refusal: `declare` below writes each asked verb's row with `kind` null,
    # which is not yet known rather than guessed, and `--kind` fills it when given.
    spec = _spec.spec_path(root)
    try:
        result["declared"] = list(
            lexicon.declare(
                lexicon.lexicon_corpora(root), noun, verb, params or [], root=root
            )
        )
        # Built again after the write, so what is described includes what was declared.
        corpora = lexicon.lexicon_corpora(root)
        described = lexicon.describe(corpora, noun, verb)
    except lexicon.LexiconError as err:
        raise SurfaceError(str(err)) from err
    if breaks:
        result["notes"].append(
            f"present   {noun}{' ' + verb if verb else ''}: the cli offers it"
        )
        _rows(spec, n, list(described["verbs"]), kind, result)
        _seed(root, lexicon, noun, list(described["verbs"]), result)
        return result
    touched: list[Path] = []
    if package_state(root, package)[0] == "fresh":
        touched += render_tree(
            TEMPLATE / "package",
            package_root(root) / package,
            {"__PKG__": package},
            clobber=False,
        )
        touched += render_tree(
            TEMPLATE / "tests", root / "tests", {"__PKG__": package}, clobber=False
        )
        touched += copy_error_package(package_root(root) / package)
        result["notes"] += [f"wrote     {path}" for path in touched]
    parts = [noun] if n.is_root else noun.split(".")
    for depth in range(1, len(parts) + 1):
        _build_noun(
            noun_of(root, package, ".".join(parts[:depth])),
            package,
            lexicon=lexicon,
            corpora=corpora,
            result=result,
            touched=touched,
        )
    bound = _keys(
        _literal(_tree(n.dir / "api.py") or ast.Module([], []), "VERBS")
        or ast.Dict([], [])
    )
    for name, meta in described["verbs"].items():
        if name in bound:
            result["notes"].append(
                f"present   {n.dir / 'api.py'}: VERBS binds {name!r}"
            )
            continue
        lines = _verb_pieces(n, name, meta)
        result["notes"] += [line for line in lines if not line.startswith("unplaced")]
        result["refused"] += [line for line in lines if line.startswith("unplaced")]
        touched += [
            n.main,
            n.dir / "api.py",
            n.test,
            n.dir / "lib" / "results.py",
            n.dir / "lib" / "renderer" / "plaintext.py",
        ]
    _finish(root, result, touched, n)
    if not result["refused"]:
        _rows(spec, n, list(described["verbs"]), kind, result)
        _seed(root, lexicon, noun, list(described["verbs"]), result)
    return result


def _seed(
    root: Path,
    lexicon: Any,
    noun: str,
    verbs: list[str],
    result: dict[str, list[str]],
) -> None:
    """Give each recorded read or write verb the bare-verb command line, where it has none.

    A `job` verb gets none.
    """
    corpora = lexicon.lexicon_corpora(root)
    owed = {
        verb
        for (row_noun, verb), state in kinds(root, package_of(root)).items()
        if row_noun == noun and state in OWED
    }
    for verb in verbs:
        node = lexicon.verb_node(corpora, noun, verb)
        if verb in owed and seed(node):
            result["notes"].append(f"seeded    {node}: {SEED}")


def _gates(root: Path, package: str, noun: str) -> list[str]:
    """Why code may not be written, read before anything is: the package, then each noun."""
    state, breaks = package_state(root, package)
    if state == "breaks":
        lines = [f"package {package} does not conform: {b}" for b in breaks]
        if any(b.kind == "file-absent" for b in breaks):
            lines.append(UPGRADE_HINT)
        return lines
    parts = noun.split(".")
    for depth in range(1, len(parts) + 1):
        above = ".".join(parts[:depth])
        state, breaks = noun_state(noun_of(root, package, above))
        if state == "breaks":
            return [f"noun {above} does not conform: {b}" for b in breaks]
    return []


def _present(root: Path, package: str, noun: str, verb: str | None) -> bool:
    """Whether the cli already offers the noun, or the verb, read from its CLI audit."""
    found = offered(root, package)
    noun = package if noun_of(root, package, noun).is_root else noun
    return noun in found if verb is None else verb in found.get(noun, set())


def owed_rows(root: Path, n: Noun, verbs: list[str]) -> list[str]:
    """The spec rows building these verbs would owe; what `update` asks `--kind` for."""
    spec = _spec.spec_path(root)
    return _owed(spec, n, verbs, building=True)


def _owed(spec: Path, n: Noun, verbs: list[str], *, building: bool) -> list[str]:
    """The rows this call would write: absent from the spec, with a function to bind.

    A verb this call builds always has one; in code that does not conform, only a verb
    `VERBS` binds already does. A row `lexicon create` wrote, with kind null, is not owed.
    """
    if not spec.is_file():
        return []
    have = {str(row.get("id")) for row in _spec.read_rows(spec)}
    return [
        _spec.row_id(n.spec_noun, verb)
        for verb in verbs
        if _spec.row_id(n.spec_noun, verb) not in have
        and (building or _bound(n, verb) is not None)
    ]


def _rows(
    spec: Path,
    n: Noun,
    verbs: list[str],
    kind: str | None,
    result: dict[str, list[str]],
) -> None:
    """Fill each verb's spec row with what the cli face built: the function it binds, the
    command line, the function's params and record, and `exists`. The row's other fields
    are left as they are."""
    results = _tree(n.dir / "lib" / "results.py") or ast.Module([], [])
    for verb in verbs:
        row_id = _spec.row_id(n.spec_noun, verb)
        fn = _bound(n, verb)
        if fn is None:
            result["notes"].append(
                f"no row    {row_id}: VERBS in {n.dir / 'api.py'} binds no function "
                f"for {verb!r}"
            )
            continue
        fields = {
            "fn": f"{n.module}.api.{fn.name}",
            "cli": f"python -m {n.cli} {verb}",
            "params": [
                a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
            ],
            "record": _fields(results, fn.returns),
            "status": "exists",
        }
        # The kind comes from `--kind` where the row has none; `create` refused a verb that
        # needed one before anything was written.
        if kind is not None:
            fields["kind"] = kind
        if _spec.upsert_row(spec, row_id, n.spec_noun, fields):
            result["notes"].append(f"row       {spec}: {row_id}")


def _bound(n: Noun, verb: str) -> ast.FunctionDef | None:
    """The function `VERBS` in the noun's api binds `verb` to, where both are there."""
    api = _tree(n.dir / "api.py") or ast.Module([], [])
    bound = _literal(api, "VERBS")
    if not isinstance(bound, ast.Dict):
        return None
    for key, value in zip(bound.keys, bound.values):
        if (
            isinstance(key, ast.Constant)
            and key.value == verb
            and isinstance(value, ast.Name)
        ):
            return _funcs(api, value.id)
    return None


def _fields(results: ast.Module, returns: ast.expr | None) -> list[str]:
    """The field names of the result type a function returns, in declared order."""
    name = returns.id if isinstance(returns, ast.Name) else None
    for node in results.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return [
                s.target.id
                for s in node.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            ]
    return []


def _build_noun(
    n: Noun,
    package: str,
    *,
    lexicon: Any,
    corpora: Any,
    result: dict[str, list[str]],
    touched: list[Path],
) -> None:
    """Render the noun if it is fresh, and list it in its parent; the gates ran already."""
    summary = _code_text(lexicon.describe(corpora, n.noun)["summary"])
    if noun_state(n)[0] == "fresh":
        written, _ = _render_noun(n, package, summary)
        touched += written
        result["notes"] += [f"wrote     {path}" for path in written]
    if n.is_root:
        return  # the package's own entry; no parent lists it
    line = add_entry(
        n.parent_main, "commands", n.last, f'("{n.last}", {_quoted(summary)})'
    )
    (result["refused"] if line.startswith("unplaced") else result["notes"]).append(line)
    touched.append(n.parent_main)


def _finish(
    root: Path,
    result: dict[str, list[str]],
    touched: list[Path],
    n: Noun | None = None,
) -> dict[str, list[str]]:
    """Format what changed with the repo's own tools, then re-check the noun written to."""
    changed = sorted({p for p in touched if p.suffix == ".py" and p.is_file()})
    if any(not line.startswith("present") for line in result["notes"]):
        for tool in format_python(root, changed):
            result["notes"].append(f"skipped   {tool} is not installed; run `make fmt`")
    if n is not None:
        state, breaks = noun_state(n)
        if state == "breaks":
            result["refused"] += [
                f"noun {n.noun} left non-conforming: {b}" for b in breaks
            ]
    return result
