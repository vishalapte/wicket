"""Every CLI node in __PKG__ answers each invocation as arch-python/CLI.md §6 says.

Generic over the nodes: it reads each node's own ``parser()``, so a noun added later, or a
verb added or renamed, is covered without editing this file. What only one noun knows (its
results, its text, its refusals) is tested beside that noun. argparse wraps usage lines to
the terminal width, so usage text is compared with its whitespace collapsed.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

import __PKG__.__main__ as root

Run = Callable[..., tuple[int, str, str]]


def _flat(text: str) -> str:
    return " ".join(text.split())


def _nodes(parent: ModuleType = root, prefix: str = "__PKG__") -> list[ModuleType]:
    """Every noun's ``__main__`` below ``parent``, sub-nouns included, parents first."""
    found: list[ModuleType] = []
    for name, _ in parent.commands():
        node = importlib.import_module(f"{prefix}.{name}.__main__")
        found += [node, *_nodes(node, f"{prefix}.{name}")]
    return found


def _verbs(node: ModuleType) -> dict[str, argparse.ArgumentParser]:
    """Each verb's own parser, read from the node's parser."""
    actions = node.parser()._actions
    groups = [a for a in actions if isinstance(a, argparse._SubParsersAction)]
    return dict(groups[0].choices) if groups else {}


def _requires(verb: argparse.ArgumentParser) -> bool:
    """Whether the verb has an argument it cannot run without.

    argparse marks a `nargs="*"` or `"?"` positional `required`, yet runs without it, so
    those do not count.
    """
    return any(
        action.required and action.nargs not in ("*", "?") for action in verb._actions
    )


def _value(action: argparse.Action) -> str:
    """A value the argument accepts: its first choice, a number, or a word."""
    if action.choices:
        return str(next(iter(action.choices)))
    return "1" if action.type in (int, float) else "x"


def _needed(verb: argparse.ArgumentParser) -> list[str]:
    """The arguments the verb cannot run without, each given a value it accepts."""
    argv: list[str] = []
    for action in verb._actions:
        if not action.required or action.nargs in ("*", "?"):
            continue
        if action.option_strings:
            argv += [action.option_strings[0], _value(action)]
        else:
            argv.append(_value(action))
    return argv


def _reading_commands() -> list[tuple[str, str]]:
    """`(module, verb)` for each command `surface.jsonl` says only reads.

    Taken from the spec, the one list of the package's commands and what each does. Only
    these are run with made-up arguments: a command that writes or starts a job acts on
    real data, and a test must not.
    """
    spec = Path(__file__).resolve().parents[1] / "surface.jsonl"
    rows = [json.loads(line) for line in spec.read_text().splitlines() if line.strip()]
    commands: list[tuple[str, str]] = []
    for row in rows:
        if row.get("kind") == "read" and row.get("cli"):
            module, verb = str(row["cli"]).removeprefix("python -m ").rsplit(" ", 1)
            commands.append((module, verb))
    return commands


NODES = _nodes()
WITH_VERBS = [node for node in NODES if _verbs(node)]
VERBS = [(node, verb) for node in NODES for verb in _verbs(node).values()]
REQUIRING = [(node, verb) for node, verb in VERBS if _requires(verb)]
READS = _reading_commands()


@pytest.mark.parametrize("node", NODES, ids=[n.__name__ for n in NODES])
def test_bare_invocation_describes_and_exits_0(node: ModuleType, run_cli: Run) -> None:
    """A noun with sub-nouns lists them; one without prints its help. Nothing runs."""
    code, out, err = run_cli(node.main, [])
    assert (code, err) == (0, "")
    prog = node.parser().prog
    if node.commands():
        assert _flat(out).startswith(prog)
        assert all(f"{prog}.{name}" in out for name, _ in node.commands())
    else:
        assert _flat(out).startswith(f"usage: {prog} ")


@pytest.mark.parametrize("node", WITH_VERBS, ids=[n.__name__ for n in WITH_VERBS])
def test_an_unknown_verb_is_named_as_one(node: ModuleType, run_cli: Run) -> None:
    code, out, err = run_cli(node.main, ["no-such-verb"])
    assert (code, out) == (2, "")
    last = err.rstrip().splitlines()[-1]
    assert last.startswith(f"{node.parser().prog}: error: unknown verb 'no-such-verb'")
    assert "argument phase" not in err


@pytest.mark.parametrize(
    ("node", "verb"), REQUIRING, ids=[v.prog for _, v in REQUIRING]
)
def test_a_missing_argument_shows_the_verbs_help_and_exits_2(
    node: ModuleType, verb: argparse.ArgumentParser, run_cli: Run
) -> None:
    code, out, err = run_cli(node.main, [verb.prog.rsplit(" ", 1)[-1]])
    assert code == 2
    assert out == ""
    assert _flat(err).startswith(f"usage: {verb.prog} ")
    assert err.rstrip().splitlines()[-1].startswith(f"{verb.prog}: needs ")


@pytest.mark.parametrize(("node", "verb"), VERBS, ids=[v.prog for _, v in VERBS])
def test_an_unknown_argument_is_reported_under_its_verb(
    node: ModuleType, verb: argparse.ArgumentParser, run_cli: Run
) -> None:
    code, out, err = run_cli(
        node.main, [verb.prog.rsplit(" ", 1)[-1], "--no-such-flag"]
    )
    assert (code, out) == (2, "")
    assert _flat(err).startswith(f"usage: {verb.prog} ")
    assert err.rstrip().endswith(
        f"{verb.prog}: error: unrecognized arguments: --no-such-flag"
    )


@pytest.mark.parametrize(("module", "verb"), READS, ids=[f"{m} {v}" for m, v in READS])
def test_every_command_runs_and_prints_json(
    module: str, verb: str, run_cli: Run
) -> None:
    """Each command that only reads, given what it needs, prints one JSON object."""
    node = importlib.import_module(f"{module}.__main__")
    argv = [verb, *_needed(_verbs(node)[verb]), "--json"]
    code, out, err = run_cli(node.main, argv)
    assert (code, err) == (0, ""), err
    assert isinstance(json.loads(out), dict)
