"""The parser every CLI node builds, and the three argparse defaults it changes.

argparse gets three things wrong for a command line with verbs, and `arch-python/CLI.md`
names each:

- **An unknown argument is reported under the node, not the verb.** A verb's parser hands
  arguments it does not recognise up to the top-level parser, so `python -m racecar.graph
  check --for` prints `graph`'s help and never mentions `check` or `--for`. Each verb's
  parser records itself when argparse chooses it, and `parse_args(parser, argv)` reports
  leftovers through that parser's own `error()`.
- **A missing required argument is reported as argparse's `the following arguments are
  required`.** `VerbParser` names it in the form's own words instead: the verb's help on
  stderr, then `<prog>: needs <arguments>` as the last line, exit 2. An unknown argument on
  the same command line is reported first.
- **An unknown verb is reported as `argument phase: invalid choice`.** `phase` is where a node
  stores the chosen verb, an internal name the lexicon matches schemas on. `NounParser` tells
  the reader `unknown verb 'chek'; did you mean 'check'?` instead, or lists the verbs.

Every other usage error keeps exit 2: the full help, then argparse's own message as the last
line. argparse's messages are matched on its own templates, translated the way argparse
translates them, never on English text.

The shape is `NounParser` for a node, `VerbParser` for each verb and for a parser without
verbs, `mark_subparsers` and `parse_args(parser, argv)`, the names an adopter's CLI
uses. This module is also the canon an adopter receives: `racecar.surface create` copies
it into a package as `<pkg>/lib/cli.py`, so it imports nothing but the standard library.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import copy
import difflib
import io
import sys
from collections.abc import Callable, Iterable
from gettext import gettext
from typing import Any, NoReturn, TypeVar, overload

_N = TypeVar("_N")

#: argparse's own wording, cut before the part that varies.
MISSING = gettext("the following arguments are required: %s").split("%s", maxsplit=1)[0]
INVALID = gettext("invalid choice: %(value)r (choose from %(choices)s)").split(
    "%(value)r", maxsplit=1
)[0]
UNRECOGNIZED = gettext("unrecognized arguments: %s")
#: A required mutually exclusive group left empty: `one of the arguments --a --b is required`.
ONE_OF = gettext("one of the arguments %s is required").split("%s", maxsplit=1)

#: Where a verb's parser records itself on the namespace when argparse chooses it.
_CHOSEN = "_cli_verb_parser"


#: Section order and heading for a grouped listing (`print_commands(kinds=...)`). The
#: write heading names no flags: each write verb's flags follow its own plan/act default.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("read", "Read-only (no writes):"),
    ("write", "Writes:"),
    ("placeholder", "Placeholders:"),
)


def print_commands(
    pkg: str,
    nodes: list[tuple[str, str]],
    verbs: list[tuple[str, str]] | None = None,
    *,
    kinds: dict[str, str] | None = None,
    sections: tuple[tuple[str, str], ...] = SECTIONS,
) -> None:
    """Render a node's listing: its contained nouns, then its own verbs.

    Every line is a full `python -m ...` invocation, runnable as printed.

    Flat by default. With `kinds`, a `name -> key` map covering every noun and verb, the
    entries are grouped under `sections` in `sections` order, empty groups skipped, so a
    verb that writes is visibly apart from one that only reads. An entry missing from
    `kinds`, or keyed outside `sections`, raises: a new verb cannot silently drop out.
    """
    rows = [(n, f"python -m {pkg}.{n}", d) for n, d in nodes]
    rows += [(n, f"python -m {pkg} {n}", d) for n, d in verbs or []]
    width = max((len(path) for _, path, _ in rows), default=0)
    print(f"python -m {pkg}\n")
    if kinds is None:
        for index, (_, path, desc) in enumerate(rows):
            if index == len(nodes) and nodes:
                print()
            print(f"  {path.ljust(width)}   {desc}")
        print("\nAppend --help to any command for its options.")
        return
    known = {key for key, _ in sections}
    unclassified = [name for name, _, _ in rows if kinds.get(name) not in known]
    if unclassified:
        raise ValueError(
            f"kinds missing or out of vocabulary for: {unclassified} "
            f"(known section keys: {sorted(known)})"
        )
    for key, label in sections:
        group = [(path, desc) for name, path, desc in rows if kinds[name] == key]
        if not group:
            continue
        print(f"  {label}")
        for path, desc in group:
            print(f"    {path.ljust(width)}   {desc}")
        print()
    print("Append --help to any command for its options.")


class _Verbs:
    """A subparsers action that fills `help=` from the node's `subcommands()` table.

    It wraps the action argparse built and forwards the one call a node makes.
    """

    def __init__(
        self,
        action: argparse._SubParsersAction[argparse.ArgumentParser],
        table: dict[str, str],
    ) -> None:
        """Hold the real action and the description table it draws help from."""
        self._action = action
        self._table = table

    @property
    def choices(self) -> dict[str, argparse.ArgumentParser]:
        """Each verb's parser by name, as argparse's own action holds them."""
        return dict(self._action.choices)

    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser:
        """Add a verb, taking its `help=` from `subcommands()`.

        An explicit `help=` is refused.
        """
        if "help" in kwargs:
            raise TypeError(
                f"add_parser({name!r}, help=...): the help text comes from "
                "subcommands(), which is its one home. Edit the entry there."
            )
        sub: argparse.ArgumentParser = self._action.add_parser(
            name, help=self._table[name], **kwargs
        )
        # argparse copies a verb parser's defaults into the namespace only when that verb
        # is chosen, so this names the parser an unknown argument belongs to.
        sub.set_defaults(**{_CHOSEN: sub})
        return sub


class VerbParser(argparse.ArgumentParser):
    """A verb's parser, and any parser without verbs: a bad invocation gets help, not a rebuke.

    A missing required argument prints this parser's help to stderr, then names what is
    missing as the last line (`<prog>: needs <arguments>`), and exits 2: nothing ran. A
    required group of alternatives left empty is the same case, and is named as the
    alternatives (`needs --surface or --all`). It yields to an unknown argument on the same
    command line, which `parse_args` reports first.
    Anything else prints the help to stderr, then argparse's own message as the last line,
    and exits 2. stdout stays empty in every case, so under `--json` it never holds text.
    """

    def error(self, message: str) -> NoReturn:
        """Answer a bad invocation: help, then what was wrong."""
        needed = None
        if message.startswith(MISSING):
            needed = message[len(MISSING) :]
        elif message.startswith(ONE_OF[0]) and message.endswith(ONE_OF[1]):
            needed = " or ".join(message[len(ONE_OF[0]) : -len(ONE_OF[1])].split())
        self.print_help(sys.stderr)
        if needed is not None:
            self.exit(2, f"{self.prog}: needs {needed}\n")
        self.exit(2, f"{self.prog}: error: {message}\n")


class NounParser(VerbParser):
    """A noun's top-level parser: its verbs are `VerbParser`s, and an unknown verb is named.

    It also lists the node's sub-nouns: pass `nodes=commands()` and `-h` shows them in the
    epilog, as the bare listing does.
    """

    def __init__(
        self,
        *args: object,
        nodes: list[tuple[str, str]] | None = None,
        **kwargs: object,
    ) -> None:
        """Build the parser, folding `nodes` into the epilog when the node has children."""
        self._verb_action: argparse._SubParsersAction[argparse.ArgumentParser] | None
        self._verb_action = None
        if nodes:
            prog = str(kwargs.get("prog", ""))
            width = max(len(n) for n, _ in nodes)
            listed = "\n".join(f"  {prog}.{n.ljust(width)}   {d}" for n, d in nodes)
            kwargs["epilog"] = f"contained nodes:\n{listed}"
            kwargs["formatter_class"] = argparse.RawDescriptionHelpFormatter
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def add_subparsers(self, **kwargs: Any) -> Any:
        """Add the verbs, as `VerbParser`s unless told otherwise, and remember them."""
        kwargs.setdefault("parser_class", VerbParser)
        self._verb_action = super().add_subparsers(**kwargs)
        return self._verb_action

    def verbs(self, declared: list[tuple[str, str]], **kwargs: Any) -> _Verbs:
        """Add the verbs with each one's `help=` taken from `subcommands()`.

        An `add_parser` for a verb the table does not declare raises `KeyError` while the
        parser is built.
        """
        return _Verbs(self.add_subparsers(**kwargs), dict(declared))

    # argparse's three overloads, restated so the override accepts every call the base does.
    # pylint: disable=arguments-differ,signature-differs
    @overload
    def parse_args(
        self, args: Iterable[str] | None = None, namespace: None = None
    ) -> argparse.Namespace: ...
    @overload
    def parse_args(self, args: Iterable[str] | None, namespace: _N) -> _N: ...
    @overload
    def parse_args(self, *, namespace: _N) -> _N: ...

    # pylint: enable=arguments-differ,signature-differs
    def parse_args(
        self, args: Iterable[str] | None = None, namespace: Any = None
    ) -> Any:
        """`parse_args(self, args)`: so the method spelling routes leftovers correctly too."""
        return parse_args(self, args, namespace)

    def error(self, message: str) -> NoReturn:
        """argparse's invalid-choice message for this node's verbs is said as `unknown verb`."""
        super().error(self._verb_named(message))

    def _verb_named(self, message: str) -> str:
        """`argument phase: invalid choice: 'chek' …` → `unknown verb 'chek'; did you mean …`."""
        action = self._verb_action
        if action is None:
            return message
        prefix = f"argument {action.dest}: {INVALID}"
        if not message.startswith(prefix):
            return message
        raw = message[len(prefix) :].split(" (", maxsplit=1)[0]
        try:
            typed = str(ast.literal_eval(raw))
        except (ValueError, SyntaxError):
            typed = raw.strip("'\"")
        return unknown_verb(typed, list(action.choices))


def mark_subparsers(sub: Any) -> None:
    """Make every verb parser under `sub` record itself when argparse chooses it.

    `sub` is what `add_subparsers()` returned; call it after the last `add_parser`.
    `NounParser.verbs()` does this for each verb as it is added, so only a node that calls
    `add_subparsers()` directly needs it.
    """
    for verb_parser in sub.choices.values():
        verb_parser.set_defaults(**{_CHOSEN: verb_parser})


def _relax(parser: argparse.ArgumentParser, seen: set[int]) -> list[Any]:
    """Set every requirement under `parser`, its verbs' included, to not required.

    Returns what it changed, to restore. Only a requirement changes, so a parse run while
    relaxed recognises exactly what a real parse recognises.
    """
    if id(parser) in seen:
        return []
    seen.add(id(parser))
    # argparse keeps its actions and groups on private attributes and offers no public way
    # to walk them; this module is the one place that reads them.
    # pylint: disable=protected-access
    changed: list[Any] = []
    for action in parser._actions:
        if action.required:
            action.required = False
            changed.append(action)
        if isinstance(action, argparse._SubParsersAction):
            for verb_parser in action.choices.values():
                changed += _relax(verb_parser, seen)
    for group in parser._mutually_exclusive_groups:
        if group.required:
            group.required = False
            changed.append(group)
    return changed


def parse_args(
    parser: argparse.ArgumentParser,
    argv: Iterable[str] | None = None,
    namespace: Any = None,
) -> Any:
    """Parse `argv`; report an unknown argument through the verb it was typed after.

    The node's own parser answers when no verb was chosen. The attribute naming the chosen
    verb's parser is dropped before the namespace leaves here.

    An unknown argument is looked for first, in a parse with every requirement relaxed,
    because argparse reports a missing required argument before the unrecognised ones. The
    parse runs twice, so no `type=` may open a file.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    relaxed = _relax(parser, set())
    # The relaxed pass only looks for unknown arguments. Anything it would print or exit on
    # is discarded and left to the real pass, whose usage shows required arguments as such.
    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            probe, extras = parser.parse_known_args(argv, copy.copy(namespace))
    except SystemExit:
        probe, extras = None, []
    finally:
        for requirement in relaxed:
            requirement.required = True
    if extras:
        owner_first: argparse.ArgumentParser = getattr(probe, _CHOSEN, None) or parser
        owner_first.error(UNRECOGNIZED % " ".join(extras))
    parsed, extras = parser.parse_known_args(argv, namespace)
    owner: argparse.ArgumentParser = getattr(parsed, _CHOSEN, None) or parser
    if hasattr(parsed, _CHOSEN):
        delattr(parsed, _CHOSEN)
    if extras:
        owner.error(UNRECOGNIZED % " ".join(extras))
    return parsed


def run(
    parser: argparse.ArgumentParser,
    argv: Iterable[str] | None,
    *,
    refusal: type[Exception],
    error: Callable[[str], None],
    listing: Callable[[], None] | None = None,
) -> int:
    """The body of a noun's `main(argv)`: parse, run the chosen verb, map a refusal to an exit.

    With no verb it describes and acts on nothing (CLI.md B1): `listing` when the noun has
    sub-nouns, its help otherwise. A verb runs as the `func` its parser was bound to. An
    exception of type `refusal` (the package's `ApiError`) has its message handed to `error`
    and its `exit_code` returned, 2 when it carries none.
    """
    args = parse_args(parser, argv)
    if args.phase is None:
        (listing or parser.print_help)()
        return 0
    try:
        return int(args.func(args))
    except refusal as exc:
        error(str(exc))
        return int(getattr(exc, "exit_code", 2))


def unknown_verb(typed: str, verbs: list[str]) -> str:
    """What to say about a word that is not a verb: the nearest verbs, else all of them."""
    near = difflib.get_close_matches(typed, verbs, n=3)
    if near:
        return f"unknown verb {typed!r}; did you mean {' or '.join(map(repr, near))}?"
    return f"unknown verb {typed!r}; the verbs are {', '.join(verbs)}"
