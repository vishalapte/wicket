#!/usr/bin/env python3
"""Mechanical validator for a racecar-llm-summary brief bundle.

Validates a brief produced by the ``racecar-llm-summary`` generator against
the schema and structural budget declared in
``llm-summary/README.md`` (sections ``## Frontmatter (YAML)`` and
``## Structural budget``). That spec is the contract; this script only
mechanizes it.

Checks performed:

  1. Frontmatter YAML parses and matches the declared schema:
     - ``generator.name``, ``generator.version`` (semver "X.Y.Z") — the
       *generator's* version, i.e. racecar's. Shape only; never compared
       against the brief'd repo, which is a different project on its own
       version line.
     - ``target.repo``, ``target.date`` (ISO YYYY-MM-DD), ``target.version``
       (semver "X.Y.Z") — the *brief'd repo's* version, which is the stamp
       checked against that repo's version home.
     - ``bundle`` (non-empty list of filenames).
     - ``entities`` (required list). Each entry requires ``name``, ``case``
       (``db_backed`` / ``on_disk_managed`` / ``content_tree`` / ``none``),
       and ``purpose`` (one-sentence class-level description). ``lifecycle``
       defaults to ``realized``; ``mutability`` validated if present on
       ``content_tree`` entries. No field tables — purely class-level.
     - ``relationships`` (optional). Each entry requires ``from``, ``to``,
       ``cardinality`` (must be quoted: "1:1" / "1:N" / "M:N"); ``on_delete``
       and ``owner_side`` are optional.
     - ``external_surface`` (optional). Sub-keys ``http_routes`` /
       ``cli_verbs`` / ``mcp_tools`` / ``library_exports`` / ``webhooks`` /
       ``signals`` / ``scripts``; enum members for HTTP ``method``.
     - ``inventory`` (required). One entry per enumerable declaration kind,
       each naming the sources that yield its members from the tree. This is
       the losslessness invariant; see check 3.

  2. Structural body checks:
     - Every §N.M with a body component appears as a heading at the depth
       declared by the spec (H2 for §1/§2/§3 and Confidence; H3 for §N.M
       subsections). A heading immediately followed (within ~200 chars)
       by ``N/A — `` counts as a stubbed-and-OK section.
     - ``## Confidence`` carries ≥3 bullets after a ``**Least confident**``
       marker and ≥1 bullet after a ``**Not in this brief**`` marker.
     - ``bundle:`` frontmatter list exactly matches the set of ``*.md`` files
       in the bundle directory — no orphans, no missing.
     - §2.4 frontmatter surface keys: any key with >5 entries must be a
       first-class recognized kind (``http_routes``, ``cli_verbs``,
       ``mcp_tools``, ``library_exports``, ``webhooks``, ``signals``).

  3. Conformance — the inventory, checked in BOTH directions. Checks 1 and 2
     ask whether the brief is BUILT correctly; they compare it against the
     schema and can pass on a brief that describes a repo it has never seen.
     This one asks whether the brief CONFORMS to the tree. For every kind in
     ``inventory``, the declared member set and the set the tree yields under
     that kind's sources must be equal:
     - a declared member no source yields is a PHANTOM (error, at the brief's
       own ``file:line``) — the brief names something that is not there;
     - a yielded member the brief does not declare is an OMISSION (error, at
       the yielding ``file:line``) — the brief is lossy over that kind.
     Neither direction alone is enough. A kind can lose one member and gain a
     phantom in the same edit and keep its count, which is exactly what
     racecar's own brief did when two CLI verbs moved between nodes.

     Also resolves the paths the FRONTMATTER names (``external_surface``
     ``scripts[].path``), not just the ones the body names, and reports an
     ``external_paths`` exemption that has stopped being an exemption.

     Every "is this in the repo" question here asks ``git ls-files``, never the
     filesystem: gate scope is what is COMMITTED, so a gitignored ledger or a
     stale ``__pycache__`` is out and a member the repo carries is in.

  4. ``--challenge`` (reports, never gates; always exit 0). Prints handoff
     probes with their answers, for the AUTHOR to hold, plus a token
     identifying which revision of the brief a recipient has. It grades an
     axis neither of the above reaches — whether the file is wholly in the
     reader's context — and it cannot be a gate, because the reader is in
     another chat on another machine. See ``llm-summary/SPEC.md``,
     "The other half".

Discovery:
  - If a path argument is given, it is used directly as the main-brief path.
  - Otherwise the script walks up from the CWD to find the nearest ancestor
    containing ``.git``. ``$repo`` is that directory's basename lowercased
    with any character outside ``[a-z0-9_-]`` replaced by ``-``; ``$REPO``
    is ``$repo`` uppercased. The brief is expected at
    ``<repo-root>/docs/summary/<$REPO>.md``.

Output:
  - One finding per line, prefixed ``check_brief: <severity>: <message>``
    where severity is ``error`` or ``warning``.
  - Final summary: ``check_brief: OK`` (exit 0) or
    ``check_brief: N errors, M warnings`` (exit 1 if any error).

Exit codes: 0 clean, 1 any errors.

Usage:
    python3 <path-to>/check_brief.py [<bundle-path>] [--challenge]

Complexity: O(F + L), F = files/paths walked in the repo tree (git ls-files, the
os.walk-based repo_files scan, and its per-path suffix index), L = lines read across
glob-matched inventory sources -- linear in the size of the repo being validated, not
the brief; _repo_path_suffixes's own comment marks that walk as the majority of this
script's measured runtime.
"""

# pylint: disable=too-many-lines
# This validator is delivered to every adopter as ONE file by `sync_scripts.py`
# (see its manifest). Splitting it to satisfy the module-length cap would trade a
# cosmetic limit for a real one: the adopter would need a package directory where a
# single script is copied today. The cap is waived here, at the site it constrains,
# rather than raised globally in `pyproject.toml` for every module in the repo.

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import date as date_cls
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeGuard

from check_packaging_rules._files import repo_files
from check_packaging_rules._root import find_repo_root
from check_packaging_rules._slug import brief_candidates, discover_brief

try:
    import yaml
except ImportError:  # pragma: no cover
    print(
        "check_brief: error: PyYAML is required. "
        "Install via `pip install --group dev` from the repo root.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover_brief_path() -> Path | None:
    """Return the conventional brief path ``docs/summary/$REPO.md`` or None.

    `$REPO` is resolved by `check_packaging_rules._slug`, which tries the directory
    basename and then `[project].name`. The second candidate is what lets this run
    from a git worktree — a checkout whose directory is named after the branch is
    still the same repository, and its brief did not move.
    """
    return discover_brief(find_repo_root())


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class Findings:
    """Accumulator for error/warning findings; preserves insertion order."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def error(self, msg: str) -> None:
        """Record an error-severity finding."""
        self.entries.append(("error", msg))

    def warning(self, msg: str) -> None:
        """Record a warning-severity finding."""
        self.entries.append(("warning", msg))

    @property
    def error_count(self) -> int:
        """Number of error-severity findings recorded."""
        return sum(1 for sev, _ in self.entries if sev == "error")

    @property
    def warning_count(self) -> int:
        """Number of warning-severity findings recorded."""
        return sum(1 for sev, _ in self.entries if sev == "warning")


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_yaml, body) or (None, full_text) when absent."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return m.group(1), m.group(2)


# ---------------------------------------------------------------------------
# Schema validators
# ---------------------------------------------------------------------------


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOWERCASE_REPO_RE = re.compile(r"^[a-z0-9_-]+$")

CARDINALITY_VALUES = {"1:1", "1:N", "M:N"}
ON_DELETE_VALUES = {"CASCADE", "PROTECT", "SET_NULL", "DO_NOTHING", "RESTRICT"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
ENTITY_CASES = {"db_backed", "on_disk_managed", "content_tree", "none"}
LIFECYCLE_VALUES = {"realized", "deprecated", "planned"}
MUTABILITY_VALUES = {"read-only", "mutable"}
# Whether a store's records were published by their source. Default private: R-08's
# rule runs unless a brief says otherwise, per TRUST.md on closed-by-default.
DISCLOSURE_VALUES = {"private", "public"}
# R-07's planes, as a brief declares them. Required to carry `path_pattern` or
# `count`, because those two keys describe a store and only the plane says whether
# describing it is metadata (PRINCIPLES.md R-08).
PLANE_VALUES = {"taxonomy", "control", "data"}
SURFACE_KINDS = {
    "http_routes",
    "cli_verbs",
    "mcp_tools",
    "library_exports",
    "webhooks",
    "signals",
    "scripts",
}


def require_mapping(value: object, where: str, f: Findings) -> bool:
    """Assert `value` is a dict; record an error at `where` and return False if not."""
    if not isinstance(value, dict):
        f.error(f"frontmatter: {where} must be a mapping; got {type(value).__name__}")
        return False
    return True


def require_list(value: object, where: str, f: Findings) -> TypeGuard[list[object]]:
    """Assert `value` is a list; record an error at `where` and return False if not.

    A TypeGuard, so the five `if not require_list(...): return` callers narrow on the
    fall-through and can iterate the value they just proved is a list.
    """
    if not isinstance(value, list):
        f.error(f"frontmatter: {where} must be a list; got {type(value).__name__}")
        return False
    return True


def validate_generator(generator: object, f: Findings) -> None:
    """Validate the frontmatter `generator` block."""
    if not require_mapping(generator, "generator", f):
        return
    assert isinstance(generator, dict)
    name = generator.get("name")
    if name != "racecar-llm-summary":
        f.error(
            f"frontmatter: generator.name must be 'racecar-llm-summary'; got {name!r}"
        )
    version = generator.get("version")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        f.error(
            f"frontmatter: generator.version must be semver 'X.Y.Z'; got {version!r}"
        )
    # Shape only. This is racecar's version, not the brief'd repo's — see
    # SPEC.md "from racecar's [project].version" — so there is nothing in an
    # adopter's tree it could correctly be compared against.


def validate_target(target: object, f: Findings) -> None:
    """Validate the frontmatter `target` block."""
    if not require_mapping(target, "target", f):
        return
    assert isinstance(target, dict)
    repo = target.get("repo")
    if not isinstance(repo, str) or not repo:
        f.error(f"frontmatter: target.repo must be a non-empty string; got {repo!r}")
    elif not LOWERCASE_REPO_RE.match(repo):
        f.error(f"frontmatter: target.repo must be lowercase [a-z0-9_-]; got {repo!r}")
    date = target.get("date")
    if isinstance(date, str):
        if not ISO_DATE_RE.match(date):
            f.error(f"frontmatter: target.date must be ISO YYYY-MM-DD; got {date!r}")
    else:
        # PyYAML may parse YYYY-MM-DD as datetime.date — accept that.
        if not isinstance(date, date_cls):
            f.error(f"frontmatter: target.date is required (ISO date); got {date!r}")
    version = target.get("version")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        f.error(
            "frontmatter: target.version must be semver 'X.Y.Z' — the version of "
            f"the repo this brief describes, read from its own version home; "
            f"got {version!r}"
        )


def validate_bundle(bundle: object, f: Findings) -> list[str]:
    """Validate the `bundle` list and return the declared member file names."""
    if not require_list(bundle, "bundle", f):
        return []
    assert isinstance(bundle, list)
    if not bundle:
        f.error("frontmatter: bundle must be a non-empty list")
        return []
    result: list[str] = []
    for i, item in enumerate(bundle):
        if not isinstance(item, str) or not item:
            f.error(
                f"frontmatter: bundle[{i}] must be a non-empty string; got {item!r}"
            )
            continue
        result.append(item)
    return result


def _literal_prefix(pattern: str) -> str:
    """The leading segments of a path pattern that carry no placeholder.

    `data/people/<slug>-<id>.md` yields `data/people` — enough to ask whether the tree
    itself is in the checkout, without trying to expand a pattern.
    """
    parts: list[str] = []
    for segment in pattern.split("/"):
        if any(ch in segment for ch in "<>*?{}[]$"):
            break
        parts.append(segment)
    return "/".join(parts)


# One snapshot of what git carries, per repo root, per process. A checker runs once, so
# a single listing is the whole answer, and one subprocess replaces one per question.
_TRACKED_CACHE: dict[str, frozenset[str] | None] = {}


def _tracked_files(repo_root: Path) -> frozenset[str] | None:
    """Every path git tracks in ``repo_root``, repo-relative posix; None if it cannot say.

    THE one home in this file for "what does this repo commit". Every rule here that asks
    whether something is part of the repo asks this and not the filesystem, because the
    two disagree in both directions and the disagreement is not rare: a gitignored ledger
    or a stale ``__pycache__`` exists for whoever ran the program and exists for nobody
    else, while a file deleted from the worktree but still staged is committed and absent.
    Gate scope is what is COMMITTED.
    """
    key = str(repo_root)
    if key in _TRACKED_CACHE:
        return _TRACKED_CACHE[key]
    result: frozenset[str] | None
    try:
        proc = subprocess.run(
            ["git", "-C", key, "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):  # pragma: no cover - git absent from PATH
        result = None
    else:
        result = (
            None
            if proc.returncode
            else frozenset(p for p in proc.stdout.split("\0") if p)
        )
    _TRACKED_CACHE[key] = result
    return result


def _git_tracks(repo_root: Path, prefix: str) -> bool | None:
    """Whether git tracks anything at or under ``prefix``; None when git cannot answer.

    Tracked-ness, not existence on disk. A derived cache (``.data/``, ``.downloads/``)
    exists in the working tree of anyone who has ever run the program and is tracked by
    nobody — testing ``Path.exists()`` reads that as committed, which is how this check
    told an adopter their gitignored cache was a defect in their repo. It is also the
    right test for the other caller: what *ships* with a release is what git carries, so
    a tree git does not track is not taxonomy either.

    The three answers are distinct and the callers need all three. None means the
    question could not be asked — no git on PATH, or not a working tree — and no caller
    may infer anything from it.
    """
    tracked = _tracked_files(repo_root)
    if tracked is None:
        return None
    if prefix in tracked:
        return True
    head = prefix.rstrip("/") + "/"
    return any(p.startswith(head) for p in tracked)


def _tracked_prefix(
    pattern: object, repo_root: Path | None
) -> tuple[str | None, bool | None]:
    """``(literal prefix inside the checkout, whether git tracks anything under it)``.

    ``(None, None)`` when there is nothing to resolve or the prefix lands OUTSIDE the
    checkout. An absolute pattern (``/var/lib/<pkg>/<id>.json``) or one that walks up
    out of it (``../store/<id>``) exists on this machine without belonging to this repo,
    and ``repo_root / "/var/lib"`` is just ``/var/lib`` — so resolving without that
    containment test would call an external store part of the checkout and send the
    author looking for a file the repo does not have.
    """
    if repo_root is None or not isinstance(pattern, str):
        return None, None
    prefix = _literal_prefix(pattern)
    if not prefix:
        return None, None
    try:
        resolved = (repo_root / prefix).resolve()
        resolved.relative_to(repo_root.resolve())
    except (ValueError, OSError):
        return None, None  # outside the checkout, or unresolvable
    return prefix, _git_tracks(repo_root, prefix)


# ---------------------------------------------------------------------------
# Per-key verifiers
# ---------------------------------------------------------------------------
#
# One function per protected key, registered in ``_KEY_VERIFIERS`` below, all sharing
# one signature so ``validate_entity_plane`` can walk them without knowing what any of
# them does. Adding a protected key is a function plus a row; the orchestrator does not
# change. That shape exists because the previous one drifted: every mechanical check was
# written against ``path_pattern`` because ``path_pattern`` was the key with something
# locatable, so ``count`` — the key R-08's own argument is about — was gated by
# declaration and corroborated by nothing. Split by key, an absent verification is a
# visibly empty function rather than an asymmetry nobody notices.
#
# A verifier CORROBORATES its key against the tree. It never rules on admissibility:
# that is R-08's rule, it is key-agnostic, and the orchestrator applies it uniformly.


def _verify_path_pattern(
    entity: dict[str, Any], where: str, f: Findings, repo_root: Path | None
) -> None:
    """Corroborate a ``path_pattern`` against what git carries.

    Two readings of the same fact, opposite in meaning. On the **taxonomy** plane the
    declaration says the tree lands at release from outside, so it ships — and what
    ships is what git tracks; a path git does not carry contradicts the declaration
    beside it. A **warning**, because taxonomy generated at build time or shipped inside
    an installed package is tracked nowhere here and is a legitimate instance (R-06).

    On the **data** plane a tracked tree means the store has been committed into the
    checkout, which is a defect in the repo — reported on its own, separate from
    whether the entry may carry the key at all.

    Where git cannot answer, the declaration is untested. Inferring from silence would
    put a finding on every adopter running the checker outside a working tree.
    """
    pattern = entity.get("path_pattern")
    prefix, tracked = _tracked_prefix(pattern, repo_root)
    if prefix is None:
        return
    plane = entity.get("plane")
    if plane == "taxonomy" and tracked is False:
        f.warning(
            f"frontmatter: {where} declares `plane: taxonomy` but its `path_pattern` "
            f"({pattern!r}) resolves nowhere in the repo. Taxonomy lands at release "
            f"from outside, so it ships with the program and git carries it; state "
            f"that accumulates after the release is control or data "
            f"(PRINCIPLES.md R-08). Generated or packaged taxonomy legitimately "
            f"resolves to nothing here — say so and move on."
        )
    elif plane == "data" and tracked:
        f.error(
            f"frontmatter: {where} declares `plane: data` and git tracks its "
            f"`path_pattern` ({prefix!r}) — the store is committed into the checkout. "
            f"That is a defect in the repo, reported here because only the brief "
            f"knows the tree is data (PRINCIPLES.md R-08)."
        )


def _verify_count(
    entity: dict[str, Any], where: str, f: Findings, repo_root: Path | None
) -> None:
    # pylint: disable=unused-argument
    """Corroborate a ``count`` against the entry that carries it.

    Nothing in the tree corroborates a quantity: a number cannot be resolved the way a
    path can, and a store that accumulates is not in the checkout to be counted. So
    this verifies the two things the schema itself fixes — that a `lifecycle: planned`
    entity says so rather than quoting a figure for a store that does not exist yet,
    and that a numeric count is not negative.

    ``repo_root`` is unused and stays in the signature: the registry calls every
    verifier the same way, and a uniform signature is what lets the orchestrator stay
    fixed while the set of keys grows.
    """
    count = entity.get("count")
    if entity.get("lifecycle") == "planned" and count != "none on disk":
        f.warning(
            f"frontmatter: {where} is `lifecycle: planned` and carries "
            f"`count: {count!r}`. Planned state has nothing on disk to count; the "
            f"schema's value for that is 'none on disk'."
        )
    if isinstance(count, bool) or not isinstance(count, int):
        return
    if count < 0:
        f.error(f"frontmatter: {where}.count must not be negative; got {count!r}")


# The one home for "which keys describe a store rather than the program". Every check
# that treats the pair as a set reads this; adding a key means a verifier above and a
# row here, and nothing in the orchestrator moves.
_KEY_VERIFIERS: dict[
    str, Callable[[dict[str, Any], str, Findings, Path | None], None]
] = {
    "path_pattern": _verify_path_pattern,
    "count": _verify_count,
}
PROTECTED_KEYS: tuple[str, ...] = tuple(_KEY_VERIFIERS)


def validate_entity_plane(
    entity: dict[str, Any], where: str, f: Findings, repo_root: Path | None = None
) -> None:
    """Gate the store-describing keys on the declared plane (PRINCIPLES.md R-08).

    ``path_pattern`` and ``count`` describe a tree the program operates on, and only the
    **taxonomy** plane admits them. Taxonomy lands at release from outside and admits no
    runtime writer, so its contents ship with the program and are the program's own shape.
    Control and data both accumulate after the release — one by an administrator's act,
    one by the system's own operation — so their contents describe the install or the
    records' subjects, never the software. A count of data-plane records is an inventory
    of someone's life; a count of control-plane rows is a fact about an operator's
    deployment. Neither is admissible, and no real name or figure has to appear for the
    disclosure to be complete, which is why content-blindness cannot catch it
    (``docs-orchestrator/CONTENT_BLINDNESS.md``).

    What survives on every plane is the entity itself: ``name``, ``case``, ``purpose`` and
    ``notes`` are never gated here. "There is a ``FeatureFlag``, it gates rollout" is the
    program's shape and stays sayable. Only how many there are, and where they sit, go.

    Bound to the KEYS, not to ``case``. The spec documents the pair as content_tree-only,
    but a brief that hangs them on ``on_disk_managed`` discloses exactly as much, and that
    is the shape the failure actually arrived in.

    The plane is the write path, not the appearance and not the location. A taxonomy whose
    membership is set by what the store holds is data-plane taxonomy and declares ``data``;
    state that lands at release and admits no runtime writer is ``taxonomy`` however
    data-like it looks. Location is checked only to sharpen the message, and it asks git
    rather than the filesystem: a data-plane tree git TRACKS means the store has been
    committed, which is a defect
    in the repo rather than a licence to describe it.
    """
    plane = entity.get("plane")
    disclosure = entity.get("disclosure")
    if plane is not None and plane not in PLANE_VALUES:
        f.error(
            f"frontmatter: {where}.plane must be one of {sorted(PLANE_VALUES)} "
            f"when present; got {plane!r}"
        )
        return
    if disclosure is not None and disclosure not in DISCLOSURE_VALUES:
        f.error(
            f"frontmatter: {where}.disclosure must be one of "
            f"{sorted(DISCLOSURE_VALUES)} when present; got {disclosure!r}"
        )
        return
    present = [key for key in PROTECTED_KEYS if key in entity]
    if not present:
        return
    if disclosure == "public":
        # Exempt from the DISCLOSURE rule, not from the shape checks. Whoever
        # published the records, a negative count is still invalid and a planned
        # entity still has nothing on disk to count — those verify the entry, not
        # what it discloses.
        for key in present:
            _KEY_VERIFIERS[key](entity, where, f, repo_root)
        return
    listed = " and ".join(f"`{key}`" for key in present)
    if plane is None:
        f.error(
            f"frontmatter: {where} carries {listed} and must declare `plane` "
            f"({' | '.join(sorted(PLANE_VALUES))}). Those keys describe a store, and "
            f"only the plane says whether describing it is a fact about the program or "
            f"about whoever its records describe (PRINCIPLES.md R-08)."
        )
        return
    if plane == "control":
        f.error(
            f"frontmatter: {where} declares `plane: control` and may not carry {listed}. "
            f"Control-plane state accumulates after the release, by an administrator's "
            f"act, so how much of it exists and where it sits are facts about an "
            f"operator's deployment rather than about the program (PRINCIPLES.md R-08). "
            f"The entity itself — `name`, `purpose`, `notes` — is unaffected. Where the "
            f"records were published by their source, declare `disclosure: public`."
        )
    elif plane == "data":
        f.error(
            f"frontmatter: {where} declares `plane: data` and may not carry {listed}. A "
            f"data-plane tree's records belong to whoever they describe, so a count is "
            f"an inventory of them (PRINCIPLES.md R-08). Describe the program's shape "
            f"instead; or, if this tree really lands at release with no runtime writer, "
            f"it is not the data plane; or, if its records were published by their "
            f"source, declare `disclosure: public`."
        )
    for key in present:
        _KEY_VERIFIERS[key](entity, where, f, repo_root)


def validate_entity(
    entity: object, idx: int, f: Findings, repo_root: Path | None = None
) -> None:
    """Validate one `entities[idx]` frontmatter entry."""
    where = f"entities[{idx}]"
    if not require_mapping(entity, where, f):
        return
    assert isinstance(entity, dict)
    case = entity.get("case")
    if case not in ENTITY_CASES:
        f.error(
            f"frontmatter: {where}.case must be one of {sorted(ENTITY_CASES)}; got {case!r}"
        )
    name = entity.get("name")
    if not isinstance(name, str) or not name:
        f.error(f"frontmatter: {where}.name must be a non-empty string; got {name!r}")
    purpose = entity.get("purpose")
    if not isinstance(purpose, str) or not purpose:
        f.error(
            f"frontmatter: {where}.purpose must be a non-empty string "
            "(one-sentence description)"
        )
    lifecycle = entity.get("lifecycle", "realized")
    if lifecycle not in LIFECYCLE_VALUES:
        f.error(
            f"frontmatter: {where}.lifecycle must be one of "
            f"{sorted(LIFECYCLE_VALUES)}; got {lifecycle!r}"
        )
    # Class-level only: no field tables, no per-case required keys beyond
    # name/case/purpose. `path_pattern`, `count`, `validator` are optional for
    # content_tree entries (description, not requirement).
    # Validated wherever it appears, not only on `content_tree`. Scoping an enum to
    # one case means a typo on any other case passes silently, and `mutability` was
    # the last key in this file still doing that — every other closed set here is
    # checked on presence. Which cases MAY carry it is a spec question; whether the
    # value is a member is not.
    if "mutability" in entity and entity.get("mutability") not in MUTABILITY_VALUES:
        f.error(
            f"frontmatter: {where}.mutability must be one of "
            f"{sorted(MUTABILITY_VALUES)} when present; "
            f"got {entity.get('mutability')!r}"
        )
    validate_entity_plane(entity, where, f, repo_root)


def validate_entities(
    entities: object, f: Findings, repo_root: Path | None = None
) -> None:
    """Validate the `entities` list and each entry within it."""
    if not require_list(entities, "entities", f):
        return
    assert isinstance(entities, list)
    for i, entity in enumerate(entities):
        validate_entity(entity, i, f, repo_root)


def validate_relationships(relationships: object, f: Findings) -> None:
    """Validate the `relationships` DAG list and each edge within it."""
    if not require_list(relationships, "relationships", f):
        return
    assert isinstance(relationships, list)
    for i, rel in enumerate(relationships):
        where = f"relationships[{i}]"
        if not require_mapping(rel, where, f):
            continue
        assert isinstance(rel, dict)
        for required_key in ("from", "to"):
            if not isinstance(rel.get(required_key), str) or not rel.get(required_key):
                f.error(
                    f"frontmatter: {where}.{required_key} must be a non-empty string"
                )
        if rel.get("cardinality") not in CARDINALITY_VALUES:
            f.error(
                f"frontmatter: {where}.cardinality must be one of {sorted(CARDINALITY_VALUES)}; "
                f"got {rel.get('cardinality')!r}"
            )
        # owner_side and on_delete are OPTIONAL — only validate enum membership when present.
        # on_delete is meaningful for DB FKs only; M:N and non-DB edges may omit it.
        if "on_delete" in rel and rel.get("on_delete") not in ON_DELETE_VALUES:
            f.error(
                f"frontmatter: {where}.on_delete must be one of "
                f"{sorted(ON_DELETE_VALUES)} when present; "
                f"got {rel.get('on_delete')!r}"
            )
        if "owner_side" in rel and (
            not isinstance(rel.get("owner_side"), str) or not rel.get("owner_side")
        ):
            f.error(
                f"frontmatter: {where}.owner_side must be a non-empty string when present"
            )


# Required non-empty string keys per surface kind. `mcp_tools` is a recognized
# kind whose entries are accepted without per-key validation (kept from the
# original schema). `http_routes` additionally validates its `method` enum below.
_SURFACE_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "http_routes": ("path", "view"),
    "cli_verbs": ("verb", "module", "behavior"),
    "library_exports": ("name", "module", "signature", "behavior"),
    "signals": ("name", "sender", "handler", "behavior"),
    "webhooks": ("source", "path", "behavior"),
    "scripts": ("name", "path", "purpose"),
}


def _validate_surface_kind(kind: str, entries: list[str], f: Findings) -> None:
    """Validate each entry of one surface kind against its required-key table."""
    required = _SURFACE_REQUIRED_KEYS.get(kind, ())
    for i, entry in enumerate(entries):
        where = f"external_surface.{kind}[{i}]"
        if not require_mapping(entry, where, f):
            continue
        assert isinstance(entry, dict)
        if kind == "http_routes" and entry.get("method") not in HTTP_METHODS:
            f.error(
                f"frontmatter: {where}.method must be one of {sorted(HTTP_METHODS)}; "
                f"got {entry.get('method')!r}"
            )
        for required_key in required:
            if not isinstance(entry.get(required_key), str) or not entry.get(
                required_key
            ):
                f.error(
                    f"frontmatter: {where}.{required_key} must be a non-empty string"
                )


def _report_unknown_kind(key: str, entries: object, f: Findings) -> None:
    """Flag a sub-key that is not a recognized surface kind (error if >5 entries)."""
    # §2.4 rule: any kind with >5 entries must be a recognized first-class key.
    # Free-form sub-keys are allowed only at small size.
    count = len(entries) if isinstance(entries, list) else 0
    if count > 5:
        f.error(
            f"frontmatter: external_surface.{key} has {count} entries but is not a "
            f"recognized surface kind ({sorted(SURFACE_KINDS)})"
        )
    else:
        f.warning(
            f"frontmatter: external_surface.{key} is not a recognized kind "
            f"({sorted(SURFACE_KINDS)})"
        )


def validate_external_surface(surface: object, f: Findings) -> None:
    """Validate the `external_surface` block grouping endpoints by surface kind."""
    if not require_mapping(surface, "external_surface", f):
        return
    assert isinstance(surface, dict)
    for key, entries in surface.items():
        if key not in SURFACE_KINDS:
            _report_unknown_kind(key, entries, f)
            continue
        if not require_list(entries, f"external_surface.{key}", f):
            continue
        assert isinstance(entries, list)
        _validate_surface_kind(key, entries, f)


def validate_external_paths(value: object, f: Findings) -> list[str]:
    """``external_paths`` must be a flat list of path strings; return it."""
    if not require_list(value, "external_paths", f):
        return []
    out: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            f.error(f"external_paths[{idx}]: must be a non-empty string")
            continue
        out.append(item.strip().rstrip("/"))
    return out


# ---------------------------------------------------------------------------
# Inventory — the losslessness invariant
# ---------------------------------------------------------------------------
#
# Everything above this line grades CONSTRUCTION: is the brief built the way the schema
# says. A brief can pass all of it while describing a repo it has never seen, because
# nothing above compares a declaration to the tree. What follows grades CONFORMANCE: for
# each enumerable kind, does the set the brief declares equal the set the tree yields.
#
# The two directions catch different defects and neither implies the other. A phantom —
# declared, not present — is the brief asserting something false. An omission — present,
# not declared — is the brief being lossy, which is the failure a reader cannot see,
# since a list they cannot compare against a tree looks complete either way. Counting
# catches neither: racecar's own brief carried 33 CLI verbs while two of them had moved
# to a different node, so it was simultaneously wrong twice and right about the total.
#
# The extractor is DECLARED, not executed. A kind names a glob and, optionally, a regex;
# the checker walks the glob and applies the regex. It never runs a command out of a
# document, and it holds no knowledge of any particular repo — which is what lets one
# delivered stdlib script enforce this in a tree it has never seen (R-03: determinism
# over heuristic, and R-01: the detector stays lower-entropy than what it watches).

INVENTORY_MATCH_VALUES = {"path", "pattern"}

# `derivation: none — <reason>`, borrowed verbatim from the architecture tree's
# `class: declared-only`. It says the oracle is not the tree, and it is REPORTED on
# every run rather than accepted in silence, because an unchecked kind inside a
# document claiming losslessness is precisely the professed rule R-02 forbids.
_DERIVATION_NONE_RE = re.compile(r"^none\s*[—-]\s*\S")

# `members_from: external_surface.cli_verbs[].verb` — one list, one scalar key from
# each of its entries. Deliberately the smallest grammar that reaches every list a
# brief already carries: the inventory must not become a second home for a member list
# the frontmatter states elsewhere (P-02).
_MEMBERS_FROM_RE = re.compile(r"^([A-Za-z_][\w.]*)\[\]\.([A-Za-z_]\w*)$")


def _glob_paths(repo_root: Path, expr: object, where: str, f: Findings) -> list[Path]:
    """Every COMMITTED path under ``repo_root`` matching ``expr``, sorted.

    Matching is ``pathlib`` globbing, so the pattern in a brief means what the Python
    docs say it means rather than what a pathspec dialect would. Scope is git's: a
    yielded member is one the repo commits, so a build artifact, a local cache or a
    stray ``__pycache__`` never becomes an omission the author is asked to declare.
    Where git cannot answer — no git, not a working tree — the filter abstains and every
    match is kept, because inferring from silence would shrink the inventory in exactly
    the case where nothing corroborates it.

    An absolute pattern or one walking up out of the checkout is refused rather than
    resolved: ``repo_root / "/etc"`` is ``/etc``, and a brief that inventories a tree
    outside its own repo is describing a machine, not a program.
    """
    if not isinstance(expr, str) or not expr.strip():
        f.error(f"frontmatter: {where}.glob must be a non-empty string; got {expr!r}")
        return []
    pattern = expr.strip()
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        f.error(
            f"frontmatter: {where}.glob must stay inside the checkout; got {pattern!r}"
        )
        return []
    try:
        matched = list(repo_root.glob(pattern))
    except (ValueError, OSError) as exc:
        f.error(f"frontmatter: {where}.glob is not a usable pattern ({exc})")
        return []
    tracked = _tracked_files(repo_root)
    kept: list[Path] = []
    for p in matched:
        rel = p.relative_to(repo_root)
        if _SKIP_DIR_PARTS & set(rel.parts):
            continue
        if tracked is not None and _git_tracks(repo_root, rel.as_posix()) is not True:
            continue
        kept.append(p)
    return sorted(kept)


def _path_fields(repo_root: Path, path: Path) -> dict[str, str]:
    """The path-derived fields an ``id_format`` template may name."""
    rel = path.relative_to(repo_root).as_posix()
    return {
        "path": rel,
        "parent": path.parent.name,
        "stem": path.stem,
        "name": path.name,
    }


def _format_id(
    template: object, fields: dict[str, str], where: str, f: Findings
) -> str | None:
    """Interpolate ``template`` from ``fields``; report the missing key rather than raise."""
    if not isinstance(template, str) or not template:
        f.error(f"frontmatter: {where}.id_format must be a non-empty string")
        return None
    try:
        return template.format_map(fields)
    except (KeyError, IndexError, ValueError) as exc:
        f.error(
            f"frontmatter: {where}.id_format {template!r} names {exc} — available "
            f"fields here are {sorted(fields)}"
        )
        return None


def _ids_from_paths(
    repo_root: Path, source: dict[str, Any], where: str, f: Findings
) -> list[tuple[str, str, int]]:
    """``match: path`` — the glob's own matches are the members.

    Returns ``(id, repo-relative file, line)``. The line is 1: the file IS the member,
    so the location that makes the finding actionable is the file itself.
    """
    out: list[tuple[str, str, int]] = []
    template = source.get("id_format")
    for path in _glob_paths(repo_root, source.get("glob"), where, f):
        fields = _path_fields(repo_root, path)
        member = (
            fields["path"]
            if template is None
            else _format_id(template, fields, where, f)
        )
        if member is not None:
            out.append((member, fields["path"], 1))
    return out


def _ids_from_pattern(
    repo_root: Path, source: dict[str, Any], where: str, f: Findings
) -> list[tuple[str, str, int]]:
    """``match: pattern`` — a line-anchored regex over the glob's matches yields members.

    The regex names the member: either through a group called ``id``, or through named
    groups an ``id_format`` template composes with the path-derived fields. Line-anchored
    on purpose — a whole-file regex would need the author to reason about where one match
    ends, and a member that cannot be pointed at on one line cannot be reported at
    ``file:line`` either.
    """
    raw = source.get("pattern")
    if not isinstance(raw, str) or not raw:
        f.error(f"frontmatter: {where}.pattern is required for `match: pattern`")
        return []
    try:
        rx = re.compile(raw)
    except re.error as exc:
        f.error(f"frontmatter: {where}.pattern is not a valid regex ({exc})")
        return []
    template = source.get("id_format")
    if template is None and "id" not in (rx.groupindex or {}):
        f.error(
            f"frontmatter: {where}.pattern must capture a group named `id`, or the "
            f"source must carry an `id_format` template"
        )
        return []
    out: list[tuple[str, str, int]] = []
    for path in _glob_paths(repo_root, source.get("glob"), where, f):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # a binary or unreadable file yields nothing; not a brief defect
        base = _path_fields(repo_root, path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = rx.search(line)
            if not m:
                continue
            fields = {**base, **{k: v or "" for k, v in m.groupdict().items()}}
            member = (
                fields.get("id")
                if template is None
                else _format_id(template, fields, where, f)
            )
            if member:
                out.append((member.strip(), base["path"], lineno))
    return out


def _source_ids(
    repo_root: Path, source: object, where: str, f: Findings
) -> list[tuple[str, str, int]]:
    """Dispatch one inventory source to its extractor."""
    if not require_mapping(source, where, f):
        return []
    assert isinstance(source, dict)
    match = source.get("match")
    if match not in INVENTORY_MATCH_VALUES:
        f.error(
            f"frontmatter: {where}.match must be one of "
            f"{sorted(INVENTORY_MATCH_VALUES)}; got {match!r}"
        )
        return []
    if match == "path":
        return _ids_from_paths(repo_root, source, where, f)
    return _ids_from_pattern(repo_root, source, where, f)


def resolve_members_from(
    data: dict[str, Any], ref: object, where: str, f: Findings
) -> list[str]:
    """Read a member list out of the frontmatter itself, per ``members_from``.

    The inventory says WHICH sets must be complete; it is not a second place to write
    them down. Where the frontmatter already carries the list — ``external_surface``
    ``cli_verbs``, ``scripts``, ``entities`` — the inventory points at it, so the check
    grades the list the recipient actually reads (P-02).
    """
    if not isinstance(ref, str):
        f.error(f"frontmatter: {where}.members_from must be a string; got {ref!r}")
        return []
    m = _MEMBERS_FROM_RE.match(ref.strip())
    if not m:
        f.error(
            f"frontmatter: {where}.members_from must be `<key>[.<key>...][].<key>`, "
            f"e.g. `external_surface.cli_verbs[].verb`; got {ref!r}"
        )
        return []
    node: Any = data
    for part in m.group(1).split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            f.error(
                f"frontmatter: {where}.members_from {ref!r} resolves to nothing — "
                f"`{part}` is not in this frontmatter"
            )
            return []
    if not isinstance(node, list):
        f.error(f"frontmatter: {where}.members_from {ref!r} does not name a list")
        return []
    key = m.group(2)
    out: list[str] = []
    for i, item in enumerate(node):
        value = item.get(key) if isinstance(item, dict) else None
        if not isinstance(value, str) or not value.strip():
            f.error(
                f"frontmatter: {where}.members_from {ref!r}: entry {i} has no `{key}`"
            )
            continue
        out.append(value.strip())
    return out


def _inventory_declared(
    entry: dict[str, Any], data: dict[str, Any], where: str, f: Findings
) -> list[str] | None:
    """The member set the brief declares for one kind, from ``members`` or ``members_from``."""
    has_inline = "members" in entry
    has_ref = "members_from" in entry
    if has_inline == has_ref:
        f.error(
            f"frontmatter: {where} must carry exactly one of `members` (the list) or "
            f"`members_from` (a pointer at a list already in this frontmatter)"
        )
        return None
    if has_ref:
        return resolve_members_from(data, entry.get("members_from"), where, f)
    members = entry.get("members")
    if not require_list(members, f"{where}.members", f):
        return None
    out: list[str] = []
    for i, item in enumerate(members):
        if not isinstance(item, str) or not item.strip():
            f.error(f"frontmatter: {where}.members[{i}] must be a non-empty string")
            continue
        out.append(item.strip())
    return out


def _brief_line_of(raw_lines: list[str], needle: str, fallback: int) -> int:
    """The brief's own line number for a declared value, so a finding is actionable."""
    for lineno, line in enumerate(raw_lines, start=1):
        if needle in line:
            return lineno
    return fallback


def _kind_sources(
    entry: dict[str, Any], where: str, f: Findings
) -> list[object] | None:
    """The source list for one kind, or None when the kind is declared-only.

    A kind with no extractor is admissible and is a warning, never silence. The tree
    genuinely cannot yield some sets — a CLI verb assembled by argparse at import time
    is not on any line of any committed file — and pretending otherwise would push the
    author into a regex that half-works, which is worse than a stated abstention (R-06).
    """
    if "sources" in entry:
        sources = entry.get("sources")
        if not require_list(sources, f"{where}.sources", f):
            return None
        return list(sources)
    if "match" in entry:
        return [entry]
    derivation = entry.get("derivation")
    if isinstance(derivation, str) and _DERIVATION_NONE_RE.match(derivation.strip()):
        f.warning(
            f"frontmatter: {where} is declared-only ({derivation.strip()}) — its member "
            f"list is stated and corroborated by nothing. The losslessness invariant does "
            f"not cover this kind; say so in `## Confidence`."
        )
        return None
    f.error(
        f"frontmatter: {where} must carry `sources` (or `match`/`glob` inline) saying how "
        f"the tree yields its members, or `derivation: none — <reason>` saying why nothing "
        f"can. A member list with neither is an unfalsifiable claim of completeness "
        f"(PRINCIPLES.md R-02)."
    )
    return None


def _report_kind(
    kind: str,
    declared: list[str],
    yielded: dict[str, tuple[str, int]],
    context: tuple[dict[str, Any], str, list[str], int],
    f: Findings,
) -> None:
    """Diff one kind's declared set against the tree's, both directions."""
    entry, brief_name, raw_lines, anchor = context
    exempt = {
        e.strip()
        for e in (entry.get("exempt") or [])
        if isinstance(e, str) and e.strip()
    }
    seen: set[str] = set()
    for member in declared:
        if member in seen:
            f.error(
                f"{brief_name}:{_brief_line_of(raw_lines, member, anchor)}: inventory "
                f"kind '{kind}' declares '{member}' twice"
            )
        seen.add(member)
        if member not in yielded:
            f.error(
                f"{brief_name}:{_brief_line_of(raw_lines, member, anchor)}: inventory "
                f"kind '{kind}' declares '{member}', which no source for that kind "
                f"yields — the brief names something the tree does not have."
            )
    for member, (src_file, src_line) in sorted(yielded.items()):
        if member in seen or member in exempt:
            continue
        f.error(
            f"{src_file}:{src_line}: inventory kind '{kind}' yields '{member}' and the "
            f"brief does not declare it — the brief is lossy over this kind. Declare it, "
            f"or list it under this kind's `exempt` with the reason beside it."
        )
    for member in sorted(exempt - set(yielded)):
        f.warning(
            f"{brief_name}:{_brief_line_of(raw_lines, member, anchor)}: inventory kind "
            f"'{kind}' exempts '{member}', which no source yields — a stale exemption."
        )


def check_inventory(
    data: dict[str, Any],
    repo_root: Path,
    brief_path: Path,
    raw_lines: list[str],
    f: Findings,
) -> None:
    """Hold every declared inventory kind to the tree, in both directions.

    Required, and its absence is an error rather than a skip. A brief with no inventory
    makes no checkable claim about what it left out, and the recipient — who holds the
    file and no repo — cannot tell a complete list from a partial one by reading it.
    That is the same asymmetry the version stamp exists for.
    """
    brief_name = brief_path.name
    if "inventory" not in data:
        f.error(
            f"{brief_name}:1: frontmatter has no `inventory` block. Every enumerable "
            f"declaration kind the brief lists — CLI verbs, delivered files, controlled "
            f"terms, axioms — must name the sources that yield it, so completeness is "
            f"checkable in both directions rather than asserted (llm-summary/SPEC.md, "
            f"'The losslessness invariant')."
        )
        return
    inventory = data["inventory"]
    if not require_list(inventory, "inventory", f):
        return
    assert isinstance(inventory, list)
    if not inventory:
        f.error("frontmatter: inventory must not be empty")
        return
    kinds: set[str] = set()
    for i, entry in enumerate(inventory):
        where = f"inventory[{i}]"
        if not require_mapping(entry, where, f):
            continue
        assert isinstance(entry, dict)
        kind = entry.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            f.error(
                f"frontmatter: {where}.kind must be a non-empty string; got {kind!r}"
            )
            continue
        kind = kind.strip()
        if kind in kinds:
            f.error(f"frontmatter: {where}.kind '{kind}' is declared more than once")
        kinds.add(kind)
        if not isinstance(entry.get("describes"), str) or not entry.get("describes"):
            f.error(
                f"frontmatter: {where}.describes must be a non-empty string saying what "
                f"one member of this kind is"
            )
        anchor = _brief_line_of(raw_lines, f"kind: {kind}", 1)
        declared = _inventory_declared(entry, data, where, f)
        sources = _kind_sources(entry, where, f)
        if declared is None or sources is None:
            continue
        yielded: dict[str, tuple[str, int]] = {}
        for j, source in enumerate(sources):
            for member, src_file, src_line in _source_ids(
                repo_root, source, f"{where}.sources[{j}]", f
            ):
                yielded.setdefault(member, (src_file, src_line))
        _report_kind(kind, declared, yielded, (entry, brief_name, raw_lines, anchor), f)


def check_frontmatter_paths(
    data: dict[str, Any],
    repo_root: Path,
    brief_path: Path,
    declared_external: list[str],
    f: Findings,
) -> None:
    """Resolve the paths the FRONTMATTER names, and report an exemption that has expired.

    ``check_repo_paths`` reads the body only, so the frontmatter — the half a recipient's
    LLM parses first and quotes most literally — was the one part of the brief no
    resolution ever touched. Two rules, both cheap:

    * ``external_surface.scripts[].path`` is spec'd as a repo-relative path, so it is
      resolved exactly rather than by suffix. Uncommitted and undeclared is a warning,
      for the same reason the body rule is one: a brief legitimately names a script in a
      generated tree, and ``external_paths`` is where that is said out loud (R-06).
    * An ``external_paths`` entry the repo DOES commit is a warning in the other
      direction. The list exempts paths the repo does not have; an entry the repo now
      has is exempting nothing, and its comment — the only thing saying why it was ever
      there — has quietly become false.

    **Both rules ask git, never the filesystem**, and the difference is not academic.
    Written against ``Path.exists()`` this fired on ``.telemetry/build.jsonl``, whose
    exemption reads "gitignored, so absent in any clone" and is TRUE — the file is on the
    author's disk and in nobody's checkout — and on a directory that survived only as a
    stale ``__pycache__``. Both are the same mistake ``_git_tracks`` was written for one
    rule up: gate scope is what is COMMITTED, and a warning that fires on the author's
    working tree is a warning every reader learns to skip. Where git cannot answer, both
    rules abstain rather than guess.

    Reads the brief itself for line numbers rather than taking them as a sixth
    parameter: a finding that cannot say WHERE is not actionable, and one re-read of a
    file already in the page cache is not worth an argument list nobody can remember.
    """
    brief_name = brief_path.name
    try:
        raw_lines = brief_path.read_text(encoding="utf-8").splitlines()
    except OSError:  # pragma: no cover - the caller just read this file
        raw_lines = []
    external = set(declared_external)
    surface = data.get("external_surface")
    scripts = surface.get("scripts") if isinstance(surface, dict) else None
    for entry in scripts or []:
        value = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(value, str) or not value.strip():
            continue
        rel = value.strip().rstrip("/")
        if rel in external or _git_tracks(repo_root, rel) is not False:
            continue
        f.warning(
            f"{brief_name}:{_brief_line_of(raw_lines, rel, 1)}: "
            f"external_surface.scripts names `{rel}`, which this repo does not commit "
            f"and which is not declared in frontmatter `external_paths`"
        )
    for rel in declared_external:
        if not rel or _git_tracks(repo_root, rel) is not True:
            continue
        f.warning(
            f"{brief_name}:{_brief_line_of(raw_lines, rel, 1)}: `external_paths` "
            f"exempts `{rel}`, which this repo commits — the exemption no longer exempts "
            f"anything, and the reason beside it is stale."
        )


# A bundle member's KIND, and the whole of what distinguishes the two schemas below.
# `dossier` carries the enumerations and the losslessness invariant; `summary` carries
# synthesis and not one enumerated list. Splitting by kind is what keeps the pair clear
# of P-02: they share no facts, so neither is a second home for the other. Default
# `dossier`, because a single-file brief IS the complete one and every brief written
# before this key existed is that.
ROLE_VALUES = {"dossier", "summary"}


def member_role(data: dict[str, Any] | None) -> str:
    """The declared role of one bundle member; ``dossier`` when unstated."""
    if not isinstance(data, dict):
        return "dossier"
    role = data.get("role")
    return role if role in ROLE_VALUES else "dossier"


def validate_frontmatter(
    frontmatter_text: str, f: Findings, repo_root: Path | None = None
) -> dict[str, Any] | None:
    """Parse and validate the YAML frontmatter; return it as a dict, or None.

    ``repo_root`` is optional so a caller validating frontmatter in isolation still
    works; it only sharpens the R-08 message when a data-plane tree turns out to sit
    inside the checkout.

    Two schemas, selected by ``role``. Everything a bundle needs to be *identified* —
    generator, target, bundle, the version stamp — is required of both members, because
    a reader holding one file must be able to tell which system and which revision it
    describes. Everything that ENUMERATES is required of the dossier and forbidden on
    the summary: an ``entities`` block or an ``inventory`` on a summary would put the
    same member list in two files, which is the exact defect the split exists to avoid.
    """
    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        f.error(f"frontmatter: YAML parse error — {exc}")
        return None
    if not isinstance(data, dict):
        f.error("frontmatter: top-level must be a mapping")
        return None
    if "role" in data and data["role"] not in ROLE_VALUES:
        f.error(
            f"frontmatter: role must be one of {sorted(ROLE_VALUES)} when present; "
            f"got {data['role']!r}"
        )
    role = member_role(data)
    # Required of both roles: what identifies the artifact.
    if "generator" not in data:
        f.error("frontmatter: generator key is required")
    else:
        validate_generator(data["generator"], f)
    if "target" not in data:
        f.error("frontmatter: target key is required")
    else:
        validate_target(data["target"], f)
    if "bundle" not in data:
        f.error("frontmatter: bundle key is required")
    if role == "summary":
        for enumerating in (
            "entities",
            "relationships",
            "external_surface",
            "inventory",
        ):
            if enumerating in data:
                f.error(
                    f"frontmatter: `{enumerating}` may not appear on a `role: summary` "
                    f"member. The summary carries synthesis and no enumerated list; the "
                    f"members live once, in the dossier (llm-summary/SPEC.md, "
                    f"'Two documents, split by kind')."
                )
    else:
        if "entities" not in data:
            f.error("frontmatter: entities key is required (use [] only if truly none)")
        else:
            validate_entities(data["entities"], f, repo_root)
        if "relationships" in data:
            validate_relationships(data["relationships"], f)
        if "external_surface" in data:
            validate_external_surface(data["external_surface"], f)
    if "external_paths" in data:
        validate_external_paths(data["external_paths"], f)
    # `internal_contracts` and `configuration` are spec'd as body-only (markdown
    # bullets in §2.5 and §2.6); a frontmatter key by those names is silently
    # accepted but not validated. Body-heading presence is enforced separately.
    return data


# ---------------------------------------------------------------------------
# Body structural checks
# ---------------------------------------------------------------------------


# Required body headings: (raw heading text, required depth).
REQUIRED_HEADINGS: tuple[tuple[str, int], ...] = (
    ("§1. Map", 2),
    ("§1.1 Purpose", 3),
    ("§1.2 Modules", 3),
    ("§1.3 Vendors", 3),
    ("§2. Implementation", 2),
    ("§2.1 Runtime", 3),
    ("§2.2 Entities", 3),
    ("§2.3 Relationships", 3),
    ("§2.4 External surface", 3),
    ("§2.5 Internal contracts", 3),
    ("§2.6 Configuration", 3),
    ("§2.7 Flows", 3),
    ("§2.8 Seams", 3),
    ("§2.9 Design decisions", 3),
    ("§2.10 Operational", 3),
    ("§2.11 Weirdness", 3),
    ("§3. Live access", 2),
    ("§3.1 Environments", 3),
    ("§3.2 Auth", 3),
    ("§3.3 Operations", 3),
    ("§3.4 Rate limits", 3),
    ("§3.5 Errors", 3),
    ("§3.6 SDKs", 3),
    ("Confidence", 2),
)


HEADING_RE = re.compile(r"^(#+)\s+(.+?)\s*$")
STUB_RE = re.compile(r"^N/A\s+—\s+", re.MULTILINE)


def find_headings(body: str) -> list[tuple[int, int, str, int]]:
    """Return ``[(lineno, depth, text, char_offset), ...]`` for body headings."""
    headings: list[tuple[int, int, str, int]] = []
    in_fence = False
    offset = 0
    for lineno, line in enumerate(body.splitlines(keepends=True), start=1):
        stripped = line.rstrip("\n")
        if stripped.startswith("```"):
            in_fence = not in_fence
            offset += len(line)
            continue
        if not in_fence:
            m = HEADING_RE.match(stripped)
            if m:
                headings.append((lineno, len(m.group(1)), m.group(2), offset))
        offset += len(line)
    return headings


def is_stubbed(body: str, char_offset: int, heading_line_len: int) -> bool:
    """A heading is 'stubbed' if `N/A — ` appears within ~200 chars after it."""
    window = body[char_offset + heading_line_len : char_offset + heading_line_len + 200]
    return bool(STUB_RE.search(window))


def normalize_heading(text: str) -> str:
    """Lowercase + collapse whitespace for tolerant heading matching."""
    return re.sub(r"\s+", " ", text.strip().lower())


def check_required_headings(body: str, f: Findings) -> None:
    """Verify the markdown body carries every required narrative heading."""
    headings = find_headings(body)
    headings_by_text: dict[str, list[tuple[int, int, int]]] = {}
    for lineno, depth, text, offset in headings:
        headings_by_text.setdefault(normalize_heading(text), []).append(
            (lineno, depth, offset)
        )
    for required_text, required_depth in REQUIRED_HEADINGS:
        key = normalize_heading(required_text)
        matches = headings_by_text.get(key, [])
        if not matches:
            f.error(
                f"body: required heading missing — expected H{required_depth} '{required_text}'"
            )
            continue
        # If any match has the right depth, accept it; otherwise flag.
        if not any(depth == required_depth for _, depth, _ in matches):
            actual_depths = sorted({d for _, d, _ in matches})
            f.error(
                f"body: heading '{required_text}' present at depth(s) "
                f"{actual_depths} but spec requires H{required_depth}"
            )


def check_confidence(body: str, f: Findings) -> None:
    """Confidence section: ≥3 'Least confident' bullets + ≥1 'Not in this brief' bullet."""
    idx = body.find("## Confidence")
    if idx < 0:
        # The required-headings pass already flagged this. Don't double-report.
        return
    section = body[idx:]
    # Find the two markers.
    least = re.search(r"\*\*Least confident\*\*", section)
    notin = re.search(r"\*\*Not in this brief\*\*", section)
    if not least:
        f.error("body: ## Confidence section missing '**Least confident**' marker")
    if not notin:
        f.error("body: ## Confidence section missing '**Not in this brief**' marker")
    if least and notin:
        # Bullets between the two markers belong to "Least confident".
        if least.start() < notin.start():
            least_text = section[least.end() : notin.start()]
            notin_text = section[notin.end() :]
        else:
            notin_text = section[notin.end() : least.start()]
            least_text = section[least.end() :]
        least_bullets = count_bullets(least_text)
        notin_bullets = count_bullets(notin_text)
        if least_bullets < 3:
            f.error(
                f"body: ## Confidence requires ≥3 'Least confident' bullets; found {least_bullets}"
            )
        if notin_bullets < 1:
            f.error(
                f"body: ## Confidence requires ≥1 'Not in this brief' bullet; found {notin_bullets}"
            )


def count_bullets(text: str) -> int:
    """Count top-level markdown bullets (`- ` or `* ` at column 0) until next H2/EOF."""
    count = 0
    for line in text.splitlines():
        if line.startswith("## "):
            break
        if re.match(r"^[-*]\s+\S", line):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Bundle integrity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Repo-path resolution
# ---------------------------------------------------------------------------

# A backticked span is treated as a repo path only when it is unambiguously
# one. Everything below is deliberately conservative: the cost of a false
# positive here is a reader learning to ignore the check.
_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_PATH_SUFFIXES = (
    ".md",
    ".py",
    ".toml",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".txt",
    ".json",
    ".jsonl",
    ".mk",
    ".sh",
    ".service",
)


_SKIP_DIR_PARTS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}


def _path_candidates(body: str) -> list[tuple[int, str]]:
    """Return ``[(lineno, path), ...]`` for backticked spans that name a repo path."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for lineno, raw in enumerate(body.splitlines(), start=1):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for span in _CODE_SPAN_RE.findall(raw):
            token = span.strip().rstrip(".,;:)")
            token = re.sub(r":\d+(-\d+)?$", "", token)  # file:line citation
            token = token.rstrip("/")
            if token.startswith("./"):
                token = token[2:]
            if not token or " " in token or "\t" in token:
                continue  # a command or a phrase, not a path
            if "://" in token or token[0] in "-$~#@^":
                continue  # URL, flag, shell var, home-relative, anchor, regex
            if any(c in token for c in "*?<>|="):
                continue  # glob or placeholder, e.g. docs/cli/**, src/<pkg>
            if "/" not in token and not token.endswith(_PATH_SUFFIXES):
                continue  # bare identifier, module path, or literal
            out.append((lineno, token))
    return out


def _under_hidden(token: str) -> bool:
    """Whether any segment of `token` starts with a dot, and so is outside the search.

    `repo_files` uses `glob`, which will not match a leading dot -- deliberately, so a
    checker never grades `.venv` or an agent worktree under `.claude`. The cost is that a
    real path under a dot-directory reads as absent, and the warning has to say so rather
    than assert the file is missing.
    """
    return any(part.startswith(".") for part in token.split("/"))


# One walk of what the filesystem carries, per repo root, per process -- the same
# reasoning as `_TRACKED_CACHE` above, applied here because `_repo_path_suffixes` is
# called once per bundle member (`validate_member` -> `check_repo_paths`) and the repo
# does not change between members in one run. Unmemoized this walked the whole tree
# again for every member; measured as the majority of this script's own runtime on a
# 2-member bundle. Safe to share the same `set` object across calls: every caller only
# reads it (`|`, `in`), never mutates it in place.
_SUFFIXES_CACHE: dict[str, set[str]] = {}


def _repo_path_suffixes(repo_root: Path) -> set[str]:
    """Every trailing path fragment of every file and directory in the repo.

    A brief names fragments, not root-relative paths — ``api/host.py`` for
    ``src/racecar/api/host.py``, ``PRINCIPLES.md`` for the one under
    ``architecture/``. Matching on suffixes is what makes the check agree with
    how briefs are actually written.

    The filesystem, deliberately, where the frontmatter rules ask git. They are
    different questions. There, "is this committed" decides in both directions and a
    gitignored file must read as absent. Here the set only decides whether to STAY
    SILENT, so a wider set produces fewer false warnings and a git-scoped one would newly
    accuse a brief of naming a path that is merely gitignored. Consistency between the
    two would be consistency in the wrong direction.
    """
    key = str(repo_root)
    cached = _SUFFIXES_CACHE.get(key)
    if cached is not None:
        return cached
    suffixes: set[str] = set()
    for p in repo_files(repo_root, "*"):
        rel = p.relative_to(repo_root)
        if _SKIP_DIR_PARTS & set(rel.parts):
            continue
        parts = rel.parts
        for i in range(len(parts)):
            suffixes.add("/".join(parts[i:]))
    _SUFFIXES_CACHE[key] = suffixes
    return suffixes


def check_repo_paths(
    body: str, repo_root: Path, declared_external: list[str], f: Findings
) -> None:
    """Warn on any backticked repo path in the brief that matches nothing on disk.

    Surfaced, never gated. A brief legitimately names paths that are not in its
    own repo — an adopter's tree, a generated server, a deleted file cited
    beside the commit that removed it — so an unresolvable path is not always a
    defect. That is the advisory case in ``architecture/PRINCIPLES.md`` R-06:
    detect and surface where the forbidden state has a legitimate instance,
    gate only where it never does.

    The legitimate instances are declared once, in the frontmatter's
    ``external_paths``, rather than guessed at here. Declaring beats hiding for
    the reason ``architecture/R10-trust/README.md`` gives about omissions: a declared
    exemption is visible, reviewable, and attributable to a decision, while a
    heuristic that silently swallows the same paths is none of those.
    """
    candidates = _path_candidates(body)
    if not candidates:
        return
    known = _repo_path_suffixes(repo_root) | set(declared_external)
    seen: set[str] = set()
    for lineno, token in candidates:
        if token in seen or token in known:
            continue
        seen.add(token)
        # The message states what was SEARCHED, not what exists. `repo_files` skips
        # hidden directories, so a path under one is invisible here whether or not it
        # is on disk -- `deploy-server/.collections` is 44M of real, present files and
        # was reported as matching nothing. A warning that names a cause the reader can
        # check is a warning they can act on; "does not exist" sent them looking for a
        # path that was sitting right there.
        hidden = " (hidden paths are not searched)" if _under_hidden(token) else ""
        f.warning(
            f"body:{lineno}: names `{token}`, which nothing in the searched tree "
            f"matches{hidden} and which frontmatter `external_paths` does not declare — "
            f"stale reference, or an external path that should say so"
        )


def check_bundle_membership(
    brief_path: Path, declared_bundle: list[str], f: Findings
) -> None:
    """`bundle:` list must exactly match the sibling `.md` files in the dir."""
    if not declared_bundle:
        return
    on_disk = {
        p.name for p in brief_path.parent.iterdir() if p.is_file() and p.suffix == ".md"
    }
    declared = set(declared_bundle)
    missing = declared - on_disk
    orphans = on_disk - declared
    for name in sorted(missing):
        f.error(
            f"bundle: '{name}' is listed in frontmatter bundle but not present in "
            f"{brief_path.parent}"
        )
    for name in sorted(orphans):
        f.error(
            f"bundle: '{name}' exists in {brief_path.parent} but is not listed in "
            f"frontmatter bundle"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Handoff challenge — the other axis, and the one this script cannot gate
# ---------------------------------------------------------------------------
#
# Completeness answers "was the fact in the document". It does not answer "did the
# reader attend to it", and until both are answered no failure of a brief's recipient is
# attributable: an answer that misses a fact is indistinguishable from a brief that
# omitted it. The invariant above closes the first door only.
#
# racecar already owns an instrument for the second door, for a different artifact.
# `hooks/session_load_standards.py` plants a content-derived `Load token:` and
# `doctor/SKILL.md` challenges the model to produce it FROM CONTEXT ONLY. It works
# because of a property the brief does not have: a VERIFIER holding the answer
# independently of the party being tested — `doctor.py` recomputes the hash from the
# bytes on disk. A token printed INSIDE the brief has no such verifier. The recipient is
# both the tested party and the only holder of the file, so echoing a string from the
# frontmatter proves the top of the file is in context and nothing about the body; a
# token they would have to COMPUTE is a sha256 no language model can do.
#
# What transfers is the SHAPE, not the token: put the verifier back beside the artifact.
# These probes are derived from the brief's own body by this script, printed to the
# AUTHOR, and never written into the file. The author asks them at handoff and holds the
# answers. Deterministic, so a re-run reproduces them; body-anchored, so the answers are
# not in the frontmatter a truncating host keeps; exactly gradeable, so no model sits in
# the loop (R-03).
#
# It reports and never gates: this script runs in the author's repo before handoff, and
# the reader is in another chat on another machine with no channel back. A check cannot
# grade an event it cannot observe, and pretending otherwise would be the professed rule
# R-02 forbids, inside the document that argues against it.


def _last_h3(body: str) -> str | None:
    """The text of the final H3 heading — a tail anchor a truncated read cannot reach."""
    h3s = [text for _, depth, text, _ in find_headings(body) if depth == 3]
    return h3s[-1] if h3s else None


def _challenge_member(brief_path: Path) -> list[str]:
    """The probe lines for one bundle member, answers included.

    Four questions, three of them anchored past the middle of the file so a partial read
    fails at least one. All four are STRUCTURAL rather than section-specific, because the
    two roles are organised differently — a probe naming ``§2. Implementation`` returns 0
    on a summary, which is a degenerate answer masquerading as a real one.
    """
    text = brief_path.read_text(encoding="utf-8")
    _, body = split_frontmatter(text)
    section = body[body.find("## Confidence") :] if "## Confidence" in body else ""
    least = re.search(r"\*\*Least confident\*\*", section)
    notin = re.search(r"\*\*Not in this brief\*\*", section)
    doubts = (
        count_bullets(section[least.end() : notin.start()])
        if least and notin and least.start() < notin.start()
        else 0
    )
    headings = find_headings(body)
    token = sha256(text.encode("utf-8")).hexdigest()[:12]
    return [
        f"challenge: {brief_path.name}  token {token}",
        f"  1. How many H2 sections does {brief_path.name} carry? -> "
        f"{sum(1 for _, d, _, _ in headings if d == 2)}",
        f"  2. How many H3 subsections in total? -> "
        f"{sum(1 for _, d, _, _ in headings if d == 3)}",
        f"  3. What is its LAST H3 heading? -> {_last_h3(body)}",
        f"  4. How many bullets follow '**Least confident**'? -> {doubts}",
    ]


def challenge(brief_path: Path) -> int:
    """Print handoff probes for EVERY bundle member, for the AUTHOR to hold.

    Per member, because the read axis is about the bundle: a recipient who attached only
    one file will answer that file's probes and fail the other's, which is precisely the
    failure worth catching and the one a single-file probe set cannot see.

    Each member's token is artifact IDENTITY — which revision does the recipient hold —
    and is deliberately not offered as evidence of reading, because a string the reader
    can copy proves only that they can copy it.
    """
    print(
        "challenge: ask these before anything else; the answers are yours, not theirs"
    )
    for line in _challenge_member(brief_path):
        print(line)
    text = brief_path.read_text(encoding="utf-8")
    frontmatter_text, _ = split_frontmatter(text)
    data = yaml.safe_load(frontmatter_text) if frontmatter_text else None
    members = data.get("bundle") if isinstance(data, dict) else None
    for name in members or []:
        sibling = brief_path.parent / str(name)
        if not sibling.is_file() or sibling.resolve() == brief_path.resolve():
            continue
        for line in _challenge_member(sibling):
            print(line)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the brief check."""
    parser = argparse.ArgumentParser(
        description="Mechanical validator for a racecar-llm-summary brief."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to the main brief. If omitted, discovered via .git walk-up.",
    )
    parser.add_argument(
        "--challenge",
        action="store_true",
        help=(
            "Print handoff probes and the artifact token for the author to hold, "
            "then exit 0. Validates nothing; see SPEC.md on the read axis."
        ),
    )
    return parser.parse_args(argv)


def validate_member(brief_path: Path, f: Findings, is_entry: bool) -> list[str]:
    """Validate one bundle member; return the ``bundle`` list it declares.

    ``is_entry`` marks the file the caller named or discovery found. Only it checks
    bundle membership against the directory — asking every member the same question
    would report one orphan once per member.
    """
    text = brief_path.read_text(encoding="utf-8")
    frontmatter_text, body = split_frontmatter(text)

    if frontmatter_text is None:
        f.error(
            f"frontmatter: no YAML frontmatter block found at top of {brief_path} "
            f"(expected '---' fences before the H1)"
        )
        # Still attempt body checks against the entire text so users see all drift.
        check_required_headings(text, f)
        check_confidence(text, f)
        return []

    repo_root = find_repo_root(brief_path.resolve().parent)
    data = validate_frontmatter(frontmatter_text, f, repo_root)
    role = member_role(data)
    if role == "dossier":
        # The §N.M spine is the dossier's shape. A summary is organised by subsystem and
        # by question, so imposing this heading set on it would be imposing the dossier's
        # structure on the document written to escape it.
        check_required_headings(body, f)
    check_confidence(body, f)
    external_paths = (
        validate_external_paths(data["external_paths"], f)
        if data is not None and "external_paths" in data
        else []
    )
    check_repo_paths(body, repo_root, external_paths, f)
    bundle: list[str] = []
    if data is not None and isinstance(data.get("bundle"), list):
        bundle = validate_bundle(data["bundle"], f)
        if is_entry:
            check_bundle_membership(brief_path, bundle, f)
    if data is not None:
        check_frontmatter_paths(data, repo_root, brief_path, external_paths, f)
        if role == "dossier":
            check_inventory(data, repo_root, brief_path, text.splitlines(), f)
        check_version_stamp(data, repo_root, f)
    return bundle


def main(argv: list[str] | None = None) -> int:
    """Validate every member of the bundle reached from ``docs/summary/<REPO>.md``.

    The unit is the BUNDLE, not the file. A two-member bundle splits the enumerations
    into the dossier and the synthesis into the summary, and grading only the file the
    caller happened to name would leave the other ungraded — including, in the common
    case, the one carrying the losslessness invariant. ``bundle:`` already names the
    files a recipient must be handed together; this makes that list load-bearing.
    """
    args = parse_args(argv if argv is not None else sys.argv[1:])
    f = Findings()

    if args.path:
        brief_path = Path(args.path)
        if not brief_path.is_file():
            f.error(f"discovery: brief not found at {brief_path}")
            return emit(f)
    else:
        discovered = discover_brief_path()
        if discovered is None:
            # Every candidate, not just the first: naming one of two is what sends a
            # reader looking for a file the repo was never going to have.
            expected = " or ".join(str(c) for c in brief_candidates(find_repo_root()))
            f.error(f"discovery: no brief found at conventional path {expected}")
            return emit(f)
        brief_path = discovered

    if args.challenge:
        return challenge(brief_path)

    bundle = validate_member(brief_path, f, is_entry=True)
    for name in bundle:
        sibling = brief_path.parent / name
        if sibling.resolve() == brief_path.resolve():
            continue
        if not sibling.is_file():
            continue  # already reported by check_bundle_membership
        validate_member(sibling, f, is_entry=False)

    return emit(f)


def _version_home_value(root: Path) -> tuple[str | None, str | None]:
    """Return the repo's current version, resolved through the ONE home for that rule.

    Loads the delivered ``scripts/check_version_bump.py`` and calls its
    ``version_home`` rather than re-reading ``pyproject.toml`` here. COMMITS.md
    "Version home" has exactly one resolver, and a second copy in this file is the
    P-02 failure that standard names — the two would disagree the first time a repo
    migrated from ``VERSION`` to ``[project].version``.

    Returns ``(version, None)`` when it resolves, and ``(None, reason)`` when it cannot.
    The stamp check is then SKIPPED rather than guessed -- an advisory that invents its
    baseline is worse than one that abstains -- but the skip is REPORTED, which is the
    half that was missing. Returning a bare None gave three different causes one silent
    answer, so a syntax error in a sibling script disabled an error-severity rule and
    printed nothing: the brief said OK having never compared the stamp to anything. That
    is the one rule here a brief's recipient structurally cannot check for themselves,
    since they hold the file and no repo to hold it against.
    """
    script = root / "scripts" / "check_version_bump.py"
    if not script.is_file():
        return None, f"{script.name} is not in this repo, so there is no version home"
    try:
        spec = importlib.util.spec_from_file_location("_brief_version_bump", script)
        if spec is None or spec.loader is None:
            return None, f"{script.name} could not be loaded as a module"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        home = module.version_home(root)
    except Exception as exc:  # pylint: disable=broad-except
        # A broken sibling must never FAIL the brief checker -- but it must not pass
        # silently either, or the sibling's breakage reads as this rule's success.
        return None, f"{script.name} raised {type(exc).__name__}: {exc}"
    if not home:
        return None, f"{script.name} resolved no version home in this repo"
    return home[1], None


def check_version_stamp(data: dict[str, Any], root: Path, f: Findings) -> None:
    """Fail when the brief's ``target.version`` has fallen behind the version home.

    The stamp compared here is ``target.version`` — the version of the repo the brief
    *describes*. ``generator.version`` is racecar's own version (SPEC.md: "from
    racecar's ``[project].version``"), and the two are only ever equal in racecar's
    own tree. Comparing the generator stamp against an adopter's version home asserts
    that two unrelated projects share a version line, which fails permanently for every
    adopter and passes only where the check has nothing to catch.

    This is the one drift a recipient structurally cannot detect: they hold the file
    and no repo to compare it against, so a brief describing an older tree reads
    exactly like a current one. The schema check above only asserts the stamp is
    *semver-shaped*, never that it is *true*.

    An ERROR, not a warning, because nothing makes a lag legitimate. The schema pins
    the brief to no commit — there is no SHA in the frontmatter, only this stamp and
    ``target.date`` — so the brief is a document like any other and travels with the
    commit that invalidates it. The stamp is one line and moves with the version home
    in the same commit; the body moves only when a commit changes what it asserts, and
    then by editing the affected lines. "Regenerate" is the word that used to smuggle
    in a whole-file rewrite and with it the excuse for a separate, later commit.
    """
    target = data.get("target")
    if not isinstance(target, dict):
        return
    stamp = target.get("version")
    if not isinstance(stamp, str):
        return  # already reported by the schema check; do not double-report
    current, unresolved = _version_home_value(root)
    if unresolved is not None:
        f.warning(f"frontmatter: target.version not compared -- {unresolved}")
        return
    if stamp == current:
        return
    f.error(
        f"frontmatter: target.version is {stamp} but the repo is at {current} — "
        f"the brief describes an older tree. Bring it current in the commit that moved "
        f"past it: the stamp is one line, and the body changes only where this commit "
        f"changed what the brief asserts."
    )


def emit(f: Findings) -> int:
    """Print all findings and return 1 if any error was recorded, else 0."""
    for severity, msg in f.entries:
        print(f"check_brief: {severity}: {msg}")
    if not f.error_count and not f.warning_count:
        print("check_brief: OK")
        return 0
    print(f"check_brief: {f.error_count} errors, {f.warning_count} warnings")
    return 1 if f.error_count else 0


if __name__ == "__main__":
    sys.exit(main())
