#!/usr/bin/env python3
"""check_vocabulary: hold a repo's flags to what the vocabulary canon says they are.

`docs/vocabulary/<flag>.md` carries one page per flag: the argument a table has no room
for, plus a `type` claim a machine can check. This script is what makes that claim a
claim rather than a note — it reads the CLI audit's extracted `type`, `action` and
`choices` for every flag the repo declares, and reports a flag whose declaration
disagrees with its page.

**The canon lives in racecar, and this script goes to it.** The trees are racecar's
one home for the words (P-02): `docs/vocabulary/README.md` says canon fixes spellings
*for adopters to use*, which is a statement about racecar's tree governing other repos'
code. A delivered checker that read `<adopter>/docs/vocabulary/` would therefore be
asking every adopter to keep a second copy of canon — the exact drift the tree exists to
prevent — and, until this was fixed, it did worse than that: it located the repo by
walking up for `docs/nomenclature/`, so in a repo without the trees it did not run at
all. `make docs` hard-failed in every adopter, on a check whose subject was the
adopter's own code. The root is now `.git` like every other delivered checker's, and the
canon is resolved separately:

    --canon <dir>              explicit; a directory that carries no tree is an error
    $RACECAR_ROOT              how `racecar.mk` passes the installed checkout through
    ~/.claude/skills/racecar   the skill symlink, the same rule `racecar.mk` uses
    the repo itself            racecar's own case, where root and canon coincide

No canon and no local tree is a legitimate answer — nothing to check, exit OK — and it
is reported as such so a reader can tell it apart from a check that ran and passed.

**A repo may extend canon, and may not contradict it.** A `docs/vocabulary/` in the repo
under check is read as well: a flag local to one project is still worth an argument. A
local page that names a canon flag with a different `type` is a finding, because two
homes disagreeing about one word is the condition the tree exists to end. Changing what
canon says is an escalation to racecar (`upgrade/README.md`), never a local override.

**Where a flag is documented but not declared, the page is accepted.** Canon fixes
spellings for adopters to use, and a spelling this repo has no occasion for is still
canon. The check is the overlap: where a page and the code both speak, they must agree.

**The noun pages are not checked here, and that is the point.** `status` used to be
validated against two tables in a term home; there is no term home now and no tables, so
the pages are the source rather than a copy of one. A check that a thing agrees with
itself is not a check. What remains — that `status` and `instead` are well-formed and
declared together — is `check_nomenclature.py`'s, which hard-fails on a page it cannot
read because it is the one that enforces the result.

**What is deliberately not checked.** Whether the prose is any good, and whether a
semantic type like `date` is really a date: argparse sees a parsing callable, not an
intent, so this can confirm `date` is not `boolean` and stops there. Claiming more
precision than the audit carries would be the same defect this script exists to catch.

**The index tables are an authoring duty, not an adopter's gate.** A tree's `README.md`
index is derived from its pages and regenerated with `--write`, and it is checked for
exactly one reason: the README carries the INDEX markers, so it has published a derived
table and must keep it true. A tree the repo does not carry, or one whose README has no
block, is not held to anything. An adopter that keeps three extension pages is not
thereby signed up to maintain a generated table.

Exit codes are racecar's fixed vocabulary (`src/racecar/lib/_exit.py`):

    0  OK        every page agrees with the tree, or there is no vocabulary to check
    1  FINDINGS  a page disagrees with the code, or with canon
    2  UNMET     the canon named does not exist, or the CLI audit could not run

Usage:
    python3 check_vocabulary.py [--root <repo>] [--canon <racecar>] [--write]

Complexity: O(P log P + F)
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from check_packaging_rules._files import repo_files
from check_packaging_rules._root import find_repo_root, same_repository

OK, FINDINGS, UNMET = 0, 1, 2

FLAG_DIR = Path("docs") / "vocabulary"
NOUN_DIR = Path("docs") / "nomenclature"

# Where an installed racecar is found when nothing points at one. Same rule as
# `racecar.mk`: the skill symlink is the install, so resolving it here means a repo that
# has racecar installed at all gets the canon check without configuring anything.
CANON_ENV = "RACECAR_ROOT"
SKILL_LINK = Path.home() / ".claude" / "skills" / "racecar"

# Trees a `__main__.py` may sit in without meaning the repo has a CLI.
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".telemetry",
    ".collections",
}

# Doc-facing type names, and the argparse evidence each one is consistent with. The
# audit reports the parser's own `type` callable, so the mapping is by what argparse
# can actually distinguish rather than by what the word means to a reader.
#
# `date`, `datetime` and `string` all arrive as a str-parsing callable or as no type at
# all, so they are one bucket here: what this can prove about them is that they are NOT
# boolean, path, integer or an enum. That is a real check — a date flag declared
# `store_true` is a genuine bug — and claiming to verify more would be inventing
# precision the audit does not carry.
_SCALARS = {"date", "datetime", "string"}
_EXPECTED: dict[str, set[str]] = {
    "boolean": {"bool"},
    "path": {"Path"},
    "integer": {"int"},
    # A flag declared with `choices=` is a closed set, which is a different promise from
    # a free string: the CLI will refuse anything outside it. The audit can see that, so
    # the page can be held to it.
    "enum": {"enum"},
}
# The kinds a scalar claim can be refuted by. A claim of `date` is provable only in the
# negative — "not one of the kinds argparse distinguishes" — so this is the whole of it.
_DISTINGUISHABLE = {"bool", "Path", "int", "enum"}


class VocabularyError(Exception):
    """A canon or an audit could not be read. Never a guess, never a vacuous pass."""


@dataclass(frozen=True)
class Page:
    """One flag page, from whichever tree declared it."""

    where: str
    name: str
    kind: str
    canon: bool


def find_root(start: Path | None = None) -> Path:
    """The repo under check — STRICTLY, unlike the shared walk-up it wraps.

    Keyed on the repo rather than on a term tree, for the reason `check_nomenclature.py`
    states in the same words: this script is delivered to adopters, and an adopter has no
    vocabulary tree of its own. Locating the repo must succeed there — finding nothing to
    check is then an answer, where failing to find the root is a broken run. Keying on
    the tree also let the walk leave the repo entirely: a checkout nested under any
    directory that happened to carry `docs/nomenclature/` was checked against that.

    So the strictness lives HERE, at one visible line, rather than in a private variant
    of the walk-up: `find_repo_root` returns its starting point when there is no `.git`,
    and that fallback is exactly the "broken run" this script refuses to make silent.
    """
    root = find_repo_root(start)
    if not (root / ".git").exists():
        raise VocabularyError(f"no repo root (no .git) at or above {root}")
    return root


def find_canon(root: Path, explicit: Path | None = None) -> Path | None:
    """The racecar checkout carrying the flag tree, or None when there is none.

    An explicit `--canon` that carries no tree is an error rather than a fallback: a
    caller who names a directory is asserting the canon is there, and quietly checking
    something else is how a run reports OK about a tree it never read.

    A canon that is ANOTHER CHECKOUT OF THIS REPOSITORY resolves to `root` itself. A git
    worktree is the case that makes this necessary: `RACECAR_ROOT` names the main
    checkout, so canon and root were two paths holding two versions of one tree, and
    comparing them by path made the branch an adopter extending racecar. Every page was
    then read twice, and a branch that EDITED the vocabulary was told its page
    `contradicts canon ... take the disagreement to racecar` -- advice with nowhere to
    go, since the branch is racecar's. Canon is the tree under test there, not the copy
    the other checkout happens to be holding.
    """
    if explicit is not None:
        resolved = explicit.resolve()
        if not (resolved / FLAG_DIR).is_dir():
            raise VocabularyError(f"--canon {resolved} carries no {FLAG_DIR}")
        return root.resolve() if same_repository(resolved, root) else resolved
    environment = os.environ.get(CANON_ENV, "").strip()
    for candidate in (
        Path(environment) if environment else None,
        SKILL_LINK,
        root,
    ):
        if candidate is not None and (candidate / FLAG_DIR).is_dir():
            if same_repository(candidate, root):
                return root.resolve()
            return candidate.resolve()
    return None


def frontmatter(text: str) -> dict[str, str]:
    """Read the top-level scalars from a page's frontmatter block."""
    if not text.startswith("---\n"):
        return {}
    _, _, rest = text.partition("---\n")
    block, marker, _ = rest.partition("\n---")
    if not marker:
        return {}
    out: dict[str, str] = {}
    for line in block.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.strip()[0].isspace():
            out[key.strip()] = value.strip().strip("\"'")
    return out


_PAGES_CACHE: dict[tuple[Path, Path], list[tuple[Path, dict[str, str]]]] = {}


def pages(base: Path, subdir: Path) -> list[tuple[Path, dict[str, str]]]:
    """Every term page under `base/subdir` with its frontmatter, index excluded.

    Empty when the tree is absent, which is what most repos look like. A README at any
    depth is an index, not a term. The noun tree nests — the three chain layers live
    under `repo/` because they are kinds of repo — so this walks the subtree.

    Memoization (Michie, 1968), cached by `(base, subdir)`: `#54` filed this as one run's
    `main()` reading and re-parsing frontmatter for the same pair up to five times across `main()`,
    `flag_pages()`, and `check_index()`'s own two call sites (the emptiness guard, then
    again inside `index_body()`). NOT invalidated on a plain re-read, only on a write:
    `check_index(..., write=True)` regenerates a tree's README, and the write path
    drops that `(root, subdir)`'s cache entry (`_PAGES_CACHE.pop`, below) rather than
    trusting that today's write target (README.md, which this function already excludes)
    can never overlap what `pages()` returns -- a blind cache with no invalidation path
    at all would be one future change to what `--write` touches away from serving a
    second `check_index()` call in the same process its pre-write answer.
    """
    key = (base, subdir)
    if key in _PAGES_CACHE:
        return _PAGES_CACHE[key]
    directory = base / subdir
    if not directory.is_dir():
        result: list[tuple[Path, dict[str, str]]] = []
    else:
        result = [
            (path, frontmatter(path.read_text(encoding="utf-8")))
            for path in sorted(directory.rglob("*.md"))
            if path.name != "README.md"
        ]
    _PAGES_CACHE[key] = result
    return result


def flag_pages(root: Path, canon: Path | None) -> list[Page]:
    """Canon's flag pages, then this repo's own — canon first, so it reads as the rule.

    In racecar itself the two are one REPOSITORY and each page appears once, as canon: a
    repo listed as both its own canon and its own extension would be checked twice and
    could contradict itself, which is not a state a single tree can be in.

    Same repository, not same path. A git worktree of racecar is a second checkout of
    the one repository at a different directory, and a path comparison called it an
    adopter extending canon — so a branch that EDITS the vocabulary was told its page
    `contradicts canon ... take the disagreement to racecar`, from inside racecar. Every
    page was also counted twice, which the OK line reported as a doubled total.
    """
    found: list[Page] = []
    if canon is not None:
        found += [
            Page(
                where=(
                    str(path.relative_to(canon))
                    if canon == root
                    else f"{path.relative_to(canon).as_posix()} (canon)"
                ),
                name=meta.get("name", ""),
                kind=meta.get("type", ""),
                canon=True,
            )
            for path, meta in pages(canon, FLAG_DIR)
        ]
    if canon != root:
        found += [
            Page(
                where=str(path.relative_to(root)),
                name=meta.get("name", ""),
                kind=meta.get("type", ""),
                canon=False,
            )
            for path, meta in pages(root, FLAG_DIR)
        ]
    return found


def has_cli(root: Path) -> bool:
    """Whether this repo declares a CLI at all — the marker is `__main__.py` (§3).

    A repo with no CLI truthfully declares no flags, and an audit returning nothing is
    then the correct answer rather than a broken run. Distinguishing the two is what
    keeps the empty-result guard honest in both directions.
    """
    return any(
        not (set(path.relative_to(root).parts) & SKIP_DIRS)
        for path in repo_files(root, "__main__.py")
    )


def _audit_module(root: Path) -> ModuleType:
    """Import the CLI audit from wherever this repo keeps it, as a module private to
    THIS root.

    Racecar and a synced repo both keep it flat in `scripts/` alongside this file, so
    there is one layout to search rather than two. This file's own directory is tried as
    well, which covers being imported by a test rather than run as a script. The repo
    root goes on the path too: a `src/` layout resolves as a namespace package, so its
    nodes import as `src.<pkg>` and that only works with the root itself importable.

    Loaded via `importlib.util` under a name keyed on `root`, never via a bare `import
    check_cli_commands` — `sys.modules` caches by name with no per-repo key, so a second
    call against a DIFFERENT repo would otherwise silently return the first repo's
    already-imported module object (issue #59). `root` stays on `sys.path` for the
    returned module's whole useful life: `audit_cli_tree()` does its own imports lazily,
    during the CALLER's later use of the module, not during this load -- removing `root`
    here would resolve those against a path the caller no longer has. Inserted only once
    per root (checked, not appended blindly), so repeated calls for the SAME root do not
    grow `sys.path` without bound; a genuinely new root each call is exactly the
    unbounded case this is not trying to solve, and does not arise in practice (a
    process audits a small, fixed set of repos, never one root per call in a loop).
    """
    here = Path(__file__).resolve().parent
    for directory in (root / "scripts", here):
        source = directory / "check_cli_commands.py"
        if source.is_file():
            name = f"_check_vocabulary_audit__{abs(hash(str(root)))}"
            spec = importlib.util.spec_from_file_location(name, source)
            if spec is None or spec.loader is None:
                raise VocabularyError(f"cannot import the CLI audit from {directory}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            try:
                spec.loader.exec_module(module)
                return module
            except ImportError as err:
                raise VocabularyError(
                    f"cannot import the CLI audit from {directory}: {err}"
                ) from err
            finally:
                sys.modules.pop(name, None)
    raise VocabularyError(
        "no check_cli_commands.py under scripts/ — the flag "
        "check reads the CLI audit rather than re-walking the tree, and cannot "
        "substitute a second opinion about what the CLI declares"
    )


def declared_flag_types(root: Path) -> dict[str, set[str]]:
    """Map each flag spelling to the argparse types this repo declares it with.

    Reuses the CLI audit rather than re-walking the tree: it already imports every node's
    `parser()` and extracts the argument surface, and a second walk here would be a
    second opinion about what the CLI declares.
    """
    audit = _audit_module(root)
    seen: dict[str, set[str]] = {}

    def walk(node: dict[str, Any]) -> None:
        for group in (
            node.get("args") or [],
            *[s.get("args") or [] for s in (node.get("subcommands") or [])],
        ):
            for arg in group:
                for flag in arg.get("flags") or []:
                    kind = arg.get("type") or ("enum" if arg.get("choices") else "str")
                    seen.setdefault(flag, set()).add(kind)
        for child in node.get("children") or []:
            walk(child)

    # The audit resolves its root relative to the working directory. Handing it an
    # absolute path returns a tree with no children, so every flag would silently have
    # no declaration to disagree with and the whole check would pass vacuously — the
    # exact failure this file exists to prevent. Chdir and pass the relative name.
    previous = Path.cwd()
    try:
        os.chdir(root)
        walk(audit.audit_cli_tree("src" if (root / "src").is_dir() else "."))
    # The audit imports the repo's own code; a failure there is data, not a crash here.
    except Exception as err:
        raise VocabularyError(f"the CLI audit did not complete: {err}") from err
    finally:
        os.chdir(previous)
    if not seen:
        raise VocabularyError(
            "the CLI audit reported no flags, but this repo declares a `__main__.py`. "
            "Every page would then have nothing to disagree with and this check would "
            "pass while verifying nothing, so an empty result from a repo that has a "
            "CLI is treated as a broken run rather than a clean one."
        )
    return seen


def _well_formed(page: Page) -> str | None:
    """The page's own frontmatter, before anything is compared to the code."""
    if not page.name or page.name.startswith("-"):
        return (
            f"{page.where}: frontmatter `name` is the flag's NAME and carries no leading "
            "dashes — `dry-run`, not `--dry-run`. The `--` is the long-option construct, "
            "added when the name is spelled on a command line; argparse agrees, since "
            "`dest` is `dry_run`."
        )
    if page.kind not in _EXPECTED and page.kind not in _SCALARS:
        return (
            f"{page.where}: `type: {page.kind}` is not one of "
            f"{', '.join(sorted(set(_EXPECTED) | _SCALARS))}"
        )
    return None


def check_flags(root: Path, canon: Path | None) -> list[str]:
    """Every flag page declares a type this repo's own declarations agree with.

    A repo with no `__main__.py` declares no flags, so the audit is not run at all: it
    resolves a package root, and pointing it at a repo that has none fails with an import
    error that says nothing about vocabulary. The pages are still read — a malformed page
    is malformed wherever it sits — and there is simply nothing for them to disagree with.
    """
    declared = declared_flag_types(root) if has_cli(root) else {}
    findings: list[str] = []
    canonical: dict[str, Page] = {}
    for page in flag_pages(root, canon):
        malformed = _well_formed(page)
        if malformed:
            findings.append(malformed)
            continue
        if page.canon:
            canonical[page.name] = page
        else:
            fixed = canonical.get(page.name)
            if fixed and fixed.kind != page.kind:
                findings.append(
                    f"{page.where}: `type: {page.kind}` contradicts canon, which "
                    f"declares `{fixed.kind}` in {fixed.where}. A local page may extend "
                    "the vocabulary and may not redefine it — take the disagreement to "
                    "racecar rather than overriding it here."
                )
                continue
        spelling = f"--{page.name}"
        actual = declared.get(spelling)
        if actual is None:
            continue  # documented for adopters; this repo has no occasion for it
        expected = _EXPECTED.get(page.kind)
        wrong = actual & _DISTINGUISHABLE if expected is None else set()
        if expected is None and wrong:
            findings.append(
                f"{page.where}: documented `type: {page.kind}` but this repo declares "
                f"{spelling} as {sorted(wrong)}"
            )
        elif expected is not None and not actual & expected:
            findings.append(
                f"{page.where}: documented `type: {page.kind}` but this repo declares "
                f"{spelling} as {sorted(actual)}"
            )
    return findings


ROW = "| [{label}]({href}) | {second} | {gloss} |"


def kind_of(path: Path, subdir_root: Path) -> str:
    """A noun's kind is the directory it sits in. Derived, never declared.

    `docs/nomenclature/<kind>/<word>.md` — so `population/fleet.md` is a population and
    `repo/control.md` is a repo. The path is the one statement of the fact, which means
    a page cannot contradict its own filing the way a frontmatter field could. Moving a
    page between directories reclassifies it, correctly and without an edit.
    """
    rel = path.relative_to(subdir_root)
    return rel.parts[0] if len(rel.parts) > 1 else "—"


def index_body(root: Path, subdir: Path, second_key: str) -> str:
    """Render a tree's index table from its pages' frontmatter.

    DERIVED, never stored. `DOC_GRAPH.md` forbids writing down what the graph already
    encodes — children are the inverse of `pnode` and are computed by scanning — and a
    hand-kept table proves the rule the moment a page moves: two renames in one sitting
    left this one pointing at three files that no longer existed.

    The gloss comes from each page's own `summary`, so a page owns its one-line
    description in the same place it owns its argument.
    """
    rows = []
    for path, meta in sorted(
        pages(root, subdir), key=lambda pm: (kind_of(pm[0], root / subdir), pm[0].stem)
    ):
        href = path.relative_to(root / subdir).as_posix()
        second = (
            kind_of(path, root / subdir)
            if second_key == "kind"
            else meta.get(second_key, "?")
        )
        rows.append(
            ROW.format(
                label=meta.get("name", path.stem),
                href=href,
                second=second,
                gloss=meta.get("summary", ""),
            )
        )
    return "\n".join(rows)


def check_index(root: Path, subdir: Path, second_key: str, write: bool) -> list[str]:
    """Hold a tree's index table to its pages, or rewrite it when `write`.

    The duty is the block, and nothing else: a tree whose README carries the INDEX
    markers has published a derived table and must keep it true, and a tree without them
    has not. Requiring the block would conscript every adopter that keeps two extension
    pages into maintaining a generated table — a duty racecar has and they do not.
    Deleting racecar's own markers is not a hole this needs to plug: every page declares
    the tree README as its `pnode`, so a README that stops existing is
    `check_doc_graph.py`'s finding, stated once where that rule lives.

    The check-vs-write split exploits idempotence: `want` (the regenerated form) is
    computed once and reused both to detect drift (`rebuilt == text`) and, when `write`,
    as the thing actually written -- the same shape `black --check` / `gofmt -l` /
    `terraform fmt -check` use, never a second derivation that could disagree with the
    first.
    """
    directory = root / subdir
    if not directory.is_dir() or not pages(root, subdir):
        return []
    readme = directory / "README.md"
    if not readme.is_file():
        return []
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- BEGIN INDEX -->", "<!-- END INDEX -->"
    if start not in text or end not in text:
        return []
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    header = f"| term | {second_key} | in one line |\n|---|---|---|"
    body = index_body(root, subdir, second_key)
    want = f"{start}\n\n{header}\n{body}\n\n{end}"
    rebuilt = head + want + tail
    if rebuilt == text:
        return []
    if write:
        readme.write_text(rebuilt, encoding="utf-8")
        _PAGES_CACHE.pop((root, subdir), None)
        return []
    return [
        f"{readme.relative_to(root)}: index is stale against the pages. "
        "Regenerate with `check_vocabulary.py --write`; it is derived, not written."
    ]


def main(argv: list[str] | None = None) -> int:
    """Check this repo's flags against the vocabulary canon, and any local index."""
    parser = argparse.ArgumentParser(
        prog="check_vocabulary.py", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--root", type=Path, default=None, help="the repo to check")
    parser.add_argument(
        "--canon",
        type=Path,
        default=None,
        help=f"the racecar checkout holding {FLAG_DIR} (default: ${CANON_ENV}, "
        "the installed skill, or this repo)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate a local tree's index table from its pages",
    )
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve() if args.root else find_root()
        canon = find_canon(root, args.canon)
        local = pages(root, FLAG_DIR)
        if canon is None and not local:
            print(
                f"check_vocabulary: no flag vocabulary for {root} — nothing to check "
                f"(no {FLAG_DIR} here, and no racecar checkout in ${CANON_ENV} or "
                f"{SKILL_LINK})"
            )
            return OK
        findings = (
            check_flags(root, canon)
            + check_index(root, FLAG_DIR, "type", args.write)
            + check_index(root, NOUN_DIR, "kind", args.write)
        )
    except VocabularyError as err:
        print(f"check_vocabulary: cannot run — {err}", file=sys.stderr)
        return UNMET

    for finding in findings:
        print(f"  Major    {finding}")
    if findings:
        print(f"check_vocabulary: {len(findings)} page(s) disagree with the tree")
        return FINDINGS
    where = "this repo" if canon == root else canon
    against = "" if has_cli(root) else ", form only — this repo declares no CLI"
    print(
        f"check_vocabulary: OK ({len(flag_pages(root, canon))} flag pages "
        f"(canon: {where}{against}), {len(pages(root, NOUN_DIR))} noun pages)"
    )
    return OK


if __name__ == "__main__":
    sys.exit(main())
