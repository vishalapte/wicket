"""check, update, upgrade and list: the cli against what the lexicon declares.

One planner walks the package and every noun the lexicon declares with at least one verb,
and yields one `Item` per way the code differs from the canonical form or from the
declaration. Each item carries the finding (`check` prints it), the change that answers it
(`update` prints it), and, when that change is mechanical, the function that makes it
(`upgrade` calls it). A change with no function is JUDGMENT: moving code somebody wrote, or
deciding between the lexicon and the code. `upgrade` leaves those for a person or the
`/racecar-surface` skill, and `update` keeps listing them until they are done.

`upgrade` makes only changes that cannot break running code:

- write a missing package file (`errors.py`, `schema.py`, `lib/cli.py`,
  `lib/renderer/json.py`, the three `lib/error/` modules, the root `__main__.py`) only when
  nothing else in the package already defines what that file would. The `lib/error/`
  modules are copied from `scripts/lib/shared/error/`, not rendered from the template;
- write a noun's missing package markers and renderer, and its `lib/results.py` only when
  no module of the noun declares a TypedDict already;
- build a declared noun that has no code at all, and add a declared verb to a noun that
  conforms, both through `create`;
- add a built noun's row to its parent's `commands()` when that is a literal.

Everything is read from the code's structure, never from a mark left in it. `upgrade`
re-plans after each pass and stops when a pass changes nothing; a mechanical change that
survives its own application is reported rather than retried, so the loop cannot spin.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from lib.shared._root import package_root
from lib.shared._templates import format_python

from ._cli import create, owed_rows
from ._edit import _defined, _keys, _literal, _tree, add_entry
from ._error import SurfaceError
from ._form import (
    TEMPLATE,
    Break,
    Noun,
    _code_text,
    _doc,
    _lexicon,
    _quoted,
    noun_of,
    noun_state,
    package_of,
    package_source,
    package_state,
)
from ._invocations import KEY, absent

#: The package files `upgrade` may write, and the names whose presence elsewhere in the
#: package means the file's job is already done somewhere and moving it is judgment.
_PACKAGE_WRITES: dict[str, tuple[str, ...]] = {
    "errors.py": ("ApiError",),
    "schema.py": ("returns", "conforms"),
    "lib/cli.py": ("NounParser", "VerbParser", "parse_args"),
    "lib/renderer/json.py": ("add_flag", "print_json", "show"),
    "lib/error/__init__.py": (),
    "lib/error/_schema.py": ("SCHEMA",),
    "lib/error/_packet.py": ("ErrorPacket", "packet"),
}
#: A noun's files `upgrade` may write when absent; `__main__.py` and `api.py` never, since
#: a noun missing either is not this form with a gap but a different shape.
_NOUN_WRITES = (
    "__init__.py",
    "lib/__init__.py",
    "lib/renderer/__init__.py",
    "lib/renderer/plaintext.py",
    "lib/results.py",
)


class Applied(NamedTuple):
    """What one mechanical change did: its report lines, and the files it wrote.

    A change that formats its own output (`create` does) returns no paths, so nothing is
    formatted twice.
    """

    lines: list[str]
    paths: list[Path]
    refused: tuple[str, ...] = ()


class Item(NamedTuple):
    """One difference: its kind, what is wrong, what answers it, and how, when a machine can.

    `kind` is fixed vocabulary (`ITEM_KINDS`, or a `Break` kind carried through), and it is
    what the `/racecar-surface` skill's table is keyed on; `text` is for a person and may
    be reworded. An item with `apply` is one `upgrade` makes; one without is judgment.
    """

    kind: str
    finding: str
    text: str
    apply: Callable[[], Applied] | None

    @property
    def action(self) -> str:
        """`upgrade` when a machine makes the change, `judgment` when a person does."""
        return "upgrade" if self.apply is not None else "judgment"

    @property
    def change(self) -> str:
        """The line `update` prints: `<action> <kind>: <text>`."""
        return f"{self.action:<9} {self.kind}: {self.text}"

    def record(self) -> dict[str, str]:
        """The change as data: what `update --json` and `upgrade --json` emit."""
        return {"action": self.action, "kind": self.kind, "text": self.text}


#: The kinds an item can carry beyond a `Break`'s own. Closed, like `BREAK_KINDS`.
ITEM_KINDS = frozenset(
    {
        "write-file",
        "move-names",
        "move-results",
        "build-noun",
        "build-noun-waits",
        "build-verb",
        "build-verb-waits",
        "verb-undeclared",
        "kind-undeclared",
        "invocation-absent",
        "transcript-differs",
        "row-add",
        "row-waits",
        "row-not-literal",
    }
)


def _subs(root: Path, package: str, n: Noun | None = None) -> dict[str, str]:
    subs = {"__PKG__": package}
    if n is not None:
        lexicon = _lexicon(root)
        summary = _code_text(
            lexicon.describe(lexicon.lexicon_corpora(root), n.noun)["summary"]
        )
        subs = {
            "__CLI__": n.cli,
            "__MODULE__": n.module,
            "__NOUN_ID__": n.ident,
            "__NOUN__": n.noun,
            "__DESCRIPTION__": _quoted(summary),
            "__SUMMARY__": _doc(summary),
            "__PKG__": package,
        }
    return subs


def _write(template: Path, dest: Path, subs: dict[str, str]) -> Callable[[], Applied]:
    """A change that renders one template file to `dest`, unless `dest` exists by then."""

    def apply() -> Applied:
        if dest.exists():
            return Applied([f"present   {dest}"], [])
        text = template.read_text(encoding="utf-8")
        for placeholder, value in subs.items():
            text = text.replace(placeholder, value)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return Applied([f"wrote     {dest}"], [dest])

    return apply


def _home(
    directory: Path, names: tuple[str, ...], skip: Path
) -> tuple[Path, list[str]] | None:
    """The one module under `directory` that already does a file's job, if any.

    That is the module defining the most of `names` at module level, and it must define at
    least two of them when there are two to define.
    """
    best_path: Path | None = None
    best_hits: list[str] = []
    for path in sorted(directory.rglob("*.py")):
        if path == skip or "__pycache__" in path.parts:
            continue
        tree = _tree(path)
        if tree is None:
            continue
        hits = [name for name in names if name in _defined(tree)]
        if len(hits) > len(best_hits):
            best_path, best_hits = path, hits
    if best_path is None or len(best_hits) < min(2, len(names)):
        return None
    return best_path, best_hits


def _typed_dicts(directory: Path) -> list[Path]:
    """The modules under `directory` that declare a TypedDict class."""
    hits = []
    for path in sorted(directory.rglob("*.py")):
        tree = _tree(path)
        if tree is None:
            continue
        if any(
            isinstance(node, ast.ClassDef)
            and any(
                getattr(b, "id", getattr(b, "attr", "")) == "TypedDict"
                for b in node.bases
            )
            for node in tree.body
        ):
            hits.append(path)
    return hits


def _create(root: Path, noun: str, verb: str | None = None) -> Callable[[], Applied]:
    def apply() -> Applied:
        result = create(root, "cli", noun, verb)
        return Applied(result["notes"], [], tuple(result["refused"]))

    return apply


def _kind_text(noun: str, owed: list[str]) -> str:
    """What to ask the owner before a verb the spec has no row for is built."""
    return (
        f"the spec has no row for {', '.join(owed)}: whether each reads, writes or runs "
        f"a job is the owner's; then `create --surface cli --noun {noun} --verb V "
        "--kind K` builds it and writes the row"
    )


def _package_items(root: Path, package: str) -> list[Item]:
    state, breaks = package_state(root, package)
    pkg = package_root(root) / package
    if state != "breaks":
        return []
    items: list[Item] = []
    handled: set[Path] = set()
    for rel, names in _PACKAGE_WRITES.items():
        dest = pkg / rel
        if dest.exists():
            continue
        handled.add(dest)
        home = _home(pkg, names, dest)
        if home is not None:
            path, hits = home
            what = ", ".join(f"`{h}`" for h in hits)
            text = (
                f"move {what} from {path} to {dest}; leave the rest of {path.name} "
                "where it is"
            )
            items.append(Item("move-names", f"{dest}: absent", text, None))
        else:
            source = package_source(rel)
            write = _write(source, dest, {"__PKG__": package})
            origin = (
                "the template" if source.is_relative_to(TEMPLATE) else source.parent
            )
            text = f"write {dest} from {origin}"
            items.append(Item("write-file", f"{dest}: absent", text, write))
    main = pkg / "__main__.py"
    if not main.exists():
        handled.add(main)
        write = _write(TEMPLATE / "package" / "__main__.py", main, {"__PKG__": package})
        text = f"write {main} from the template"
        items.append(Item("write-file", f"{main}: absent", text, write))
    for brk in breaks:
        if brk.kind == "file-absent" and brk.path in handled:
            continue
        items.append(Item(brk.kind, str(brk), str(brk), None))
    return items


def _blocked(root: Path, package: str, name: str, package_ok: bool) -> str | None:
    """Why `create` would refuse to write into `name` now, or None when it would write.

    The same test `create` applies, asked before it runs, so `update` never lists as
    `upgrade` a change `create` is going to refuse: the package, then every noun above
    `name` from the top down, since `create` renders or checks each of them in that order.
    """
    if not package_ok:
        return f"package {package} does not conform"
    parts = name.split(".")
    for depth in range(1, len(parts)):
        above = ".".join(parts[:depth])
        if noun_state(noun_of(root, package, above))[0] == "breaks":
            return f"{above}, above it, does not conform"
    return None


def _judgment(n: Noun, brk: Break) -> Item:
    """The judgment item for one break, keyed on its kind, never on its wording.

    A test file that exists without a literal `SAMPLES` holds tests somebody wrote, so its
    line says to keep them; an absent one is written from the template.
    """
    template = "templates/cli/noun-tests/"
    plaintext = n.dir / "lib" / "renderer" / "plaintext.py"
    finding = f"noun {n.noun}: {brk}"
    if brk.kind == "test-absent":
        text = f"write {n.test} from {template}, with one real call per verb in SAMPLES"
    elif brk.kind == "test-no-samples":
        text = (
            f"keep every test in {n.test}; add a literal SAMPLES (one real call per "
            f"verb) and the two tests from {template} beside them"
        )
    elif brk.kind == "writes-output":
        text = f"{brk}: move each write into a print_<verb> (or error()) in {plaintext}"
    else:
        text = str(brk)
    return Item(brk.kind, finding, text, None)


def _broken_noun_items(
    root: Path, package: str, n: Noun, breaks: list[Break], wanted: list[str]
) -> list[Item]:
    """A noun that exists and does not conform: the files `upgrade` may add, the rest judgment."""
    name = n.noun
    items: list[Item] = []
    written: set[Path] = set()
    typed = _typed_dicts(n.dir)
    for rel in _NOUN_WRITES:
        dest = n.dir / rel
        if dest.exists():
            continue
        written.add(dest)
        if rel == "lib/results.py" and typed:
            where = ", ".join(str(p) for p in typed)
            text = f"move the result TypedDicts in {where} into {dest}"
            items.append(Item("move-results", f"{dest}: absent", text, None))
            continue
        write = _write(TEMPLATE / "noun" / rel, dest, _subs(root, package, n))
        text = f"write {dest} from the template"
        items.append(Item("write-file", f"{dest}: absent", text, write))
    for brk in breaks:
        if brk.kind == "file-absent" and brk.path in written:
            continue
        items.append(_judgment(n, brk))
    api = _tree(n.dir / "api.py")
    bound = _keys(_literal(api, "VERBS") or ast.Dict([], [])) if api else set()
    for v in wanted:
        if v not in bound:
            finding = f"noun {name}: verb {v!r} declared, not built"
            text = f"build {name} {v}, which waits: {name} does not conform"
            items.append(Item("build-verb-waits", finding, text, None))
    return items


def _noun_items(
    root: Path,
    package: str,
    name: str,
    meta: dict[str, Any],
    verb: str | None,
    *,
    package_ok: bool,
) -> list[Item]:
    n = noun_of(root, package, name)
    wanted = [v for v in meta["verbs"] if verb is None or v == verb]
    state, breaks = noun_state(n)
    if state == "fresh":
        why = _blocked(root, package, name, package_ok)
        finding = f"noun {name}: declared, not built"
        if why is not None:
            text = f"build noun {name}, which waits: {why}"
            return [Item("build-noun-waits", finding, text, None)]
        owed = owed_rows(root, n, sorted(meta["verbs"]))
        if owed:
            return [Item("kind-undeclared", finding, _kind_text(name, owed), None)]
        text = f"build noun {name} with verbs {', '.join(sorted(meta['verbs']))}"
        return [Item("build-noun", finding, text, _create(root, name))]
    if state == "breaks":
        return _broken_noun_items(root, package, n, breaks, wanted)
    items: list[Item] = []
    api = _tree(n.dir / "api.py")
    assert api is not None
    bound = _keys(_literal(api, "VERBS") or ast.Dict([], []))
    why = _blocked(root, package, name, package_ok)
    for v in wanted:
        if v in bound:
            continue
        finding = f"noun {name}: verb {v!r} declared, not built"
        if why is None and owed_rows(root, n, [v]):
            text = _kind_text(name, owed_rows(root, n, [v]))
            items.append(Item("kind-undeclared", finding, text, None))
        elif why is None:
            items.append(
                Item("build-verb", finding, f"build {name} {v}", _create(root, name, v))
            )
        else:
            text = f"build {name} {v}, which waits: {why}"
            items.append(Item("build-verb-waits", finding, text, None))
    if verb is None:
        for v in sorted(bound - set(meta["verbs"])):
            finding = (
                f"noun {name}: VERBS binds {v!r}, which the lexicon does not declare"
            )
            text = (
                f"declare {name} {v} (`lexicon create --noun {name} --verb {v}`), or "
                "remove it from the code"
            )
            items.append(Item("verb-undeclared", finding, text, None))
    summary = _code_text(meta.get("summary"))
    items += _row_items(n, summary, why)
    return items


def _row(n: Noun, summary: str) -> Callable[[], Applied]:
    """A change that lists the noun in its parent's `commands()`."""

    def apply() -> Applied:
        line = add_entry(
            n.parent_main, "commands", n.last, f'("{n.last}", {_quoted(summary)})'
        )
        return Applied([line], [n.parent_main] if line.startswith("added") else [])

    return apply


def _row_items(n: Noun, summary: str, why: str | None) -> list[Item]:
    """The noun's row in its parent's `commands()`, when the parent's list lacks it.

    None for the root: it is the package itself, so no parent lists it.
    """
    if n.is_root:
        return []
    parent = _tree(n.parent_main)
    literal = _literal(parent, "commands") if parent is not None else None
    if literal is not None and n.last in _keys(literal):
        return []
    where = f"{n.parent_main} commands()"
    if literal is None:
        finding = f"{n.parent_main}: does not list {n.last!r} in a literal commands()"
        return [Item("row-not-literal", finding, f"list {n.last!r} in {where}", None)]
    finding = f"{n.parent_main}: commands() does not list {n.last!r}"
    if why is not None:
        text = f"add {n.last!r} to {where}, which waits: {why}"
        return [Item("row-waits", finding, text, None)]
    return [Item("row-add", finding, f"add {n.last!r} to {where}", _row(n, summary))]


def declared(root: Path) -> dict[str, dict[str, Any]]:
    """Every noun the corpus declares with at least one verb, and what it declares.

    The root noun (the package itself) is keyed by its own name with `"root": True`.
    """
    lexicon = _lexicon(root)
    corpora = lexicon.lexicon_corpora(root)
    out: dict[str, dict[str, Any]] = {}
    for chain in sorted(
        lexicon.eligible_nouns(corpora, lexicon.eligible_domains(corpora))
    ):
        name = ".".join(chain) if chain else lexicon.root_noun(corpora)
        described = dict(lexicon.describe(corpora, name))
        if described["verbs"]:
            described["root"] = not chain
            out[name] = described
    return out


def plan(
    root: Path, surface: str, noun: str | None = None, verb: str | None = None
) -> list[Item]:
    """Every difference between the cli and the canonical form or the lexicon, in order."""
    if surface != "cli":
        raise SurfaceError(f"--surface {surface}: this module builds the cli face only")
    package = package_of(root)
    nouns = declared(root)
    if noun is not None and noun not in nouns:
        raise SurfaceError(f"{noun}: the lexicon declares no verb for it")
    state = package_state(root, package)[0]
    # A fresh package is not a difference by itself: building its first noun renders it, and
    # a package whose lexicon declares no noun with verbs owes no cli at all.
    items = [] if state == "fresh" else _package_items(root, package)
    package_ok = state != "breaks"
    for name, meta in nouns.items():
        if noun is not None and name != noun:
            continue
        items += _noun_items(root, package, name, meta, verb, package_ok=package_ok)
    return items + _invocation_items(root, noun, verb)


def _invocation_items(root: Path, noun: str | None, verb: str | None) -> list[Item]:
    """A built `read` or `write` verb whose lexicon node declares no command line to run.

    Judgment, never a mechanical change: the item offers a skeleton to prune.
    """
    return [
        Item(
            "invocation-absent",
            f"{gap['noun']} {gap['verb']}: a built verb with no command line declared to "
            f"run; add `{KEY}:` to {gap['node']}",
            f"declare `{KEY}:` in {gap['node']}; proposed from its parser, to edit and "
            f"prune: {gap['proposed']}",
            None,
        )
        for gap in absent(root, noun, verb)
    ]


def check(
    root: Path, surface: str, noun: str | None = None, verb: str | None = None
) -> list[dict[str, str]]:
    """What is wrong: `{"kind", "finding"}` per difference. Empty means the cli conforms."""
    return [
        {"kind": item.kind, "finding": item.finding}
        for item in plan(root, surface, noun, verb)
    ]


def update(
    root: Path, surface: str, noun: str | None = None, verb: str | None = None
) -> list[dict[str, str]]:
    """What `upgrade` would do and what it leaves: `{"action", "kind", "text"}` each."""
    return [item.record() for item in plan(root, surface, noun, verb)]


def upgrade(
    root: Path, surface: str, noun: str | None = None, verb: str | None = None
) -> dict[str, list[dict[str, str]]]:
    """Make every mechanical change, re-plan, and repeat until a pass changes nothing.

    Returns `{"changed", "remaining"}`. Each changed entry is `{"status", "detail"}`,
    `status` being the first word the editing primitive reported (`wrote`, `added`). Each
    remaining entry is an `update` record; one whose change ran and did not take carries
    `action: "refused"` and the reason in `why`.
    """
    changed: list[dict[str, str]] = []
    written: list[Path] = []
    attempted: dict[str, tuple[str, ...]] = {}
    while True:
        items = plan(root, surface, noun, verb)
        due = [i for i in items if i.apply is not None and i.change not in attempted]
        if not due:
            break
        for item in due:
            assert item.apply is not None
            done = item.apply()
            attempted[item.change] = done.refused
            for line in done.lines:
                status, _, detail = line.partition(" ")
                if status not in ("present", "refused"):
                    changed.append({"status": status, "detail": detail.strip()})
            written += done.paths
    format_python(
        root, sorted({p for p in written if p.suffix == ".py" and p.is_file()})
    )
    return {
        "changed": changed,
        "remaining": _remaining(plan(root, surface, noun, verb), attempted),
    }


def _remaining(
    items: list[Item], attempted: dict[str, tuple[str, ...]]
) -> list[dict[str, str]]:
    """What is left, with a mechanical change that ran and is still due marked refused."""
    out: list[dict[str, str]] = []
    for item in items:
        if item.change not in attempted:
            out.append(item.record())
            continue
        reasons = attempted[item.change] or (
            "it ran and the difference is still there",
        )
        for why in reasons:
            out.append({**item.record(), "action": "refused", "why": why})
    return out


def rows(root: Path, surface: str, noun: str | None = None) -> list[dict[str, str]]:
    """Each declared (noun, verb) and whether the cli binds it: `built` or `unbuilt`."""
    if surface != "cli":
        raise SurfaceError(f"--surface {surface}: this module builds the cli face only")
    package = package_of(root)
    out = []
    for name, meta in declared(root).items():
        if noun is not None and name != noun:
            continue
        n = noun_of(root, package, name)
        api = None if meta["root"] else _tree(n.dir / "api.py")
        bound = _keys(_literal(api, "VERBS") or ast.Dict([], [])) if api else set()
        state = "root" if meta["root"] else noun_state(n)[0]
        for verb in sorted(meta["verbs"]):
            out.append(
                {
                    "noun": name,
                    "verb": verb,
                    "built": "built" if verb in bound else "unbuilt",
                    "noun_state": state,
                }
            )
    return out
