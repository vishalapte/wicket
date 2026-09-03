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
  4. the **`target.version` stamp** in every `docs/summary/*.md` brief, which
     scripts/check_brief.py compares against the version home.

Editing one by hand and calling it a renumber is how a repo ends up with a footer naming
a version that was never a state of the tree. This moves all four together.

The case that needs it is an integration. Two branches that each bumped from one base
claim the SAME number, and the second one integrated has to renumber -- see
commit-merge/SKILL.md "the version home and the changelog". The version home does not
even conflict when it happens: both sides wrote the same string, so git auto-merges it
and the discarded bump is silent. That silence is why this is a tool rather than a
paragraph of instructions, and why check_commit_history.py gates the merge case.

Every rewrite is ANCHORED to the line that carries the number, never a global
search-and-replace of the version string. A changelog body cites old versions in prose
("declined to wrap the deploy step until 0.70.0"), and a blind replace corrupts the
history it is supposed to be renumbering.

Usage:
    python scripts/renumber_bump.py --to 0.71.0
    python scripts/renumber_bump.py --to 0.71.0 --from 0.70.0
    python scripts/renumber_bump.py --to 0.71.0 --message .git/COMMIT_EDITMSG
    python scripts/renumber_bump.py --to 0.71.0 --check

Exit 0 when the tree is at `--to` (rewritten, or already there), 1 with `--check` when a
rewrite is still pending, 2 on a usage or configuration error.

Complexity: O(n), n = lines in CHANGELOG.md scanned for the heading to renumber (other
edits are O(1)/small-file).
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class RenumberError(Exception):
    """A usage or configuration fault that should exit 2 with one clear line."""


def resolve_version_home(root: Path) -> tuple[str, str] | None:
    """Return `(repo-relative path, value)` for the version home, via the ONE resolver.

    Loads `scripts/check_version_bump.py` and calls its `version_home` rather than
    re-reading `pyproject.toml` here. COMMITS.md "Version home" has exactly one resolver,
    and a second copy in this file is the P-02 failure that principle names: the two
    would disagree the first time a repo migrated from `VERSION` to `[project].version`,
    and this tool would then renumber the file the gate is not reading. check_brief.py
    reuses the same resolver by the same route.

    Raises RenumberError when the gate script is absent -- unlike the brief checker, which
    abstains, a renumber that guessed its own baseline would write the wrong number.
    """
    script = root / "scripts" / "check_version_bump.py"
    if not script.is_file():
        raise RenumberError(
            "scripts/check_version_bump.py is missing, and it is the one resolver for "
            "COMMITS.md 'Version home'. Nothing to renumber against."
        )
    spec = importlib.util.spec_from_file_location("_renumber_version_bump", script)
    if spec is None or spec.loader is None:
        raise RenumberError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The loaded module is untyped, so its return is Any. Unpack it into the declared
    # shape rather than casting: this is a cross-file contract held by convention, and
    # a resolver that started returning something else should fail here and not three
    # frames later inside a rewrite.
    home = module.version_home(root)
    if home is None:
        return None
    home_path, value = home
    return str(home_path), str(value)


def rewrite_version_home(text: str, home_path: str, old: str, new: str) -> str:
    """Return `text` with the version home's value moved from `old` to `new`.

    A root `VERSION` file is the whole value, so it is replaced outright. A pyproject is
    rewritten only inside the `[project]` table: `version = "..."` is a legal key in
    `[tool.poetry]`, in tool tables and in build backends, and a file-wide replace would
    move whichever one came first.
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

    The heading's DATE is PRESERVED. A renumber moves a bump's identity, not the day the
    work was authored, and check_commit_history.py's `section-date-mismatch` rule compares
    that date against the commit's author date -- which a renumber does not change either.

    Only the heading line moves, and only the first one. Body prose citing `old` is left
    exactly as written: those references point at the version that genuinely shipped under
    that number in some other section, and rewriting them is corruption dressed as
    consistency.
    """
    pattern = re.compile(rf"^## {re.escape(old)} - (\d{{4}}-\d{{2}}-\d{{2}})", re.M)
    return pattern.sub(rf"## {new} - \1", text, count=1)


def rewrite_brief_stamp(text: str, old: str, new: str) -> str:
    """Return `text` with the frontmatter `target.version` stamp moved to `new`.

    Anchored to the `target:` block. `generator.version` is racecar's OWN version and sits
    in the same frontmatter under an almost identical key; moving it would stamp an
    adopter's brief with the adopter's version as though racecar had produced it.
    check_brief.py draws the same distinction and for the same reason.
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


def display(path: Path, root: Path) -> str:
    """Return `path` relative to `root` for reporting, or absolute when it is outside.

    A commit-message file is routinely outside the repo -- `.git/COMMIT_EDITMSG` during a
    replay, a scratch file during an integration -- and a bare `relative_to` raises on it.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def plan(
    root: Path, home_path: str, old: str, new: str, message: Path | None
) -> list[tuple[Path, str]]:
    """Return `(path, new_content)` for every artifact this renumber would rewrite.

    Only files whose content actually changes are listed, which is what makes a re-run a
    reported no-op rather than a second edit.
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
    for brief in sorted((root / "docs" / "summary").glob("*.md")):
        stage(brief, lambda t: rewrite_brief_stamp(t, old, new))
    if message is not None:
        stage(message, lambda t: rewrite_footer(t, old, new))
    return edits


def assert_target_is_free(root: Path, new: str) -> None:
    """Refuse when a `## <new>` section already stands.

    Renumbering onto an existing section would put two bumps' worth of change under one
    heading, which is the accumulation COMMITS.md "One changelog section per bump" exists
    to stop -- and it is the likeliest way to use this tool wrongly during an integration.
    """
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file():
        return
    if re.search(
        rf"^## {re.escape(new)} - ", changelog.read_text(encoding="utf-8"), re.M
    ):
        raise RenumberError(
            f"CHANGELOG.md already has a `## {new}` section. Renumbering onto it would "
            f"merge two bumps under one heading (COMMITS.md 'One changelog section per "
            f"bump'). Pick a free number."
        )


# ---------------------------------------------------------------------------
# Assigning a number that was never predicted
# ---------------------------------------------------------------------------
#
# The counterpart to renumbering, and strictly the simpler of the two. Under the trunk-only
# version rule (shared/COMMITS.md), a branch records its entry under `## [Unreleased]` and
# leaves the version home alone, because the number is not knowable there. Landing is where
# it becomes knowable: the trunk's current version plus the commit's type give exactly one
# answer.
#
# Simpler than a renumber because there is no stale number to find and no "was a bump
# silently discarded?" to detect. Nothing predicted, so nothing can be wrong.

_BUMP_BY_TYPE = {
    "feat": "minor",
    "fix": "patch",
    "perf": "patch",
    "docs": "none",
    "style": "none",
    "refactor": "none",
    "test": "none",
    "build": "none",
    "ci": "none",
    "chore": "none",
    "revert": "none",
}

UNRELEASED = "## [Unreleased]"


def next_version(current: str, commit_type: str, breaking: bool, pre_1_0: bool) -> str:
    """The one version `current` becomes under COMMITS.md's type-to-bump table.

    Pre-1.0 downgrades a breaking change to a minor bump, which is COMMITS.md's rule and
    not this module's opinion; `pre_1_0` is passed rather than inferred so the caller owns
    that reading.
    """
    major, minor, patch = (int(part) for part in current.split("."))
    bump = "major" if breaking else _BUMP_BY_TYPE.get(commit_type, "none")
    if bump == "major" and pre_1_0:
        bump = "minor"
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise RenumberError(
        f"'{commit_type}' maps to no bump, so there is no version to assign. A commit "
        f"that does not bump records its entry under '{UNRELEASED}' and leaves it there."
    )


def promote_unreleased(text: str, new: str, date: str) -> str:
    """Turn the `## [Unreleased]` heading into a released section, keeping one above it.

    The empty `[Unreleased]` is left in place rather than consumed: the next non-bumping
    commit needs somewhere to write, and re-adding a heading is the step people forget.
    """
    if UNRELEASED not in text:
        raise RenumberError(
            f"no '{UNRELEASED}' heading to promote. A branch under the trunk-only version "
            f"rule records its entry there; without one there is nothing to assign."
        )
    return text.replace(UNRELEASED, f"{UNRELEASED}\n\n## {new} - {date}", 1)


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

    Deliberately NOT `plan()`. A renumber rewrites the `## old - DATE` heading because that
    heading IS the bump being moved; an assign has no such heading -- the branch wrote
    `## [Unreleased]` -- and `## current - DATE` is the PREVIOUS release. Reusing the
    renumber path here would silently renumber the last shipped section instead of opening
    a new one, which is corruption dressed as reuse.
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
    for brief in sorted((root / "docs" / "summary").glob("*.md")):
        stage(brief, lambda t: rewrite_brief_stamp(t, move.current, move.new))
    if message is not None:
        stage(message, lambda t: t.rstrip("\n") + f"\nBump version to {move.new}.\n")
    return edits


def run_assign(args: argparse.Namespace) -> int:
    """Assign the version a branch could not know; return the process exit code."""
    home = resolve_version_home(args.root)
    if home is None:
        raise RenumberError(
            "no version home found (no [project].version and no VERSION file)"
        )
    home_path, current = home
    new = next_version(
        current, args.assign, args.breaking, pre_1_0=current.startswith("0.")
    )
    assert_target_is_free(args.root, new)

    move = Assignment(home_path, current, new, args.date)
    edits = assign_plan(args.root, move, args.message)
    if not edits:
        raise RenumberError(
            f"nothing to assign (version home {home_path} is at {current})"
        )

    verb = "would write" if args.check else "wrote"
    for path, _ in edits:
        print(f"  {verb}  {display(path, args.root)}")
    if args.check:
        print(f"renumber_bump: {len(edits)} artifact(s) pending {current} -> {new}")
        return 1
    for path, content in edits:
        path.write_text(content, encoding="utf-8")
    print(
        f"renumber_bump: assigned {new} (from {current}, '{args.assign}') "
        f"across {len(edits)} artifact(s)"
    )
    return 0


def run(args: argparse.Namespace) -> int:
    """Do the renumber described by `args`; return the process exit code."""
    for label, value in (("--to", args.to), ("--from", args.from_version)):
        if value is not None and not SEMVER_RE.match(value):
            raise RenumberError(f"{label} must be semver X.Y.Z; got {value!r}")

    home = resolve_version_home(args.root)
    if home is None:
        raise RenumberError(
            "no version home found (no [project].version and no VERSION file)"
        )
    home_path, current = home
    old = args.from_version or current

    if old == args.to:
        print(f"renumber_bump: already at {args.to}; nothing to move")
        return 0

    assert_target_is_free(args.root, args.to)

    edits = plan(args.root, home_path, old, args.to, args.message)
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
        print(f"renumber_bump: {len(edits)} artifact(s) pending {old} -> {args.to}")
        return 1

    for path, content in edits:
        path.write_text(content, encoding="utf-8")
    print(f"renumber_bump: {old} -> {args.to} across {len(edits)} artifact(s)")
    if args.message is None:
        print(
            f"The `Bump version to {old}.` footer is in the commit message, which only a "
            f"replay can rewrite. Re-run with --message, or amend the message by hand."
        )
    return 0


def main(argv: list[str]) -> int:
    """Renumber a bump across its artifacts; see the module docstring for the contract."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--to", help="The version to renumber TO. Mutually exclusive with --assign."
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
        "--from",
        dest="from_version",
        help="The version to renumber FROM. Default: the current version home value.",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--message",
        type=Path,
        help="A commit-message file whose `Bump version to X.Y.Z.` footer also moves.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what would move and exit 1 if anything would; write nothing.",
    )
    args = parser.parse_args(argv)
    if bool(args.to) == bool(args.assign):
        parser.error("give exactly one of --to (renumber) or --assign TYPE (assign)")
    if args.date is None:
        args.date = _today()

    try:
        return run_assign(args) if args.assign else run(args)
    except RenumberError as exc:
        print(f"renumber_bump: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
