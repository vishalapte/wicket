#!/usr/bin/env python3
"""Move a bump from one version number to another, across every artifact that must agree.

shared/COMMITS.md spreads a single bump over four places, and they are only correct
together:

  1. the **version home** -- `[project].version`, else a root `VERSION` file
     (COMMITS.md "Version home");
  2. the **`Bump version to X.Y.Z.` footer** in that commit's message
     (COMMITS.md "Version bumps in commits");
  3. the **`## X.Y.Z - DATE` changelog heading** the bump opens
     (COMMITS.md "One changelog section per bump");
  4. the **brief**: a bundle with a generated `*-INVENTORY.md` has that file regenerated
     by `check_brief.py --apply`, and its authored members are left alone, because their
     `target.version` says when a person last read them; a two-file bundle has the
     `target.version` stamp of every `docs/summary/*.md` moved.

Use it when integrating two branches that each bumped from one base: the second one
renumbers (commit-merge/SKILL.md "the version home and the changelog"). Every rewrite is
anchored to the line that carries the number, never a global replace, so changelog prose
citing old versions is untouched.

Usage:
    python scripts/bump_version.py --new-version 0.71.0
    python scripts/bump_version.py --new-version 0.71.0 --old-version 0.70.0
    python scripts/bump_version.py --new-version 0.71.0 --message-file .git/COMMIT_EDITMSG
    python scripts/bump_version.py --new-version 0.71.0 --check

Exit 0 when the tree is at `--to` (rewritten, or already there), 1 with `--check` when a
rewrite is still pending, 2 on a usage or configuration error.

Complexity: O(n), n = lines in CHANGELOG.md scanned for the heading to renumber (other
edits are O(1)/small-file), plus one `check_brief.py --apply` per generated brief
inventory, which is linear in the repo it enumerates.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

import check_commit_message
from lib.shared import _commits, _frontmatter, _markdown
from lib.shared._constants import RELEASE_RE, RELEASE_TITLE_RE, UNRELEASED_TITLE


class RenumberError(Exception):
    """A usage or configuration fault that should exit 2 with one clear line."""


def rewrite_version_home(text: str, home_path: str, old: str, new: str) -> str:
    """Return `text` with the version home's value moved from `old` to `new`.

    A root `VERSION` file is replaced outright. A pyproject is rewritten only inside the
    `[project]` table, since other tables may carry a `version` key.
    """
    if home_path == "VERSION":
        return text.replace(old, new, 1) if text.strip() == old else text

    lines = text.splitlines(keepends=True)
    in_project = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project and re.match(rf'^version\s*=\s*"{re.escape(old)}"\s*$', stripped):
            lines[index] = line.replace(f'"{old}"', f'"{new}"', 1)
            break
    return "".join(lines)


def rewrite_changelog(text: str, old: str, new: str) -> str:
    """Return `text` with the `## <old> - DATE` heading renumbered to `<new>`.

    The heading's date is preserved: the commit audit's `section-date-mismatch` rule
    compares it with the commit's author date. Only the first matching heading moves;
    body prose citing `old` is left as written.
    """
    lines = text.splitlines(keepends=True)
    for heading, version, date in _releases(text):
        if version == old:
            ending = lines[heading.no - 1][len(lines[heading.no - 1].rstrip("\r\n")) :]
            lines[heading.no - 1] = f"## {new} - {date}{ending}"
            break
    return "".join(lines)


def _releases(text: str) -> list[tuple[_markdown.Heading, str, str]]:
    """`(heading, version, date)` for each `## X.Y.Z - DATE` heading, in file order."""
    return [
        (heading, match.group(1), match.group(2))
        for heading in _markdown.parse(text).headings(2)
        if (match := RELEASE_TITLE_RE.fullmatch(heading.title))
    ]


def rewrite_brief_stamp(text: str, old: str, new: str) -> str:
    """Return `text` with the frontmatter `target.version` stamp moved to `new`.

    Anchored to the `target:` block; `generator.version` (racecar's own version) is left
    alone, as in check_brief.py.
    """
    lines = text.splitlines(keepends=True)
    in_target = False
    for index, line in enumerate(lines):
        if re.match(r"^target:\s*$", line):
            in_target = True
            continue
        if in_target:
            if line[:1] not in (" ", "\t"):
                break
            if re.match(rf'^\s+version:\s*"{re.escape(old)}"\s*$', line):
                lines[index] = line.replace(f'"{old}"', f'"{new}"', 1)
                break
    return "".join(lines)


def rewrite_footer(text: str, old: str, new: str) -> str:
    """Return `text` with the `Bump version to <old>.` footer moved to `<new>`."""
    pattern = re.compile(rf"^Bump version to {re.escape(old)}\.$", re.M)
    return pattern.sub(f"Bump version to {new}.", text, count=1)


def append_footer(text: str, new: str) -> str:
    """Return `text` with a `Bump version to <new>.` footer added.

    Git reads trailers only from the last paragraph, so the footer joins a trailing
    paragraph of trailers, or opens a new one.
    """
    body = text.rstrip("\n")
    footer = f"Bump version to {new}."
    if not body:
        return footer + "\n"
    # `check_commit_message.py` decides what counts as a trailer.
    footer_re = check_commit_message.FOOTER_RE
    last = body.rsplit("\n\n", 1)[-1].splitlines()
    joins = bool(last) and all(footer_re.match(line.strip()) for line in last)
    return body + ("\n" if joins else "\n\n") + footer + "\n"


_INVENTORY_SUFFIX = "-INVENTORY.md"
_ROLE_INVENTORY_RE = re.compile(r"\Arole:\s*inventory\s*$", re.M)
_SECTION_RECORD_RE = re.compile(r"^<!--\s*\{\s*version:", re.M)


def bump_stamps(text: str) -> bool:
    """Whether a bump moves this brief's `target.version`: only in a two-file bundle.

    A member whose `bundle:` names a generated `*-INVENTORY.md` is authored, and its stamp
    is moved by whoever re-reads it; the inventory is regenerated whole (`inventories`).
    Read with a regex so this script stays stdlib-only. The runbook asks this too.
    """
    head = _frontmatter.block(text) or ""
    if _ROLE_INVENTORY_RE.search(head) or _INVENTORY_SUFFIX in head:
        return False
    # A file whose sections record their own reviews takes its stamp from the oldest of
    # them (check_brief.py), and a bump reads none of them.
    return not _SECTION_RECORD_RE.search(text)


def stamped_briefs(root: Path) -> list[Path]:
    """The `docs/summary/*.md` files a bump stamps; see `bump_stamps`."""
    return [
        brief
        for brief in sorted((root / "docs" / "summary").glob("*.md"))
        if bump_stamps(brief.read_text(encoding="utf-8"))
    ]


def inventories(root: Path) -> list[Path]:
    """Every generated brief inventory, regenerated rather than stamped by a bump."""
    return [
        p
        for p in sorted((root / "docs" / "summary").glob(f"*{_INVENTORY_SUFFIX}"))
        if _ROLE_INVENTORY_RE.search(
            _frontmatter.block(p.read_text(encoding="utf-8")) or ""
        )
    ]


def regenerate_inventories(root: Path, check: bool) -> list[Path]:
    """Rewrite every generated inventory from the tree now that the version home moved.

    Runs the delivered `check_brief.py --apply` beside this file in a child process (it
    needs PyYAML; this script is stdlib-only). With `check`, lists what would be
    regenerated.
    """
    found = inventories(root)
    if check or not found:
        return found
    script = Path(__file__).resolve().parent / "check_brief.py"
    if not script.is_file():
        raise RenumberError(
            "check_brief.py is missing, and a bundle with a generated inventory needs it "
            "to regenerate at the new version"
        )
    for inventory in found:
        done = subprocess.run(
            [sys.executable, str(script), "--apply", str(inventory)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise RenumberError(
                f"regenerating {display(inventory, root)} failed:\n"
                f"{done.stdout}{done.stderr}".rstrip()
            )
    return found


def display(path: Path, root: Path) -> str:
    """Return `path` relative to `root` for reporting, or absolute when it is outside.

    A commit-message file is often outside the repo (`.git/COMMIT_EDITMSG`).
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def plan(
    root: Path, home_path: str, old: str, new: str, message: Path | None
) -> list[tuple[Path, str]]:
    """Return `(path, new_content)` for every artifact this renumber would rewrite.

    Only files whose content changes are listed, so a re-run is a reported no-op.
    """
    edits: list[tuple[Path, str]] = []

    def stage(path: Path, rewrite: Callable[[str], str]) -> None:
        """Record `path`'s rewritten content when the rewrite actually changes it."""
        if not path.is_file():
            return
        before = path.read_text(encoding="utf-8")
        after = rewrite(before)
        if after != before:
            edits.append((path, after))

    stage(root / home_path, lambda t: rewrite_version_home(t, home_path, old, new))
    stage(root / "CHANGELOG.md", lambda t: rewrite_changelog(t, old, new))
    for brief in stamped_briefs(root):
        stage(brief, lambda t: rewrite_brief_stamp(t, old, new))
    if message is not None:
        stage(message, lambda t: rewrite_footer(t, old, new))
    return edits


def assert_target_is_free(root: Path, new: str) -> None:
    """Refuse when a `## <new>` section already stands.

    Two bumps under one heading breaks COMMITS.md "One changelog section per bump".
    """
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file():
        return
    if any(
        version == new
        for _, version, _ in _releases(changelog.read_text(encoding="utf-8"))
    ):
        raise RenumberError(
            f"CHANGELOG.md already has a `## {new}` section. Renumbering onto it would "
            f"merge two bumps under one heading (COMMITS.md 'One changelog section per "
            f"bump'). Pick a free number."
        )


# ---------------------------------------------------------------------------
# Assigning a number at landing
# ---------------------------------------------------------------------------
#
# Under the trunk-only version rule (shared/COMMITS.md), a branch records its entry
# under `## [Unreleased]` and leaves the version home alone. At landing, the trunk's
# current version plus the commit's type give the one next version.

UNRELEASED = f"## {UNRELEASED_TITLE}"


def next_version(current: str, commit_type: str, breaking: bool) -> str:
    """The one version `current` becomes under COMMITS.md's type-to-bump table.

    The table and the pre-1.0 rule live in `lib/shared/_commits.py`; this refuses a type
    that maps to no bump.
    """
    level = _commits.bump_for(commit_type, breaking, current)
    new = _commits.next_version(current, level)
    if new is None:
        raise RenumberError(f"version home holds '{current}', which is not X.Y.Z")
    if new == current:
        raise RenumberError(
            f"'{commit_type}' maps to no bump, so there is no version to assign. A commit "
            f"that does not bump records its entry under '{UNRELEASED}' and leaves it there."
        )
    return new


def _unreleased(text: str) -> _markdown.Section:
    """The `## [Unreleased]` section, as the markdown reader finds the heading.

    Found as a heading, not a substring, so a quoted `## [Unreleased]` in prose or a
    code block never matches.
    """
    section = _markdown.parse(text).section(UNRELEASED_TITLE)
    if section is None:
        raise RenumberError(
            f"no '{UNRELEASED}' heading to promote. A branch under the trunk-only version "
            f"rule records its entry there; without one there is nothing to assign."
        )
    return section


def unreleased_body(text: str) -> str:
    """What sits under the `## [Unreleased]` heading, up to the next `## ` heading."""
    section = _unreleased(text)
    lines = text.splitlines(keepends=True)
    return "".join(lines[section.start : section.end - 1])


def has_entry(body: str) -> bool:
    """Whether an unreleased section says anything a reader of that release would find.

    A line of prose or a bullet counts; a `### ` sub-heading alone does not.
    """
    doc = _markdown.parse(body, frontmatter=False)
    return any(
        line.text.strip() and doc.heading_at(line.no) is None for line in doc.body()
    )


def promote_unreleased(text: str, new: str, date: str) -> str:
    """Turn the `## [Unreleased]` heading into a released section, keeping one above it.

    An empty `[Unreleased]` is kept above it for the next non-bumping commit. Refuses
    when the section carries no entry (COMMITS.md "One changelog section per bump").
    """
    body = unreleased_body(text)
    if not has_entry(body):
        raise RenumberError(
            f"'{UNRELEASED}' carries no entry, so there is nothing to release. A released "
            f"section describes its commit's change and is written in that commit "
            f"(COMMITS.md 'One changelog section per bump'). Write the entry, then assign."
        )
    lines = text.splitlines(keepends=True)
    at = _unreleased(text).start  # the line after the heading, counting from 0
    head = "".join(lines[:at]).rstrip("\r\n")
    return f"{head}\n\n## {new} - {date}\n{''.join(lines[at:])}"


def _today() -> str:
    """Today, ISO. Isolated so a test can pin it and a caller can override it."""
    return datetime.date.today().isoformat()


class Assignment(NamedTuple):
    """The one move an assign performs, passed whole because its parts are meaningless apart."""

    home_path: str
    current: str
    new: str
    date: str


def assign_plan(
    root: Path, move: Assignment, message: Path | None
) -> list[tuple[Path, str]]:
    """Return `(path, new_content)` for every artifact an assign would write.

    Not `plan()`: an assign opens a new section from `## [Unreleased]`, and
    `## current - DATE` is the previous release, which must not be renumbered.
    """
    edits: list[tuple[Path, str]] = []

    def stage(path: Path, rewrite: Callable[[str], str]) -> None:
        if not path.is_file():
            return
        before = path.read_text(encoding="utf-8")
        after = rewrite(before)
        if after != before:
            edits.append((path, after))

    stage(
        root / move.home_path,
        lambda t: rewrite_version_home(t, move.home_path, move.current, move.new),
    )
    stage(root / "CHANGELOG.md", lambda t: promote_unreleased(t, move.new, move.date))
    for brief in stamped_briefs(root):
        stage(brief, lambda t: rewrite_brief_stamp(t, move.current, move.new))
    if message is not None:
        stage(message, lambda t: append_footer(t, move.new))
    return edits


def run_assign(args: argparse.Namespace) -> int:
    """Assign the version a branch could not know; return the process exit code."""
    home = _commits.version_home(args.root)
    if home is None:
        raise RenumberError(
            "no version home found (no [project].version and no VERSION file)"
        )
    home_path, current = home
    new = next_version(current, args.assign, args.breaking)
    assert_target_is_free(args.root, new)

    move = Assignment(home_path, current, new, args.date)
    edits = assign_plan(args.root, move, args.message_file)
    if not edits:
        raise RenumberError(
            f"nothing to assign (version home {home_path} is at {current})"
        )

    verb = "would write" if args.check else "wrote"
    for path, _ in edits:
        print(f"  {verb}  {display(path, args.root)}")
    if args.check:
        for path in regenerate_inventories(args.root, check=True):
            print(f"  would regenerate  {display(path, args.root)}")
        print(f"bump_version: {len(edits)} artifact(s) pending {current} -> {new}")
        return 1
    for path, content in edits:
        path.write_text(content, encoding="utf-8")
    for path in regenerate_inventories(args.root, check=False):
        print(f"  regenerated  {display(path, args.root)}")
    print(
        f"bump_version: assigned {new} (from {current}, '{args.assign}') "
        f"across {len(edits)} artifact(s)"
    )
    return 0


def run(args: argparse.Namespace) -> int:
    """Do the renumber described by `args`; return the process exit code."""
    for label, value in (
        ("--new-version", args.new_version),
        ("--old-version", args.old_version),
    ):
        if value is not None and not RELEASE_RE.match(value):
            raise RenumberError(f"{label} must be semver X.Y.Z; got {value!r}")

    home = _commits.version_home(args.root)
    if home is None:
        raise RenumberError(
            "no version home found (no [project].version and no VERSION file)"
        )
    home_path, current = home
    old = args.old_version or current

    if old == args.new_version:
        print(f"bump_version: already at {args.new_version}; nothing to move")
        return 0

    assert_target_is_free(args.root, args.new_version)

    edits = plan(args.root, home_path, old, args.new_version, args.message_file)
    if not edits:
        raise RenumberError(
            f"nothing carries {old} (version home {home_path} is at {current})"
        )
    if not any(path == args.root / home_path for path, _ in edits):
        raise RenumberError(
            f"{home_path} still reads {current!r}, not {old!r} -- refusing to report "
            f"success on other artifacts while the version home, the anchor every one "
            f"of them is defined relative to, never moved"
        )

    verb = "would move" if args.check else "moved"
    for path, _ in edits:
        print(f"  {verb}  {display(path, args.root)}")
    if args.check:
        for path in regenerate_inventories(args.root, check=True):
            print(f"  would regenerate  {display(path, args.root)}")
        print(
            f"bump_version: {len(edits)} artifact(s) pending {old} -> {args.new_version}"
        )
        return 1

    for path, content in edits:
        path.write_text(content, encoding="utf-8")
    for path in regenerate_inventories(args.root, check=False):
        print(f"  regenerated  {display(path, args.root)}")
    print(f"bump_version: {old} -> {args.new_version} across {len(edits)} artifact(s)")
    if args.message_file is None:
        print(
            f"The `Bump version to {old}.` footer is in the commit message, which only a "
            f"replay can rewrite. Re-run with --message, or amend the message by hand."
        )
    return 0


def main(argv: list[str]) -> int:
    """Renumber a bump across its artifacts; see the module docstring for the contract."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--new-version",
        help="The version to renumber TO. Mutually exclusive with --assign.",
    )
    parser.add_argument(
        "--assign",
        metavar="TYPE",
        help="Assign the next version from the commit's conventional TYPE, promoting "
        "'## [Unreleased]'. For landing a branch that recorded no version.",
    )
    parser.add_argument(
        "--breaking",
        action="store_true",
        help="With --assign: the commit is breaking (pre-1.0 this is a minor bump).",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="With --assign: the released section's date. Default: today.",
    )
    parser.add_argument(
        "--old-version",
        help="The version to renumber FROM. Default: the current version home value.",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--message-file",
        type=Path,
        help="A commit-message file whose `Bump version to X.Y.Z.` footer also moves.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what would move and exit 1 if anything would; write nothing.",
    )

    args = parser.parse_args(argv)
    if bool(args.new_version) == bool(args.assign):
        parser.error("give exactly one of --new-version VERSION or --assign TYPE")
    if args.date is None:
        args.date = _today()
    try:
        return run_assign(args) if args.assign else run(args)
    except RenumberError as exc:
        print(f"bump_version: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
