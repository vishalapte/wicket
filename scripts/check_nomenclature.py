#!/usr/bin/env python3
"""check_nomenclature: the skill-to-term graph, derived from the term trees and checked.

`docs/nomenclature/` and `docs/vocabulary/` fix the words racecar uses, one page per
word. This script is what makes those trees load-bearing rather than aspirational: it
reads their frontmatter as DATA, scans every skill for which terms each one actually
uses, and reports a retired word wherever it still appears.

**The source is frontmatter, not prose.** It used to be two markdown tables, parsed with
a regex, and that parser silently skipped every row it could not read — five declared
rules, one loaded, and a green report the whole time. The lesson is not "write a better
regex". It is that a rule a machine must enforce should be stored where a machine cannot
misread it. A page declares `status:` and `instead:` or it does not; there is no cell to
mis-tokenise.

**One stored edge.** The only hand-written thing is the term pages themselves. Every
other relation is *derived* by scanning, including the reverse index — which skills use a
given term — which is derived from the forward one and never written down. Two
directions, one source: exactly the shape `DOC_GRAPH.md` prescribes for the doc graph,
applied to vocabulary (P-02, one home).

**Consuming and governing are both derived, from different corpora.** A skill *consumes*
a term when its prose says the word. A skill *governs* a term when its own code says it —
`ALWAYS_NOUNS = {"fleet"}` in the CLI audit is `arch-python` governing `fleet`, and it is
governance precisely because a checker will now act on the word rather than merely use
it. Neither belongs in frontmatter: a declared `governed_by:` would be a second copy of
something the code already states, free to drift from the code that actually enforces it,
and the first thing a reader would have to distrust.

**Why a retired word is a finding rather than a style note.** The cost of a renamed term
is not the rename, it is the period afterwards when both words are in the tree and a
reader cannot tell whether they name one thing or two. That period ends when a checker
closes it.

**A word a checker reserves must have a page.** `ALWAYS_NOUNS` in the CLI audit bars
`fleet` from every verb slot in the tree. A rule like that is enforced against code an
author never reads, so the only place they can find out why is the term tree — and a
reservation with no page is a rule that fires with no explanation attached. The list
lives in code because that is where it is applied; this checker reads it back and
requires each word in it to resolve to a page, which is the loop closed rather than a
second copy of the list.

**Retired is scanned; reserved is not.** A retired word is gone, so its appearance
anywhere is the finding and a text scan is exactly the right instrument. A reserved word
is legitimate in one sense and not in another, and a scanner sees words rather than
senses — pointing it at one would flag every correct use. Those route to the CLI audit,
which reads grammatical position and flag `dest` out of `parser()`, or to a reviewer.

The scan is word-level and case-insensitive for prose terms, and exact for flags, so
`--check` matches a flag and never the word "check". A term is credited to a skill when
it appears in any tracked `.md` under that skill's directory.

Exit codes are racecar's fixed vocabulary (`src/racecar/lib/_exit.py`):

    0  OK        no retired term in use, or no term tree in this repo
    1  FINDINGS  a retired term is still in the tree, or a reserved word has no page
    2  UNMET     a term tree exists but a page in it is unreadable

Usage:
    python3 check_nomenclature.py [--root <repo>] [--json] [--terms] [--skill <name>]

Complexity: O(F*D + T*L) -- F=files walked, D=avg path depth (_owned trie descent),
T=#terms, L=bytes of skill-owned doc/code text scanned per term (_needle prefilters the
constant, not the exponent).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from check_packaging_rules._root import find_repo_root

OK, FINDINGS, UNMET = 0, 1, 2

# The two term trees. `docs/vocabulary/` holds flags, whose page name omits the leading
# dashes (`--dry-run` is spelled `dry-run`, because the dashes are a construct of the
# command line and not part of the word); FLAG_TREE says which tree to put them back on
# when building the string to scan for.
NOUN_TREE = Path("docs") / "nomenclature"
FLAG_TREE = Path("docs") / "vocabulary"
TERM_TREES = (NOUN_TREE, FLAG_TREE)

STATUSES = ("reserved", "retired")

_FRONTMATTER = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.S)
_KEY = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):\s*(?P<value>.*?)\s*$")

# Directories this checker never grades. Every DOT-prefixed directory is skipped by rule
# (see `_skipped`); these two are the only ones that are not hidden and so cannot be
# caught that way.
#
# Nothing under a dot-directory is tracked by git, so the rule removes no committed
# content from scope.
SKIP_DIRS = {
    "__pycache__",
    # `drafts/` holds decision documents: what was argued, when, in the words used then.
    # Rewriting one to today's vocabulary would forge the record of how the vocabulary
    # got here. `check_foreign_names.py` skips it for the same reason.
    "drafts",
}


def _skipped(rel: Path) -> bool:
    """Whether a repo-relative path lies under something this checker ignores."""
    parts = rel.parts
    return any(p.startswith(".") for p in parts) or bool(set(parts) & SKIP_DIRS)


# The documents allowed to say a retired word, and the only ones. Two kinds:
#
#   - the trees that DECLARE the retirement, and the flag contract that argues it. A
#     rule that cannot name what it forbids is unstatable, and this is the same
#     exemption `check_foreign_names.py` grants the one file that must talk about its
#     boundary.
#   - the changelog and the decision log, which are RECORDS. They report what was true
#     at the time; editing an old entry to use today's word would make the history a lie
#     about itself. This is why a record is exempt and a generated page is not.
#
# Anything else naming a retired term is a finding, including a generated page — a stale
# generated page is regenerated, never exempted.
CITING_DOCS = {
    Path("arch-python") / "CLI.md",
    Path("CHANGELOG.md"),
}


class NomenclatureError(Exception):
    """A term page could not be read. Never a guess, never a partial answer."""


@dataclass(frozen=True)
class Term:
    """One controlled word, as its page declares it."""

    word: str
    page: str
    status: str
    instead: str


@dataclass(frozen=True)
class Finding:
    """One retired word still in the tree."""

    path: str
    line: int
    retired: str
    replacement: str

    def render(self) -> str:
        """Format as one audit line, matching racecar's other checkers."""
        return (
            f"  Major    {self.path}:{self.line}  retired term "
            f"{self.retired!r} — use {self.replacement!r}"
        )


def reserved_by_code(root: Path) -> frozenset[str]:
    """The words a checker bars from a slot, read back from the checker that bars them.

    Read rather than restated: a copy of `ALWAYS_NOUNS` here would be a second home for
    the list, and the copy that drifts is always the one not doing the enforcing.
    Returns empty when the CLI audit is absent, which is the case in an adopter.
    """
    audit = root / "scripts" / "check_cli_commands.py"
    if not audit.is_file():
        return frozenset()
    sys.path.insert(0, str(audit.parent))
    try:
        import check_cli_commands  # pylint: disable=import-outside-toplevel

        return frozenset(check_cli_commands.ALWAYS_NOUNS)
    except (ImportError, AttributeError) as err:
        raise NomenclatureError(
            f"cannot read ALWAYS_NOUNS from {audit}: {err}"
        ) from err
    finally:
        sys.path.remove(str(audit.parent))


def undefined_reservations(root: Path, terms: list[Term]) -> list[str]:
    """Words the code reserves that no page defines. Each is a rule with no explanation."""
    defined = {t.word for t in terms}
    return sorted(word for word in reserved_by_code(root) if word not in defined)


def find_root(start: Path | None = None) -> Path:
    """The repo root — STRICTLY, unlike the shared walk-up it wraps.

    Keyed on the repo rather than on a term tree because this script is delivered to
    adopters, and an adopter has no controlled vocabulary of its own. Finding the root
    must succeed there; finding nothing to check is then a legitimate answer rather than
    a failure to locate the repo.

    So the strictness lives HERE, at one visible line, rather than in a private variant
    of the walk-up: `find_repo_root` returns its starting point when there is no `.git`,
    and that fallback is exactly the "broken run" this script refuses to make silent.
    """
    root = find_repo_root(start)
    if not (root / ".git").exists():
        raise NomenclatureError(f"no repo root (no .git) at or above {root}")
    return root


def frontmatter(text: str) -> dict[str, str]:
    """Return a page's frontmatter as scalars. A page without any is not a term page."""
    block = _FRONTMATTER.match(text)
    if not block:
        return {}
    found: dict[str, str] = {}
    for raw in block.group("body").splitlines():
        pair = _KEY.match(raw)
        if pair:
            found[pair.group("key")] = pair.group("value").strip().strip("\"'")
    return found


def read_terms(root: Path) -> list[Term]:
    """Every term page in either tree, as declared. Empty when the repo has no trees.

    A page that declares `status:` must also declare `instead:`, and vice versa: a
    retirement with no replacement tells a reader what to stop writing and not what to
    write, and a replacement with no status is a rule that never fires. Both are hard
    errors rather than skipped pages, for the reason this whole file exists.
    """
    terms: list[Term] = []
    for tree in TERM_TREES:
        directory = root / tree
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            if path.name == "README.md":
                continue
            where = str(path.relative_to(root))
            try:
                raw = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise NomenclatureError(f"{where}: not valid UTF-8 ({exc})") from exc
            meta = frontmatter(raw)
            name = meta.get("name")
            if not name:
                raise NomenclatureError(f"{where}: term page declares no `name`")
            status, instead = meta.get("status", ""), meta.get("instead", "")
            if status and status not in STATUSES:
                raise NomenclatureError(
                    f"{where}: status {status!r} is not one of {STATUSES}. Absence "
                    "means canon; only an exception is declared."
                )
            if bool(status) != bool(instead):
                raise NomenclatureError(
                    f"{where}: `status` and `instead` must be declared together "
                    f"(status={status!r}, instead={instead!r}). A retirement with no "
                    "replacement says what to stop writing but not what to write; a "
                    "replacement with no status is a rule that never fires."
                )
            word = f"--{name}" if tree == FLAG_TREE else name
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
    # Each word is escaped SEPARATELY and rejoined on `\s+`. Escaping the whole term
    # after substituting the separator would escape the backslash in `\s+` and the
    # pattern would then only match a literal `\s+` — which is to say a multi-word term
    # would silently never match, and its retirement would be unenforced while reading
    # as enforced.
    spaced = r"\s+".join(re.escape(word) for word in term.split())
    return re.compile(r"(?<![\w-])" + spaced + r"(?![\w-])", re.I)


def _needle(term: str) -> str:
    r"""The lowercase literal that MUST appear in a text for `_pattern(term)` to match.

    A necessary condition, not a sufficient one — which is the whole point. `_pattern`
    wraps the term in lookarounds and joins multi-word terms on `\s+`, so the first word
    always appears verbatim; a flag appears whole. Testing `needle in text.lower()`
    therefore never rejects a real match, and rejects almost every non-match for the price
    of a C substring scan instead of a regex pass over the file.

    This is the difference between 82,741 regex searches and roughly 2,000. Every term was
    being run against every file, and the regex engine cannot know that `telemetry` is
    absent without walking the text. `str.__contains__` can.
    """
    return term.split()[0].lower()


def _walk(root: Path) -> list[Path]:
    """Every file under `root`, PRUNING skipped directories as it descends.

    `rglob` cannot prune: it walks `.venv` and `.git` in full and leaves the caller to
    discard the results afterwards, so the cost of ignoring a directory is the cost of
    reading it. `dirs[:] = ...` is the pruning; mutating it in place is what stops the
    descent. Cached because several callers ask the same question.
    """
    if root in _WALK_CACHE:
        return _WALK_CACHE[root]
    out: list[Path] = []
    for parent, dirs, files in os.walk(root):
        here = Path(parent)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        out.extend(here / f for f in files)
    _WALK_CACHE[root] = out
    return out


_WALK_CACHE: dict[Path, list[Path]] = {}
_SKILL_CACHE: dict[Path, list[Path]] = {}


def skill_dirs(root: Path) -> list[Path]:
    """Every directory carrying a SKILL.md, sorted. That is what a skill IS on disk."""
    if root not in _SKILL_CACHE:
        _SKILL_CACHE[root] = sorted(
            {p.parent for p in _walk(root) if p.name == "SKILL.md"}
        )
    return _SKILL_CACHE[root]


# What a skill is made of, split by what naming a term in it MEANS. Prose that says a
# word uses it; code that says a word acts on it.
DOC_SUFFIXES = (".md",)
CODE_SUFFIXES = (".py", ".sh", ".mk")


_OWNED_CACHE: dict[Path, dict[Path, list[Path]]] = {}


class _TrieNode:
    """One path segment's worth of trie. `skill` is set only where a skill dir ends."""

    __slots__ = ("children", "skill")

    def __init__(self) -> None:
        self.children: dict[str, "_TrieNode"] = {}
        self.skill: Path | None = None


def _owned(root: Path) -> dict[Path, list[Path]]:
    """Every skill directory to the files it OWNS: its own tree, not its children's skills.

    Ownership is "the deepest skill directory above this file", which is a property of the
    file and is therefore answered once for the whole tree. Asking it the other way round --
    per skill, filter the walk, and for each file test `any(other in file.parents)` -- is the
    same question asked `skills x files x nested-skills` times, and it was the second-largest
    cost in this checker: 263,326 pathlib calls to place roughly 1,100 files.

    Files under no skill directory belong to nobody and are not returned, which is what the
    per-skill filter did too.

    This is longest-prefix match, the same rule an IP routing table applies to pick its
    most specific route -- and it is now the real, trie-backed form of that rule, not the
    naive one. A skill directory's `.parts` are inserted as one path down a trie keyed by
    path segment, terminal nodes marked with the skill they close; placing a file walks its
    OWN `.parts` down that same trie, remembering the deepest skill-marked node crossed.
    That is a single O(depth) descent per file, not a scan of every directory: this is what
    replaced the flat prefix list that made placing one file cost up to `len(directories)`
    string comparisons -- the O(files x skills) `#51` filed, independent of how many
    directories the file's own path does not even pass through.
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
    owned: dict[Path, list[Path]] = {d: [] for d in directories}
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
            owned[deepest].append(path)
    _OWNED_CACHE[root] = owned
    return owned


def _files(directory: Path, root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """A skill's own files of the given kinds: its own tree, not its children's skills."""
    return sorted(p for p in _owned(root).get(directory, []) if p.suffix in suffixes)


def _docs(directory: Path, root: Path) -> list[Path]:
    """The markdown a skill is made of."""
    return _files(directory, root, DOC_SUFFIXES)


def _exempt(relative: Path) -> bool:
    """Whether a document may name a retired word: it declares one, or it records one."""
    return relative in CITING_DOCS or any(
        tree == relative or tree in relative.parents for tree in TERM_TREES
    )


def scan(
    root: Path, terms: list[Term], retired: dict[str, str]
) -> tuple[dict[str, dict[str, list[str]]], list[Finding]]:
    """Return `(skill -> {consumes, governs}, findings)`. Reverse indexes derive later."""
    compiled = [(t.word, _needle(t.word), _pattern(t.word)) for t in terms]
    condemned = [(w, _needle(w), _pattern(w)) for w in retired]
    used: dict[str, dict[str, list[str]]] = {}
    findings: list[Finding] = []
    # One read per file. A skill's tree overlaps its neighbours' only through shared
    # parents, but the same file is reached by several relations below, and reading it
    # twice to answer two questions about the same bytes is the cost this cache removes.
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

        `found` is not an optimisation detail leaking out -- the caller is accumulating a
        SET, so a term already in it cannot change the answer no matter how many more files
        name it. Skipping those is the difference between testing every term against every
        file and testing each term until it lands, and the common terms land in the first
        file of a skill.
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
            if _exempt(doc.relative_to(root)):
                continue
            # Only a term the whole document names can be on one of its lines, so the
            # per-line walk runs for the retired words that are actually here -- which is
            # normally none of them, and the loop does not run at all.
            for word in present(condemned, text, low):
                pattern = _pattern(word)
                for number, line in enumerate(text.splitlines(), 1):
                    if pattern.search(line):
                        findings.append(
                            Finding(
                                str(doc.relative_to(root)), number, word, retired[word]
                            )
                        )
        for source in _files(directory, root, CODE_SUFFIXES):
            text, low = read(source)
            governs |= present(compiled, text, low, governs)
        used[name] = {"consumes": sorted(consumes), "governs": sorted(governs)}
    return used, findings


def reverse(
    used: dict[str, dict[str, list[str]]], relation: str = "consumes"
) -> dict[str, list[str]]:
    """Invert one relation: term -> the skills in it. Derived, never stored."""
    index: dict[str, list[str]] = {}
    for skill, relations in used.items():
        for term in relations[relation]:
            index.setdefault(term, []).append(skill)
    return {term: sorted(skills) for term, skills in sorted(index.items())}


def _report(
    terms: list[Term],
    used: dict[str, dict[str, list[str]]],
    findings: list[Finding],
    undefined: list[str],
) -> int:
    """Print the human report and return the exit code."""
    for finding in findings:
        print(finding.render())
    for word in undefined:
        print(
            f"  Major    scripts/check_cli_commands.py  reserved word "
            f"{word!r} has no page under {NOUN_TREE} — a rule that fires with no "
            "explanation attached"
        )
    consumed, governed = reverse(used), reverse(used, "governs")
    orphans = [t.word for t in terms if t.word not in consumed]
    if orphans:
        print(
            f"check_nomenclature: info: {len(orphans)} term(s) used by no skill "
            f"({', '.join(sorted(orphans))}) — advisory, a term may be canon before it "
            "is cited"
        )
    if findings or undefined:
        parts = []
        if findings:
            parts.append(f"{len(findings)} retired term(s) in use")
        if undefined:
            parts.append(f"{len(undefined)} reserved word(s) undefined")
        print(f"check_nomenclature: {', '.join(parts)}")
        return FINDINGS
    retired = sum(1 for t in terms if t.status == "retired")
    reserved = sum(1 for t in terms if t.status == "reserved")
    print(
        f"check_nomenclature: OK ({len(terms)} terms, {retired} retired, "
        f"{reserved} reserved — reserved route to the CLI audit; "
        f"{len(governed)} term(s) governed by a checker)"
    )
    return OK


def main(argv: list[str] | None = None) -> int:
    """Report retired terms; `--json` emits the graph, `--terms` the reverse index."""
    parser = argparse.ArgumentParser(
        prog="check_nomenclature.py", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--root", type=Path, default=None, help="the repo to scan")
    parser.add_argument("--skill", default=None, help="narrow the report to one skill")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="emit the graph as JSON")
    mode.add_argument(
        "--terms",
        action="store_true",
        help="print term -> skills, the derived direction",
    )
    args = parser.parse_args(argv)

    try:
        root = args.root.resolve() if args.root else find_root()
        terms = read_terms(root)
    except NomenclatureError as err:
        print(f"check_nomenclature: cannot run — {err}", file=sys.stderr)
        return UNMET

    if not terms:
        # An adopter carries racecar's checkers but not racecar's vocabulary. Nothing to
        # check is a legitimate answer, and reporting it is how a reader tells that
        # result apart from a checker that ran and found nothing wrong.
        print(f"check_nomenclature: no term tree in {root} — nothing to check")
        return OK

    try:
        undefined = undefined_reservations(root, terms)
    except NomenclatureError as err:
        print(f"check_nomenclature: cannot run — {err}", file=sys.stderr)
        return UNMET

    used, findings = scan(root, terms, retirements(terms))

    if args.skill:
        used = {k: v for k, v in used.items() if k == args.skill}
        findings = [f for f in findings if f.path.startswith(args.skill)]

    if args.json:
        print(
            json.dumps(
                {
                    "terms": {t.word: t.page for t in terms},
                    "retired": retirements(terms),
                    "reserved": {
                        t.word: t.instead for t in terms if t.status == "reserved"
                    },
                    "skill_to_terms": used,
                    "term_to_skills": reverse(used),
                    "term_to_governors": reverse(used, "governs"),
                },
                indent=2,
            )
        )
        return OK

    if args.terms:
        consumed, governed = reverse(used), reverse(used, "governs")
        for term in sorted(t.word for t in terms):
            skills = consumed.get(term, [])
            where = ", ".join(skills) if skills else "(unused)"
            by = governed.get(term, [])
            rule = f"  [governed by {', '.join(by)}]" if by else ""
            print(f"  {term:24s}  {where}{rule}")
        return OK

    return _report(terms, used, findings, undefined)


if __name__ == "__main__":
    sys.exit(main())
