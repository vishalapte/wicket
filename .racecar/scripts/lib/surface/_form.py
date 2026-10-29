"""The canonical cli form: its names, its template, how a verb's pieces are filled, and conformity.

What `racecar.surface` writes and what it recognises are defined here, once. `noun_state`
and `package_state` read conformity off the code's structure, and every verb of the
`surface` noun asks them rather than keeping its own idea of the form.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import keyword
import textwrap
from pathlib import Path
from typing import Any, NamedTuple

from lib.shared import _as_json
from lib.shared._root import package_dir, package_root
from lib.shared._templates import render_tree

from ._edit import (
    _calls,
    _defined,
    _funcs,
    _keys,
    _literal,
    _parser_verbs,
    _targets,
    _tree,
    add_block,
    add_entry,
    add_import,
    add_parser_block,
)
from ._error import SurfaceError

#: The delivered scripts directory: racecar's `scripts/`, or an adopter's `.racecar/scripts/`.
#: Resolved through any symlink, so racecar's own `.racecar/scripts/` links land in `scripts/`.
SCRIPTS = Path(__file__).resolve().parents[2]
#: The cli templates, beside the scripts they serve: racecar's `templates/cli/`,
#: delivered to an adopter as `.racecar/templates/cli/`.
TEMPLATE = SCRIPTS.parent / "templates" / "cli"
#: The error packet's modules, copied into a package's `lib/error/` byte for byte.
ERROR_SOURCE = SCRIPTS / "lib" / "shared" / "error"
# The vocabulary is param/surface.md's six (SURFACES.md §15); BUILT is what this noun builds.
# A face in the first and not the second is reported, never refused.
SURFACES = ("api", "bin", "cli", "mcp", "rest", "web")
BUILT = ("cli", "rest", "mcp")
_TYPES = {"integer": "int", "boolean": "bool", "path": "Path"}


def _lexicon(root: Path) -> Any:
    """The `lib.lexicon` package delivered beside this one: the one reader and writer of
    declarations.

    `root` is the repo acted on; the package is never looked up there.
    """
    del root
    return importlib.import_module("lib.lexicon")


def audit_tree(root: Path) -> dict[str, Any]:
    """The CLI audit tree of `root`'s package: `lib.lexicon`'s audit, the one route to
    it, with its refusal said as this package's own `SurfaceError`."""
    try:
        tree: dict[str, Any] = _lexicon(root).cli_tree(root)
    except _lexicon(root).LexiconError as err:
        raise SurfaceError(str(err)) from err
    return tree


def offered(root: Path, package: str) -> dict[str, set[str]]:
    """`{noun: verbs}` the cli offers, from the lexicon's CLI audit; the root is `package`.

    The audit imports the repo's code. A node it could not import is a refusal, never an
    empty answer.
    """
    lexicon = _lexicon(root)
    try:
        found = lexicon.implemented(root)
    except lexicon.LexiconError as err:
        raise SurfaceError(str(err)) from err
    return {noun or package: set(verbs) for noun, verbs in found.items()}


def package_of(root: Path) -> str:
    """The repo's package; refused, and said so, when there is none or several."""
    package = package_dir(root)
    if package is None:
        raise SurfaceError(
            f"{package_root(root)}: a cli is built into the repo's one package, and there "
            "is none, or several that [project].name does not settle; nothing was written"
        )
    return package.name


#: Every name Python binds before any import, which a generated parameter must not shadow.
_BUILTINS = frozenset(dir(builtins))


def _ident(word: str) -> str:
    """A lexicon word as a Python identifier: `dry-run` is `dry_run`, `from` is `from_`,
    `open` is `open_`. A keyword cannot be a name and a builtin must not be shadowed by
    one, so both take a trailing underscore."""
    name = word.replace("-", "_").replace(".", "_")
    return f"{name}_" if keyword.iskeyword(name) or name in _BUILTINS else name


def _word(word: str) -> str:
    """A lexicon word as the tail of an identifier (`_cmd_import`, `print_dry_run`): no
    keyword suffix, since a prefix already keeps it from being a keyword."""
    return word.replace("-", "_").replace(".", "_")


def _camel(word: str) -> str:
    return "".join(part.capitalize() for part in _word(word).split("_"))


def _code_text(value: object) -> str:
    """A summary as it can sit inside a double-quoted string and a docstring, on one line."""
    text = " ".join(str(value or "TODO").split())
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _quoted(text: str) -> str:
    """`text` (already escaped) as a string literal no line of which outgrows 88 columns.

    Long text becomes adjacent literals in parentheses, split between words, which black
    lays out and never joins; a split never lands inside an escape, since none holds a space.
    """
    chunks = textwrap.wrap(text, 60, break_long_words=False, break_on_hyphens=False)
    if len(chunks) < 2:
        return f'"{text}"'
    spaced = [f"{chunk} " for chunk in chunks[:-1]] + chunks[-1:]
    return "(" + " ".join(f'"{chunk}"' for chunk in spaced) + ")"


def _doc(text: str, indent: str = "") -> str:
    """`text` filled as docstring prose at `indent`, inside 88 columns."""
    return textwrap.fill(text, 88 - len(indent), subsequent_indent=indent)


def _fragment(name: str, subs: dict[str, str]) -> str:
    text = (TEMPLATE / "verb" / f"{name}.py").read_text(encoding="utf-8")
    for placeholder, value in subs.items():
        text = text.replace(placeholder, value)
    return text


def _drop_empty(text: str, placeholder: str, value: str) -> str:
    """`text` with the line holding `placeholder` filled with `value`, or removed if empty."""
    lines = []
    for line in text.splitlines(keepends=True):
        if placeholder in line:
            if value:
                lines.append(line.replace(placeholder, value))
            continue
        lines.append(line)
    return "".join(lines)


#: Params the form gives every verb; the one home is `lib.shared._as_json`.
FORM_PARAMS = _as_json.FORM_PARAMS


def _flag(verb_id: str, param: dict[str, Any], command: str) -> str:
    """One `add_argument` line for a declared param, from its node.

    Typed from `type:`; positional where the node's `position:` lists `command`, the verb as
    `<noun>.<verb>`; `required=True` where the node says `required: true`. A switch is never
    required, since passing it is the only thing it can mean.
    """
    name = str(param["name"])
    kind = _TYPES.get(str(param.get("type")), "str")
    if command in (param.get("position") or []):
        options = [f'"{_ident(name)}"']
        if _ident(name) != name:
            options.append(f'metavar="{name}"')
        if not param.get("required"):
            options.append('nargs="?"')
        if kind in ("int", "Path"):
            options.append(f"type={kind}")
        options.append(f"help={_quoted(_code_text(param.get('summary')))}")
        return f"p_{verb_id}.add_argument({', '.join(options)})"
    options = [f'"--{name}"']
    if _ident(name) != name.replace("-", "_"):
        options.append(f'dest="{_ident(name)}"')
    if kind == "bool":
        options.append('action="store_true"')
    elif kind in ("int", "Path"):
        options.append(f"type={kind}")
    if param.get("required") and kind != "bool":
        options.append("required=True")
    options.append(f"help={_quoted(_code_text(param.get('summary')))}")
    return f"p_{verb_id}.add_argument({', '.join(options)})"


def _signature(params: list[dict[str, Any]]) -> tuple[str, str]:
    """The api stub's parameters, and the call `_cmd_<verb>` makes with them."""
    parts, call = [], []
    for param in params:
        ident = _ident(str(param["name"]))
        kind = _TYPES.get(str(param.get("type")), "str")
        parts.append(
            f"{ident}: bool = False"
            if kind == "bool"
            else f"{ident}: {kind} | None = None"
        )
        call.append(f"{ident}=args.{ident}")
    return ", ".join(parts), ", ".join(call)


#: Where the root noun's code lives: its verbs run as `python -m <pkg> <verb>`, from the
#: package's own `__main__.py`, and their api and lib sit in this subpackage, as racecar's own
#: root verbs do. It is also the noun its spec rows are filed under: `root.<verb>`.
ROOT = "root"


class Noun:
    """Every name a noun's files are addressed by, derived from its dotted lexicon name.

    The root noun -- the package itself, `is_root` -- is addressed differently: its command
    line is the package's `__main__.py` and its code is `<pkg>/root/`. `main` is where a
    verb's parser and handler go and `cli` the module a command runs, for every noun.
    """

    def __init__(
        self, root: Path, package: str, noun: str, *, is_root: bool = False
    ) -> None:
        self.noun = noun
        self.is_root = is_root
        code = ROOT if is_root else noun
        self.last = code.rsplit(".", 1)[-1]
        self.ident = _word(code)
        self.module = f"{package}.{code}"
        home = package_root(root) / package
        self.dir = home / Path(*code.split("."))
        self.main = home / "__main__.py" if is_root else self.dir / "__main__.py"
        self.tests = root / "tests"
        self.test = self.tests / f"test_{self.ident}_cli.py"
        parent = noun.rsplit(".", 1)[0] if "." in noun and not is_root else None
        self.parent_main = (
            home / "__main__.py"
            if parent is None
            else home / Path(*parent.split(".")) / "__main__.py"
        )

    @property
    def cli(self) -> str:
        """The module a command runs: the package for the root, the noun's module otherwise."""
        return self.module.split(".", maxsplit=1)[0] if self.is_root else self.module

    @property
    def spec_noun(self) -> str:
        """The noun a spec row's id and group name: `root` for the root noun."""
        return ROOT if self.is_root else self.noun


def noun_of(root: Path, package: str, noun: str) -> Noun:
    """The `Noun` a lexicon noun names: the root flavour when it is the corpus's root noun."""
    lexicon = _lexicon(root)
    corpora = lexicon.lexicon_corpora(root)
    is_root = corpora.at("README.md", "own") is not None and noun == lexicon.root_noun(
        corpora
    )
    return Noun(root, package, noun, is_root=is_root)


def _render_noun(n: Noun, package: str, summary: str) -> tuple[list[Path], list[str]]:
    """A fresh noun's files and its test file, from `noun/` and `noun-tests/`.

    The root noun takes every file but `__main__.py` into `<pkg>/root/`, and the package's
    own `__main__.py` becomes the command line that runs its verbs (`_root_main`).
    """
    subs = {
        "__CLI__": n.cli,
        "__MODULE__": n.module,
        "__NOUN_ID__": n.ident,
        "__NOUN__": n.noun,
        "__DESCRIPTION__": _quoted(summary),
        "__SUMMARY__": _doc(summary),
        "__PKG__": package,
    }
    skip = frozenset({"__main__.py"}) if n.is_root else frozenset()
    written = render_tree(TEMPLATE / "noun", n.dir, subs, clobber=False, skip=skip)
    written += render_tree(TEMPLATE / "noun-tests", n.tests, subs, clobber=False)
    if n.is_root:
        written += _root_main(n, package, summary)
    return written, []


def _root_main(n: Noun, package: str, summary: str) -> list[Path]:
    """Make the package's `__main__.py` the root noun's command line, keeping its nouns.

    The package form's entry lists nouns and runs nothing; a root verb needs it to parse and
    dispatch, as a noun's does. It is rewritten from `root/__main__.py` only while it is
    still the package form's own, every `commands()` row carried across, so a hand-written
    entry is never overwritten.
    """
    main = _tree(n.main)
    if main is not None and _funcs(main, "subcommands") is not None:
        return []
    rows = _literal(main, "commands") if main is not None else None
    text = n.main.read_text(encoding="utf-8") if n.main.is_file() else ""
    kept = [
        (_keys(ast.List([row], ast.Load())), ast.get_source_segment(text, row) or "")
        for row in (rows.elts if isinstance(rows, ast.List) else [])
    ]
    written = render_tree(
        TEMPLATE / ROOT,
        n.main.parent,
        {
            "__PKG__": package,
            "__DESCRIPTION__": _quoted(summary),
            "__SUMMARY__": _doc(summary),
        },
    )
    for names, source in kept:
        for name in names:
            add_entry(n.main, "commands", name, source)
    return written


def _verb_pieces(n: Noun, verb: str, meta: dict[str, Any]) -> list[str]:
    """Insert every piece of one verb that is not there yet; one note per piece."""
    verb_id = _word(verb)
    fn = f"{verb_id}_{_word(n.last)}"
    result = f"{_camel(verb)}{_camel(n.last)}"
    declared = [p for p in meta["params"] if str(p["name"]) not in FORM_PARAMS]
    params, call = _signature(declared)
    command = f"{n.noun}.{verb}"
    flags = "\n".join(_flag(verb_id, p, command) for p in declared)
    summary = _code_text(meta.get("summary"))
    idents = [_ident(str(p["name"])) for p in declared]
    discard = (
        f"del {', '.join(idents)}  # a stub: the author uses these" if idents else ""
    )
    subs = {
        "__VERB_ID__": verb_id,
        "__VERB__": verb,
        "__FN__": fn,
        "__RESULT__": result,
        "__SUMMARY__": _doc(summary, "    "),
        "__PARAMS__": params,
        "__CALL__": call,
        "__NOUN__": n.noun,
    }
    parser_block = _fragment("parser", subs).replace(
        "__FLAGS__\n", f"{flags}\n" if flags else ""
    )
    results = n.dir / "lib" / "results.py"
    plaintext = n.dir / "lib" / "renderer" / "plaintext.py"
    main, api = n.main, n.dir / "api.py"
    # A `path` param is typed `Path` in the parser and in the api stub, so both files import
    # it; only where one is declared, so a noun with none carries no unused import.
    paths = [
        add_import(where, "pathlib", 0, "Path")
        for where in (main, api)
        if any(_TYPES.get(str(p.get("type"))) == "Path" for p in declared)
    ]
    return paths + [
        add_import(results, "typing", 0, "TypedDict"),
        add_block(results, result, _fragment("results", subs), None),
        add_import(plaintext, "results", 2, result),
        add_block(plaintext, f"print_{verb_id}", _fragment("plaintext", subs), "error"),
        add_import(api, "lib.results", 1, result),
        add_block(
            api,
            fn,
            _drop_empty(_fragment("api", subs), "__DISCARD__", discard),
            "VERBS",
        ),
        add_entry(api, "VERBS", verb, f'"{verb}": {fn}'),
        add_block(main, f"_cmd_{verb_id}", _fragment("cmd", subs), "subcommands"),
        add_entry(main, "subcommands", verb, f'("{verb}", {_quoted(summary)})'),
        add_parser_block(main, verb, parser_block),
        add_entry(n.test, "SAMPLES", verb, f'"{verb}": {{}}'),
    ]


# --- conformity: the one test of whether code is the canonical form, read from the code


_PACKAGE_FILES: dict[str, tuple[str, ...]] = {
    "__main__.py": ("commands",),
    "errors.py": ("ApiError",),
    "schema.py": ("returns", "conforms"),
    "lib/cli.py": ("NounParser", "VerbParser", "parse_args", "print_commands", "run"),
    "lib/renderer/json.py": ("add_flag", "print_json", "show"),
    "lib/error/__init__.py": (),
    "lib/error/_schema.py": ("SCHEMA",),
    "lib/error/_packet.py": ("ErrorPacket", "packet"),
}
#: The package files that are not rendered from the cli template, and where each comes from.
#: `_copy.py`, the stale-copy check, is not here: the program never uses it.
_PACKAGE_SOURCES: dict[str, Path] = {
    f"lib/error/{name}": ERROR_SOURCE / name
    for name in ("__init__.py", "_schema.py", "_packet.py")
}
_PACKAGE_TESTS: dict[str, tuple[str, ...]] = {
    "conftest.py": ("_run_cli",),
    "test_cli.py": (),
}
_NOUN_FILES: dict[str, tuple[str, ...]] = {
    "__init__.py": (),
    "__main__.py": (
        "commands",
        "_print_commands",
        "subcommands",
        "parser",
        "main",
        "output",
    ),
    "api.py": (),
    "lib/__init__.py": (),
    "lib/results.py": (),
    "lib/renderer/__init__.py": (),
    "lib/renderer/plaintext.py": ("error",),
}


def package_source(rel: str) -> Path:
    """The file a package's `rel` is written from: the cli template, or `lib/shared/error/`."""
    return _PACKAGE_SOURCES.get(rel, TEMPLATE / "package" / rel)


def copy_error_package(pkg: Path) -> list[Path]:
    """Write each `lib/error/` module `pkg` lacks, byte for byte; never overwrite one.

    Returns the paths written. The modules carry no placeholder, so a copy is the file.
    """
    written = []
    for rel, source in _PACKAGE_SOURCES.items():
        dest = pkg / rel
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        written.append(dest)
    return written


class Break(NamedTuple):
    """One way the code differs from the canonical form: a fixed kind, the file, and what.

    The kind is what a reader acts on, and it never changes wording: `upgrade`'s planner
    and the `/racecar-surface` skill both key on it. The detail is for a person, and may
    be reworded freely. `str()` gives `<path>: <detail>`.
    """

    kind: str
    path: Path
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: {self.detail}"


#: Every kind a `Break` can carry. Closed: a new way to differ is a new entry here, and the
#: skill's table gains the row for it in the same change.
BREAK_KINDS = frozenset(
    {
        "file-absent",
        "file-unparseable",
        "name-missing",
        "commands-not-literal",
        "not-literal",
        "verbs-not-from-subcommands",
        "no-flag-loop",
        "no-cmd",
        "no-print",
        "no-set-defaults",
        "main-no-argv",
        "verb-lists-disagree",
        "writes-output",
        "test-absent",
        "test-no-samples",
    }
)


def _file_breaks(
    path: Path, names: tuple[str, ...], absent: str = "file-absent"
) -> list[Break]:
    """What is wrong with one file the form requires: absent, unparseable, or a name missing."""
    if not path.is_file():
        return [Break(absent, path, "absent")]
    tree = _tree(path)
    if tree is None:
        return [Break("file-unparseable", path, "does not parse")]
    defined = _defined(tree) | {t.id for node in tree.body for t in _targets(node)}
    return [
        Break("name-missing", path, f"defines no `{name}`")
        for name in names
        if name not in defined
    ]


def package_state(root: Path, package: str) -> tuple[str, list[Break]]:
    """`("fresh", [])`, `("conforms", [])` or `("breaks", [each break])` for the package.

    Fresh means none of the files the form puts in the package or in `tests/` exists yet,
    so rendering them all is safe. Anything between fresh and conforming is somebody's
    code, and it is reported, never written to.
    """
    pkg = package_root(root) / package
    wanted = [(pkg / rel, names) for rel, names in _PACKAGE_FILES.items()]
    wanted += [(root / "tests" / rel, names) for rel, names in _PACKAGE_TESTS.items()]
    if not any(path.exists() for path, _ in wanted):
        return "fresh", []
    breaks = [b for path, names in wanted for b in _file_breaks(path, names)]
    main = _tree(pkg / "__main__.py")
    if main is not None and _literal(main, "commands") is None:
        breaks.append(
            Break(
                "commands-not-literal",
                pkg / "__main__.py",
                "commands() does not return a literal list",
            )
        )
    return ("breaks", breaks) if breaks else ("conforms", [])


def _uses_subcommands(call: ast.Call) -> bool:
    """True for `verbs(subcommands(), ...)`: the verbs are added from their one home."""
    first = call.args[0] if call.args else None
    return (
        isinstance(first, ast.Call)
        and isinstance(first.func, ast.Name)
        and first.func.id == "subcommands"
    )


def _parser_breaks(
    n: Noun, main: ast.Module, parser: ast.FunctionDef, plain: ast.Module | None
) -> list[Break]:
    """What `parser()` and the handlers and renderers it names get wrong."""
    main_path = n.main
    plain_path = n.dir / "lib" / "renderer" / "plaintext.py"
    breaks: list[Break] = []
    if not any(_uses_subcommands(c) for c in _calls(parser, "verbs")):
        breaks.append(
            Break(
                "verbs-not-from-subcommands",
                main_path,
                "parser() does not add its verbs via verbs(subcommands())",
            )
        )
    if not any(isinstance(s, ast.For) for s in parser.body):
        breaks.append(
            Break(
                "no-flag-loop",
                main_path,
                "parser() has no add_flag loop over its verbs",
            )
        )
    wired = {
        c.func.value.id
        for c in _calls(parser, "set_defaults")
        if isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name)
    }
    main_defs = _defined(main)
    plain_defs = _defined(plain) if plain is not None else set()
    for verb in sorted(_parser_verbs(parser)):
        verb_id = _word(verb)
        if f"_cmd_{verb_id}" not in main_defs:
            breaks.append(Break("no-cmd", main_path, f"no _cmd_{verb_id}"))
        if plain is not None and f"print_{verb_id}" not in plain_defs:
            breaks.append(Break("no-print", plain_path, f"no print_{verb_id}"))
        if f"p_{verb_id}" not in wired:
            breaks.append(
                Break(
                    "no-set-defaults",
                    main_path,
                    f"{verb!r} has no p_{verb_id}.set_defaults(func=...)",
                )
            )
    return breaks


def _writes_output(call: ast.Call) -> bool:
    """`print(...)`, or `sys.stdout.write(...)` / `sys.stderr.write(...)`, spelled so.

    It does not see `from sys import stdout` and
    `stdout.write`, `click.echo`, `rich`, a logging handler on stdout, or `os.write(1, ...)`.
    No `writes-output` finding means none of these three spellings, not that the noun
    writes nothing.
    """
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "write"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr in ("stdout", "stderr")
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "sys"
    )


def _output_breaks(n: Noun) -> list[Break]:
    """Every module of the noun, outside `lib/renderer/`, that writes output itself.

    The form says the renderers are the only code in a noun that writes output. A
    sub-noun (a directory below with its own `__main__.py`) is graded on its own.
    """
    renderer = n.dir / "lib" / "renderer"
    nested = {p.parent for p in n.dir.rglob("__main__.py") if p.parent != n.dir}
    breaks: list[Break] = []
    for path in sorted(n.dir.rglob("*.py")):
        if path.is_relative_to(renderer) or "__pycache__" in path.parts:
            continue
        if any(path.is_relative_to(sub) for sub in nested):
            continue
        tree = _tree(path)
        if tree is None:
            continue
        lines = sorted(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _writes_output(node)
        )
        if lines:
            shown = ", ".join(str(line) for line in lines[:8])
            more = f" and {len(lines) - 8} more" if len(lines) > 8 else ""
            breaks.append(
                Break(
                    "writes-output",
                    path,
                    f"writes output outside lib/renderer/ (line {shown}{more})",
                )
            )
    return breaks


def noun_state(n: Noun) -> tuple[str, list[Break]]:
    """`("fresh", [])`, `("conforms", [])` or `("breaks", [each break])` for one noun.

    Conforming is the canonical form read off the code, with nothing written into it to be
    recognised by: every file present, `VERBS` a literal, `subcommands()` a literal list,
    `parser()` adding its verbs through `verbs(subcommands())` and giving each one
    `set_defaults(func=_cmd_<verb>)`, `main(argv)`, one `_cmd_<verb>` and one
    `print_<verb>` per verb, the test's `SAMPLES` a literal, all four verb lists equal,
    and no module of the noun outside `lib/renderer/` writing output itself.
    """
    wanted = [
        (n.main if rel == "__main__.py" else n.dir / rel, names)
        for rel, names in _NOUN_FILES.items()
    ]
    # The root's entry is the package's `__main__.py`, which exists before any root verb
    # does, so only its own code says whether the root has been built.
    if not any(
        path.exists() for path, _ in wanted[:3] if path != n.main or not n.is_root
    ):
        return "fresh", []
    breaks = [b for path, names in wanted for b in _file_breaks(path, names)]
    breaks += _file_breaks(n.test, (), absent="test-absent")
    breaks += _output_breaks(n)
    main_path, api_path = n.main, n.dir / "api.py"
    main, api, test = _tree(main_path), _tree(api_path), _tree(n.test)
    plain = _tree(n.dir / "lib" / "renderer" / "plaintext.py")
    # Every check below runs on whichever files parse, so one missing file hides nothing.
    lists: dict[str, set[str]] = {}
    for label, tree, owner, path, kind in (
        ("VERBS", api, "VERBS", api_path, "not-literal"),
        ("subcommands()", main, "subcommands", main_path, "not-literal"),
        ("SAMPLES", test, "SAMPLES", n.test, "test-no-samples"),
    ):
        if tree is None:
            continue
        literal = _literal(tree, owner)
        if literal is None:
            breaks.append(Break(kind, path, f"{label} is not a literal"))
        else:
            lists[label] = _keys(literal)
    parser = _funcs(main, "parser") if main is not None else None
    entry = _funcs(main, "main") if main is not None else None
    if main is not None and parser is not None:
        lists["parser()"] = set(_parser_verbs(parser))
        breaks += _parser_breaks(n, main, parser, plain)
    if entry is not None and not entry.args.args:
        breaks.append(Break("main-no-argv", main_path, "main() takes no argv"))
    if len({frozenset(v) for v in lists.values()}) > 1:
        shown = "; ".join(f"{k} {sorted(v)}" for k, v in lists.items())
        breaks.append(
            Break("verb-lists-disagree", n.dir, f"the verb lists disagree: {shown}")
        )
    return ("breaks", breaks) if breaks else ("conforms", [])
