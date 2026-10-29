"""Deriving: the lexicon entries a repo's cli implements and its lexicon does not declare.

Part of `lib.lexicon`. Reads what the cli offers through the CLI audit (`_audit`) and
names each noun, verb and param missing from the lexicon as the `lexicon create` command
that would declare it. With `apply`, each command runs through `declare`
(get-or-create), so a second run finds nothing.

It writes only the lexicon: it never edits the code or deletes an entry the code lacks.
Each new entry's summary is a TODO and its bearing `draft`. A param is a verb's long
flag without dashes, or a positional's name, never `--json` or `-h`; it is listed on the
verb, and a shared `param/` node is `check --apply`'s to write.

Complexity: O(N + V + P) over the audited nodes, verbs and params, plus one CLI audit
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from lib import not_a_command
from lib.lexicon._audit import _flat_args, cli_tree
from lib.lexicon._corpora import OWN, Lexicon, LexiconError
from lib.lexicon._emit import emit
from lib.lexicon._nodes import (
    OK,
    _params_of,
    root_noun,
    verb_node,
)
from lib.lexicon._scaffold import declare
from lib.lexicon.renderer import text
from lib.shared._constants import DELIVERY_DIR
from lib.shared._root import package_dir

#: Flags every verb carries by the form, which declare nothing about the verb.
_CARRIED = frozenset({"json", "help"})


class Derived(NamedTuple):
    """One `lexicon create` the code implies: a noun, perhaps a verb, and its missing params."""

    noun: str
    verb: str | None
    params: tuple[str, ...]

    def command(self) -> str:
        """The command that declares it, exactly as a person would type it.

        The delivered script, since `lexicon derive` runs in repos without racecar.
        """
        parts = [f"python3 {DELIVERY_DIR}/lexicon.py create", f"--noun {self.noun}"]
        if self.verb is not None:
            parts.append(f"--verb {self.verb}")
        parts += [f"--param {p}" for p in self.params]
        return " ".join(parts)


def _param(arg: dict[str, Any]) -> str | None:
    """The lexicon word for one argument: its long flag without dashes, or its name."""
    flags = [str(f) for f in arg.get("flags") or []]
    long = [f for f in flags if f.startswith("--")]
    if long:
        word = long[0][2:]
    elif flags:
        return None  # a short flag alone names no word
    else:
        word = str(arg.get("dest") or "")
    return word if word and word.replace("_", "-") not in _CARRIED else None


def implemented(root: Path) -> dict[str, dict[str, list[str]]]:
    """`{noun: {verb: [params]}}` the cli offers, from one CLI audit of `root`.

    The package root is keyed by `""`; a noun by its dotted path below the package.
    """
    package = package_dir(root)
    if package is None:
        raise LexiconError(f"{root}: no package under src/ to derive a lexicon from")
    # `cli_tree` refuses a tree with a node that did not import.
    tree = cli_tree(root)
    found: dict[str, dict[str, list[str]]] = {}

    def walk(node: dict[str, Any]) -> None:
        if node.get("kind") == "missing":
            return  # no `__main__.py`: a package, not a cli node
        pkg = str(node["pkg"])
        noun = pkg[len(package.name) + 1 :] if pkg != package.name else ""
        verbs = found.setdefault(noun, {})
        for sub in node.get("subcommands") or []:
            if not sub.get("name"):
                continue
            words = [_param(arg) for arg in _flat_args(sub.get("args") or [])]
            verbs[str(sub["name"])] = [w for w in words if w]
        for child in node.get("children") or []:
            walk(child)

    walk(tree)
    return found


def derive(root: Path, lexicon: Lexicon) -> list[Derived]:
    """Every noun, verb and param the cli implements and the lexicon does not declare.

    One entry per `lexicon create` it would take, in the audit's order: a noun with no
    entry, a verb with no entry (with all its params), or a declared verb missing some
    params (with only those). The package root's verbs sit under the lexicon's root noun.
    """
    if lexicon.at("README.md", OWN) is None:
        raise LexiconError(
            f"{lexicon.own}: no lexicon root (README.md); a derived entry needs a corpus "
            "to join"
        )
    out: list[Derived] = []
    for noun, verbs in implemented(root).items():
        name = noun or root_noun(lexicon)
        if noun and not verb_node(lexicon, name, None).is_file():
            out.append(Derived(name, None, ()))
        for verb, params in verbs.items():
            node = verb_node(lexicon, name, verb)
            listed = set(_params_of(node)) if node.is_file() else set()
            missing = tuple(p for p in params if p not in listed)
            if not node.is_file():
                out.append(Derived(name, verb, tuple(params)))
            elif missing:
                out.append(Derived(name, verb, missing))
    return out


def apply_derived(lexicon: Lexicon, derived: list[Derived]) -> list[str]:
    """Run each derived `create` through `declare`; the paths written, in order."""
    written: list[str] = []
    for entry in derived:
        written += declare(lexicon, entry.noun, entry.verb, list(entry.params))
    return written


def run(root: Path, lexicon: Lexicon, *, apply: bool = False) -> dict[str, list[str]]:
    """`{"commands", "declared"}`: create commands implied, and paths `apply` wrote.

    One CLI audit, so the printed and applied lists are the same.
    """
    found = derive(root, lexicon)
    written = apply_derived(lexicon, found) if apply else []
    return {"commands": [e.command() for e in found], "declared": written}


def main(
    root: Path, lexicon: Lexicon, *, apply: bool = False, as_json: bool = False
) -> int:
    """Print the `create` commands the cli implies; with `--apply`, run them."""
    record = run(root, lexicon, apply=apply)
    if as_json:
        emit(record, None)
    else:
        print(text.derive(record, apply=apply))
    return OK


if __name__ == "__main__":
    not_a_command()
