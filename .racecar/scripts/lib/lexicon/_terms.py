"""The term scan: a retired word still cited somewhere in the repo.

Part of `lib.lexicon`, the package `scripts/lexicon.py` is a command line over. The tests,
the `lexicon` noun's api and the delivered CLI import these functions.

Complexity: O(T*F), T = terms, F = files scanned for a citation of one
"""

from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass
from pathlib import Path

from check_docs import ignore_patterns
from lib.lexicon._corpora import Lexicon
from lib.lexicon._nodes import (
    CODE_SUFFIXES,
    DOC_SUFFIXES,
    NomenclatureError,
    _is_command,
)
from lib.shared import _frontmatter
from lib.shared._files import repo_files

STATUSES = ("reserved", "retired")

#: The statuses that must name a replacement. A retired string is gone, so a reader needs
#: what replaced it; a reserved word is held, and reservation is excluded from the set that
#: requires one (docs/lexicon/key/status.md).
NEEDS_INSTEAD = frozenset({"retired"})


def instead_disagrees(status: str, instead: str) -> bool:
    """Whether a node's `status` and `instead` break the rule: a status that needs a
    replacement and names none, or a replacement with no status to give it meaning."""
    return (status in NEEDS_INSTEAD and not instead) or (bool(instead) and not status)


CITING_DOCS = {
    Path("arch-python") / "CLI.md",
    Path("CHANGELOG.md"),
}


@dataclass(frozen=True)
class Term:
    """One controlled word, as its node declares it."""

    word: str
    node: str
    status: str
    instead: str


@dataclass(frozen=True)
class Retired:
    """One retired word still cited somewhere in the tree.

    Not named `Finding`, which is the grading loop's own record.
    """

    path: str
    line: int
    retired: str
    replacement: str

    def where(self) -> str:
        """The file and line the word is cited at, as a finding's subject."""
        return f"{self.path}:{self.line}"

    def message(self) -> str:
        """What is wrong there, as a finding's message."""
        return f"retired term {self.retired!r} — use {self.replacement!r}"

    def render(self) -> str:
        """Format as one audit line, matching racecar's other checkers."""
        return (
            f"  Major    {self.path}:{self.line}  retired term "
            f"{self.retired!r} — use {self.replacement!r}"
        )


def raw_kind(path: pathlib.Path) -> str:
    """The `kind:` a node declares, read without parsing it as a term.

    Silent on an undecodable file: the caller's term parse reports it as a NomenclatureError.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""
    for line in (_frontmatter.block(text) or "").splitlines():
        if line.startswith("kind:"):
            return line.split(":", 1)[1].strip()
    return ""


def read_terms(lexicon: Lexicon) -> list[Term]:
    """Every term node of the union, as declared. Empty when the lexicon has none.

    The union: a word racecar delivers, or retires, binds the repo that receives it.

    A node that declares `status:` must also declare `instead:`, and vice versa; either
    alone is a hard error.
    """
    terms: list[Term] = []
    for entry in lexicon.nodes():
        path = lexicon.path(entry)
        # Not a blanket README skip: a noun's README can be the only node that defines a
        # word (`host`, `config`). An index or a schema carries no word.
        if raw_kind(path) in {"index", "schema"}:
            continue
        if path.name == "README.md" and not raw_kind(path):
            continue
        # The nounspace shares this directory with the word trees and is told apart by
        # `kind:`. A `command` carries `command:` and no `name:`.
        head = raw_kind(path)
        # A command node is excluded: `graph/check.md` is an address, and what `check`
        # means lives at `verb/check.md`. A noun node is kept: `host/README.md` is the only
        # node that defines `host`.
        if head == "verb" and _is_command(entry, lexicon):
            continue
        where = f"{entry.directory}/{entry.filename}"
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise NomenclatureError(f"{where}: not valid UTF-8 ({exc})") from exc
        meta = _frontmatter.load(raw)
        name = meta.get("name")
        if not name:
            raise NomenclatureError(f"{where}: term node declares no `name`")
        # YAML reads bare `yes`, `no`, `on` and `off` as booleans, so an unquoted
        # `name: yes` would match nothing.
        if not isinstance(name, str):
            raise NomenclatureError(
                f"{where}: `name: {name}` parsed as {type(name).__name__}, not a string — "
                "YAML reads bare yes/no/on/off as booleans. Quote it."
            )
        status, instead = meta.get("status", ""), meta.get("instead", "")
        if status and status not in STATUSES:
            raise NomenclatureError(
                f"{where}: status {status!r} is not one of {STATUSES}. Absence "
                "means canon; only an exception is declared."
            )
        if instead_disagrees(status, instead):
            raise NomenclatureError(
                f"{where}: a retirement names its replacement and a replacement needs a "
                f"status (status={status!r}, instead={instead!r}). A retirement with no "
                "replacement says what to stop writing but not what to write; a "
                "replacement with no status is a rule that never fires."
            )
        # A flag's word carries its dashes: `--yes` is retired, the English word "yes" is
        # not. Keyed on the node's declared kind, never its directory.
        word = f"--{name}" if head == "param" else name
        terms.append(Term(word, where, status, instead))
    return terms


def retirements(terms: list[Term]) -> dict[str, str]:
    """The scannable rules: retired word -> its replacement. Reserved words excluded."""
    return {t.word: t.instead for t in terms if t.status == "retired"}


def _pattern(term: str) -> re.Pattern[str]:
    """Match `term` as a whole word, or exactly when it is a flag.

    A flag is matched literally and bounded on the right only: `--check` must not fire on
    `--check-something`, and there is no left word boundary to assert because `-` is not a
    word character.
    """
    if term.startswith("-"):
        return re.compile(re.escape(term) + r"(?![\w-])")
    # Each word is escaped separately and rejoined on `\s+`; escaping the whole term after
    # substituting the separator would escape the `\s+` itself.
    spaced = r"\s+".join(re.escape(word) for word in term.split())
    return re.compile(r"(?<![\w-])" + spaced + r"(?![\w-])", re.I)


def _needle(term: str) -> str:
    r"""The lowercase literal that must appear in a text for `_pattern(term)` to match.

    A necessary condition, not a sufficient one. `_pattern` joins multi-word terms on `\s+`,
    so the first word always appears verbatim; a flag appears whole. A substring test on
    `text.lower()` therefore never rejects a real match, and skips the regex for most files.
    """
    return term.split()[0].lower()


def _walk(root: Path) -> list[Path]:
    """Every file this checker grades, from the one shared walk.

    `repo_files` skips hidden directories, `__pycache__`, everything `.gitignore` names,
    and checked-out submodules. The project's own `ignore-paths` declaration is applied
    here, as in `check_doc_graph.in_scope`; it covers `drafts/`. Cached.
    """
    if root in _WALK_CACHE:
        return _WALK_CACHE[root]
    declared = ignore_patterns(root)
    out = [
        path
        for path in repo_files(root, "*")
        if path.is_file()
        and not any(r.search(path.relative_to(root).as_posix()) for r in declared)
    ]
    _WALK_CACHE[root] = out
    return out


_WALK_CACHE: dict[Path, list[Path]] = {}

_SKILL_CACHE: dict[Path, list[Path]] = {}


def skill_dirs(root: Path) -> list[Path]:
    """Every directory carrying a SKILL.md, sorted."""
    if root not in _SKILL_CACHE:
        _SKILL_CACHE[root] = sorted(
            {p.parent for p in _walk(root) if p.name == "SKILL.md"}
        )
    return _SKILL_CACHE[root]


_OWNED_CACHE: dict[Path, dict[Path, list[Path]]] = {}


class _TrieNode:
    """One path segment's worth of trie. `skill` is set only where a skill dir ends."""

    __slots__ = ("children", "skill")

    def __init__(self) -> None:
        self.children: dict[str, "_TrieNode"] = {}
        self.skill: Path | None = None


def _owned(root: Path) -> dict[Path, list[Path]]:
    """Every skill directory to the files it owns: its own tree, not its children's skills.

    A file belongs to the deepest skill directory above it. This is longest-prefix match on
    a trie keyed by path segment: each skill directory's `.parts` is inserted, and each file
    walks its own `.parts` down the trie, keeping the deepest skill-marked node. Files under
    no skill directory are not returned.
    """
    if root in _OWNED_CACHE:
        return _OWNED_CACHE[root]
    directories = skill_dirs(root)
    trie = _TrieNode()
    for directory in directories:
        node = trie
        for part in directory.parts:
            node = node.children.setdefault(part, _TrieNode())
        node.skill = directory
    by_skill: dict[Path, list[Path]] = {d: [] for d in directories}
    for path in _walk(root):
        node = trie
        deepest: Path | None = None
        for part in path.parts:
            nxt = node.children.get(part)
            if nxt is None:
                break
            node = nxt
            if node.skill is not None:
                deepest = node.skill
        if deepest is not None:
            by_skill[deepest].append(path)
    _OWNED_CACHE[root] = by_skill
    return by_skill


def _files(directory: Path, root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """A skill's own files of the given kinds: its own tree, not its children's skills."""
    return sorted(p for p in _owned(root).get(directory, []) if p.suffix in suffixes)


def _docs(directory: Path, root: Path) -> list[Path]:
    """The markdown a skill is made of."""
    return _files(directory, root, DOC_SUFFIXES)


def _homes(lexicon: Lexicon) -> tuple[Path, ...]:
    """The lexicon's homes, relative to its repo: the documents that declare words."""
    root = lexicon.root.resolve()
    return tuple(
        Path(os.path.relpath(home.path.resolve(), root)) for home in lexicon.homes
    )


def _exempt(relative: Path, homes: tuple[Path, ...]) -> bool:
    """Whether a document may name a retired word: it declares one (it sits in a home of the
    lexicon), or it records one."""
    return relative in CITING_DOCS or any(
        home == relative or home in relative.parents for home in homes
    )


def scan(
    lexicon: Lexicon, terms: list[Term], retired: dict[str, str]
) -> tuple[dict[str, dict[str, list[str]]], list[Retired]]:
    """Return `(skill -> {consumes, governs}, found)`. Reverse indexes derive later."""
    root = lexicon.root
    compiled = [(t.word, _needle(t.word), _pattern(t.word)) for t in terms]
    condemned = [(w, _needle(w), _pattern(w)) for w in retired]
    used: dict[str, dict[str, list[str]]] = {}
    found: list[Retired] = []
    # One read per file: the same file is reached by several relations below.
    texts: dict[Path, tuple[str, str]] = {}

    def read(path: Path) -> tuple[str, str]:
        """`(text, text.lower())`. The lowered copy is made once and prefiltered against."""
        if path not in texts:
            body = path.read_text(encoding="utf-8", errors="replace")
            texts[path] = (body, body.lower())
        return texts[path]

    def present(
        entries: list[tuple[str, str, re.Pattern[str]]],
        text: str,
        low: str,
        found: set[str] = frozenset(),  # type: ignore[assignment]
    ) -> set[str]:
        """Which terms this text names, beyond those already found.

        The caller accumulates a set, so a term already in `found` is not tested again.
        """
        return {
            w
            for w, needle, pattern in entries
            if w not in found and needle in low and pattern.search(text)
        }

    for directory in skill_dirs(root):
        name = str(directory.relative_to(root)) or "."
        consumes: set[str] = set()
        governs: set[str] = set()
        for doc in _docs(directory, root):
            text, low = read(doc)
            consumes |= present(compiled, text, low, consumes)
            if _exempt(doc.relative_to(root), _homes(lexicon)):
                continue
            # Only a term the whole document names can be on one of its lines, so the
            # per-line walk runs only for those.
            for word in present(condemned, text, low):
                pattern = _pattern(word)
                for number, line in enumerate(text.splitlines(), 1):
                    if pattern.search(line):
                        found.append(
                            Retired(
                                str(doc.relative_to(root)), number, word, retired[word]
                            )
                        )
        for source in _files(directory, root, CODE_SUFFIXES):
            text, low = read(source)
            governs |= present(compiled, text, low, governs)
        used[name] = {"consumes": sorted(consumes), "governs": sorted(governs)}
    return used, found
