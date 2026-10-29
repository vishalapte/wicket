"""The command lines that exercise a noun's cli: the parser half derived, the run half declared.

The list a before-and-after comparison runs has two halves.

The parser half is derived on each run, never stored. Per node: the bare node, `-h`, an
unknown verb and an unknown flag; per verb: `<verb> -h`, and the bare verb where it needs an
argument, which argparse answers without running anything. None of these runs a verb, so none
has a side effect, and all of them come from `parser()` alone -- the CLI audit's tree. Read
from the code being compared, a removed flag or verb then shows up as a difference.

The run half is declared in the lexicon, beside the verb's `params:`, as `invocations:`:
one entry per command line, the arguments after the verb and, optionally, the directory inside
the tree it runs from. It is authored apart from the code it grades (P-07). Every declared
line of a built verb runs, whatever its kind: `_transcript` runs each one in its own copy of
the tree, with a scratch home and no
credentials, so what a `write` line writes lands in the copy and nothing it does can reach a
tracker, a bucket or a host that needs a key.

A built `read` or `write` verb with no declared entry is a finding, and `update` proposes the
entries it lacks from its parser: one per switch, one per choice value, a `<value>` where a
value is needed. The author edits that skeleton and deletes the lines that are only rendering
choices (arch-python/CLI.md). A `job` verb owes none. `surface create` seeds
`invocations: [""]` on a `read` or `write` verb it records, so a package built
from a list of `create` calls carries a line for each.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.shared import _frontmatter, _spec

from ._error import SurfaceError
from ._form import _lexicon, audit_tree, package_of
from ._vocab import face, named

#: The frontmatter key a verb's lexicon node declares its command lines under.
KEY = "invocations"
#: The spec rows that owe a declared line: a built verb that reads or writes. A `job` starts
#: work that keeps running, so a line replaying it is chosen, never demanded.
OWED = {("read", "exists"), ("write", "exists")}
#: What `surface create` seeds on a verb it records: the bare verb.
SEED = 'invocations: [""]'


def seed(node: Path) -> bool:
    """Give a verb's lexicon node the bare-verb line when it declares none; True if written.

    Only the frontmatter is touched, and only by adding one line after `params:`. A node that
    already names `invocations:` is the author's and is left alone, so a second `create`
    writes nothing.
    """
    if not node.is_file():
        return False
    head, body = _frontmatter.split(node.read_text(encoding="utf-8"))
    if head is None or f"\n{KEY}:" in f"\n{head}":
        return False
    lines = head.split("\n")
    at = next((i for i, line in enumerate(lines) if line.startswith("params:")), None)
    lines.insert(len(lines) if at is None else at + 1, SEED)
    node.write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")
    return True


#: A verb and a flag no parser declares, so the parser's own refusal is what gets recorded.
UNKNOWN_VERB = "no-such-verb"
UNKNOWN_FLAG = "--no-such-flag"
_VALUE = "<value>"


@dataclass(frozen=True)
class Invocation:
    """One command line: which noun and verb it exercises, and whether a comparison runs it.

    `args` are what follows `python -m <module>`. `source` is `parser` for the derived half
    and `declared` for the lexicon's. `fixture`, on a declared entry, is the repo-relative
    directory inside the tree's copy that the command runs from.
    """

    noun: str
    verb: str | None
    module: str
    args: tuple[str, ...]
    source: str
    runs: bool
    fixture: str | None = None

    @property
    def argv(self) -> list[str]:
        """The noun and its arguments, the way the lexicon names the command."""
        return [self.noun, *self.args]

    def command(self) -> str:
        """The command line a person would type."""
        return " ".join(["python", "-m", self.module, *map(shlex.quote, self.args)])

    def record(self) -> dict[str, Any]:
        """The JSON form: what `--json` prints and what a comparison is keyed on."""
        return {
            "noun": self.noun,
            "verb": self.verb,
            "argv": self.argv,
            "command": self.command(),
            "source": self.source,
            "runs": self.runs,
            "fixture": self.fixture,
        }


def _nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Every node of the audit tree, depth first."""
    out = [tree]
    for child in tree.get("children") or []:
        out += _nodes(child)
    return out


def _args(verb: dict[str, Any]) -> list[dict[str, Any]]:
    """A verb's arguments, a mutually exclusive group flattened into its members."""
    out: list[dict[str, Any]] = []
    for arg in verb.get("args") or []:
        out += arg.get("oneOf", [arg])
    return out


def _needs_value(verb: dict[str, Any]) -> bool:
    """Whether a bare run of the verb stops at a missing argument before doing anything."""
    for arg in verb.get("args") or []:
        if "oneOf" in arg and arg.get("required"):
            return True
        for member in arg.get("oneOf", [arg]):
            positional = not member.get("flags")
            if member.get("required") or (
                positional and member.get("nargs") not in ("?", "*")
            ):
                return True
    return False


def _noun(module: str, package: str) -> str:
    """`racecar.graph.topology` is the noun `graph.topology`; the root is the package."""
    return module[len(package) + 1 :] if module != package else package


def kinds(root: Path, package: str) -> dict[tuple[str, str], tuple[str, str]]:
    """`(noun, verb) -> (kind, status)` from the spec; empty where the repo keeps none."""
    spec = _spec.spec_path(root)
    out: dict[tuple[str, str], tuple[str, str]] = {}
    if not spec.is_file():
        return out
    for row in _spec.read_rows(spec):
        parsed_cli = _spec.command(row)
        if parsed_cli is not None:
            module, verb = parsed_cli
            out[(_noun(module, package), verb)] = (
                str(row.get("kind")),
                str(row.get("status")),
            )
    return out


def parser_half(tree: dict[str, Any], package: str) -> list[Invocation]:
    """The command lines `parser()` alone yields: none runs a verb."""
    out: list[Invocation] = []
    for node in _nodes(tree):
        module = str(node.get("pkg") or "")
        verbs = node.get("subcommands") or []
        if not module or not (verbs or node.get("children")):
            continue
        noun = _noun(module, package)
        for args in ((), ("-h",), (UNKNOWN_VERB,), (UNKNOWN_FLAG,)):
            out.append(Invocation(noun, None, module, args, "parser", True))
        for verb in verbs:
            name = str(verb["name"])
            out.append(Invocation(noun, name, module, (name, "-h"), "parser", True))
            if _needs_value(verb):
                out.append(Invocation(noun, name, module, (name,), "parser", True))
    return out


def _entries(
    node: Path,
) -> list[tuple[tuple[str, ...], tuple[str, ...], str | None]]:
    """A verb node's `invocations:`, each as `(node-level args, args after the verb, fixture)`.

    An entry is a string, the arguments after the verb, or a mapping: `args`, `before` for
    the node's own flags that go ahead of the verb (`--root`), and `fixture`.
    """
    if not node.is_file():
        return []
    raw = _frontmatter.load(node).get(KEY) or []
    if not isinstance(raw, list):
        raise SurfaceError(f"{node}: `{KEY}` must be a list of command lines")
    out: list[tuple[tuple[str, ...], tuple[str, ...], str | None]] = []
    for entry in raw:
        mapping = entry if isinstance(entry, dict) else {"args": entry}
        args, before = mapping.get("args", ""), mapping.get("before", "")
        fixture = mapping.get("fixture")
        if not isinstance(args, str) or not isinstance(before, str):
            raise SurfaceError(
                f"{node}: each `{KEY}` entry is a string, or {{args, before, fixture}}"
            )
        out.append((tuple(shlex.split(before)), tuple(shlex.split(args)), fixture))
    return out


def declared_half(root: Path, tree: dict[str, Any], package: str) -> list[Invocation]:
    """The command lines the lexicon declares, each marked with whether it may run."""
    lexicon = _lexicon(root)
    corpora = lexicon.lexicon_corpora(root)
    spec = kinds(root, package)
    out: list[Invocation] = []
    for node in _nodes(tree):
        module = str(node.get("pkg") or "")
        noun = _noun(module, package)
        lexicon_noun = noun if noun != package else lexicon.root_noun(corpora)
        for verb in node.get("subcommands") or []:
            name = str(verb["name"])
            where = lexicon.verb_node(corpora, lexicon_noun, name)
            kind, status = spec.get((noun, name), ("", ""))
            for before, args, fixture in _entries(where):
                runs = status == "exists" and kind in ("read", "write", "job")
                line = (*before, name, *args)
                out.append(
                    Invocation(noun, name, module, line, "declared", runs, fixture)
                )
    return out


def invocations(root: Path, noun: str | None = None) -> list[Invocation]:
    """Every command line that exercises the cli, or one noun's: parser half, then declared."""
    package = package_of(root)
    tree = audit_tree(root)
    found = parser_half(tree, package) + declared_half(root, tree, package)
    return [line for line in found if noun is None or line.noun == noun]


def propose(verb: dict[str, Any]) -> list[str]:
    """Skeleton entries for a verb from its parser, for the author to edit and prune.

    One line with no arguments, one per switch, one per value of a choice flag; `<value>`
    stands where a value is needed. Positionals and required flags are filled on every line,
    since a line without them only reaches the parser's refusal.
    """
    required: list[str] = []
    optional: list[list[str]] = []
    for arg in _args(verb):
        flags = arg.get("flags") or []
        choices = [str(c) for c in arg.get("choices") or []]
        if not flags:
            required.append(choices[0] if choices else _VALUE)
            continue
        flag = str(flags[0])
        if arg.get("required"):
            required += [flag, choices[0] if choices else _VALUE]
        elif arg.get("action") == "store_true":
            optional.append([flag])
        elif choices:
            optional += [[flag, choice] for choice in choices]
    lines = [" ".join(required)] + [" ".join(required + extra) for extra in optional]
    return [line.strip() for line in lines]


def absent(
    root: Path, noun: str | None = None, verb: str | None = None
) -> list[dict[str, str]]:
    """Each built `read` or `write` verb whose lexicon node declares no command line to run.

    Empty where the repo keeps no spec: without a row there is no `kind`, and nothing says
    which verbs start work that keeps running.
    """
    package = package_of(root)
    spec = kinds(root, package)
    if not spec:
        return []
    tree = audit_tree(root)
    ran = {
        (line.noun, line.verb)
        for line in declared_half(root, tree, package)
        if line.runs
    }
    lexicon = _lexicon(root)
    corpora = lexicon.lexicon_corpora(root)
    out: list[dict[str, str]] = []
    for node in _nodes(tree):
        module = str(node.get("pkg") or "")
        this = _noun(module, package)
        if noun is not None and this != noun:
            continue
        for sub in node.get("subcommands") or []:
            name = str(sub["name"])
            if verb is not None and name != verb:
                continue
            if spec.get((this, name)) not in OWED or (this, name) in ran:
                continue
            where = lexicon.verb_node(
                corpora, this if this != package else lexicon.root_noun(corpora), name
            )
            out.append(
                {
                    "noun": this,
                    "verb": name,
                    "node": str(where.relative_to(root)),
                    "proposed": json.dumps(propose(sub)),
                }
            )
    return out


def for_face(root: Path, surface: str, noun: str | None = None) -> list[dict[str, Any]]:
    """The command lines that exercise `surface`, as records; only the cli has any."""
    if face(surface) != "cli":
        return []
    return named(surface, [line.record() for line in invocations(root, noun)])
