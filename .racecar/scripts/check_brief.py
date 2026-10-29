#!/usr/bin/env python3
"""Mechanical validator for a racecar-llm-summary brief bundle.

Validates a brief produced by the ``racecar-llm-summary`` generator against
the schema and structural budget declared in
``llm-summary/README.md`` (sections ``## Frontmatter (YAML)`` and
``## Structural budget``).

Checks performed:

  1. Frontmatter YAML parses and matches the declared schema:
     - ``generator.name``, ``generator.version`` (semver "X.Y.Z") — racecar's
       version. Shape only; never compared against the brief'd repo.
     - ``target.repo``, ``target.date`` (ISO YYYY-MM-DD), ``target.version``
       (semver "X.Y.Z") — the brief'd repo's version, checked against that
       repo's version home.
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
       each naming the sources that yield its members from the tree. See check 3.

  2. Structural body checks:
     - Every §N.M with a body component appears as a heading at the depth
       declared by the spec (H2 for §1/§2/§3 and Confidence; H3 for §N.M
       subsections). A heading whose body is an ``N/A — `` line counts as a
       stubbed-and-OK section.
     - ``## Confidence`` carries ≥3 bullets after a ``**Least confident**``
       marker and ≥1 bullet after a ``**Not in this brief**`` marker.
     - ``bundle:`` frontmatter list exactly matches the set of ``*.md`` files
       in the bundle directory — no orphans, no missing.
     - §2.4 frontmatter surface keys: any key with >5 entries must be a
       first-class recognized kind (``http_routes``, ``cli_verbs``,
       ``mcp_tools``, ``library_exports``, ``webhooks``, ``signals``).

  3. Conformance. For every kind in ``inventory``, the declared member set and
     the set the tree yields under that kind's sources must be equal:
     - a declared member no source yields is a PHANTOM (error, at the brief's
       own ``file:line``);
     - a yielded member the brief does not declare is an OMISSION (error, at
       the yielding ``file:line``).
     Also resolves the paths the frontmatter names (``external_surface``
     ``scripts[].path``) and reports an ``external_paths`` exemption that has
     stopped being an exemption. "Is this in the repo" means ``git ls-files``,
     never the filesystem.

  4. ``--challenge`` (reports only; always exit 0). Prints handoff probes with
     their answers for the author, plus a token identifying which revision of
     the brief a recipient has. See ``llm-summary/SPEC.md``, "The other half".

  5. Section review records, on the authored members. Each prose-carrying H2 and H3
     carries a comment naming the files it describes and git's object id for each at its
     last review. Construction is an error (a missing comment, an uncommitted source, a
     ``target.version`` other than the oldest section's); a section whose sources moved
     since is a warning. ``--stale`` lists those with the diffs to read, and
     ``--reviewed <member>:<line>`` records a re-read. See ``llm-summary/SPEC.md``,
     "Section review records".

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
    python3 <path-to>/check_brief.py [<bundle-path>] [--challenge | --apply | --stale]
    python3 <path-to>/check_brief.py --reviewed <member>:<line>

Complexity: O(F + L), F = files/paths walked in the repo tree (git ls-files, the
os.walk-based repo_files scan, and its per-path suffix index), L = lines read across
glob-matched inventory sources -- linear in the size of the repo being validated, not
the brief.
"""

# pylint: disable=too-many-lines
# Delivered to adopters as one file by `racecar sync`, so the module-length cap is waived here.

from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date as date_cls
from hashlib import sha1, sha256
from pathlib import Path
from typing import Any, TypeGuard

from lib.shared import _commits, _frontmatter, _markdown
from lib.shared._constants import RELEASE_RE
from lib.shared._files import git_ls, git_scope, repo_files
from lib.shared._report import Findings, emit
from lib.shared._root import find_repo_root, package_root, packages
from lib.shared._slug import brief_candidates, discover_brief

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

    `$REPO` is resolved by `lib.shared._slug`, which tries the directory
    basename and then `[project].name`, so a git worktree resolves the same brief.
    """
    return discover_brief(find_repo_root())


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Schema validators
# ---------------------------------------------------------------------------


ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOWERCASE_REPO_RE = re.compile(r"^[a-z0-9_-]+$")

CARDINALITY_VALUES = {"1:1", "1:N", "M:N"}
ON_DELETE_VALUES = {"CASCADE", "PROTECT", "SET_NULL", "DO_NOTHING", "RESTRICT"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
ENTITY_CASES = {"db_backed", "on_disk_managed", "content_tree", "none"}
LIFECYCLE_VALUES = {"realized", "deprecated", "planned"}
MUTABILITY_VALUES = {"read-only", "mutable"}
# Whether a store's records were published by their source. Default private.
DISCLOSURE_VALUES = {"private", "public"}
# R-07's planes, as a brief declares them. Required to carry `path_pattern` or
# `count` (PRINCIPLES.md R-08).
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

    A TypeGuard, so callers narrow on the fall-through.
    """
    if not isinstance(value, list):
        f.error(f"frontmatter: {where} must be a list; got {type(value).__name__}")
        return False
    return True


def validate_generator(generator: object, f: Findings, role: str = "dossier") -> None:
    """Validate the frontmatter `generator` block.

    The generated inventory carries no `version`: a delivered script in an adopter
    cannot know racecar's version.
    """
    if not require_mapping(generator, "generator", f):
        return
    assert isinstance(generator, dict)
    name = generator.get("name")
    if name != "racecar-llm-summary":
        f.error(
            f"frontmatter: generator.name must be 'racecar-llm-summary'; got {name!r}"
        )
    version = generator.get("version")
    if role == "inventory":
        return
    if not isinstance(version, str) or not RELEASE_RE.match(version):
        f.error(
            f"frontmatter: generator.version must be semver 'X.Y.Z'; got {version!r}"
        )
    # Shape only: racecar's version, not the brief'd repo's.


def validate_target(target: object, f: Findings, role: str = "dossier") -> None:
    """Validate the frontmatter `target` block.

    The generated inventory carries no `date`, so regeneration is byte-stable.
    """
    if not require_mapping(target, "target", f):
        return
    assert isinstance(target, dict)
    repo = target.get("repo")
    if not isinstance(repo, str) or not repo:
        f.error(f"frontmatter: target.repo must be a non-empty string; got {repo!r}")
    elif not LOWERCASE_REPO_RE.match(repo):
        f.error(f"frontmatter: target.repo must be lowercase [a-z0-9_-]; got {repo!r}")
    date = target.get("date")
    if role == "inventory":
        pass
    elif isinstance(date, str):
        if not ISO_DATE_RE.match(date):
            f.error(f"frontmatter: target.date must be ISO YYYY-MM-DD; got {date!r}")
    else:
        # PyYAML may parse YYYY-MM-DD as datetime.date — accept that.
        if not isinstance(date, date_cls):
            f.error(f"frontmatter: target.date is required (ISO date); got {date!r}")
    version = target.get("version")
    if not isinstance(version, str) or not RELEASE_RE.match(version):
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


# One snapshot of what git carries, per repo root, per process.
def _tracked_files(repo_root: Path) -> frozenset[str] | None:
    """Every path git tracks in ``repo_root``, repo-relative posix; None if it cannot say.

    Every rule here that asks whether something is part of the repo asks this, not the
    filesystem. Scope is what is committed.
    """
    out = git_ls(repo_root, "-z")
    if out is None:
        return None
    return frozenset(p for p in out.split("\0") if p)


def _git_tracks(repo_root: Path, prefix: str) -> bool | None:
    """Whether git tracks anything at or under ``prefix``; None when git cannot answer.

    Tracked-ness, not existence on disk: a gitignored cache (``.data/``) exists on disk
    but is not committed. None means the question could not be asked (no git, or not a
    working tree); callers infer nothing from it.
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

    ``(None, None)`` when there is nothing to resolve or the prefix lands outside the
    checkout (an absolute pattern, or one that walks up with ``..``).
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
# one signature so ``validate_entity_plane`` can walk them. Adding a protected key is a
# function plus a row. A verifier corroborates its key against the tree; admissibility
# (R-08) is applied by the orchestrator.


def _verify_path_pattern(
    entity: dict[str, Any], where: str, f: Findings, repo_root: Path | None
) -> None:
    """Corroborate a ``path_pattern`` against what git carries.

    On the taxonomy plane, a path git does not carry contradicts the declaration: a
    warning, since build-time or packaged taxonomy is tracked nowhere here. On the data
    plane, a tracked tree means the store was committed into the checkout: reported on
    its own. Where git cannot answer, nothing is reported.
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

    Checks that a `lifecycle: planned` entity quotes no figure and that a numeric count
    is not negative. ``repo_root`` is unused; the registry calls every verifier alike.
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


def _verify_inventory(
    entity: dict[str, Any], where: str, f: Findings, repo_root: Path | None
) -> None:
    """Shape of an entity's ``inventory``: one kind name. Its existence is checked later.

    ``repo_root`` is unused; the registry calls every verifier alike.
    """
    del repo_root
    if not isinstance(entity.get("inventory"), str) or not entity["inventory"].strip():
        f.error(f"frontmatter: {where}.inventory must name one inventory kind")


# The keys that describe a store rather than the program. Adding one means a verifier
# above and a row here.
_KEY_VERIFIERS: dict[
    str, Callable[[dict[str, Any], str, Findings, Path | None], None]
] = {
    "path_pattern": _verify_path_pattern,
    "count": _verify_count,
    # Names the kind whose count the generated inventory carries; gated like `count`.
    "inventory": _verify_inventory,
}
PROTECTED_KEYS: tuple[str, ...] = tuple(_KEY_VERIFIERS)


def validate_entity_plane(
    entity: dict[str, Any], where: str, f: Findings, repo_root: Path | None = None
) -> None:
    """Gate the store-describing keys on the declared plane (PRINCIPLES.md R-08).

    ``path_pattern`` and ``count`` are admitted only on the taxonomy plane. ``name``,
    ``case``, ``purpose`` and ``notes`` are never gated. Bound to the keys, not to
    ``case``. A data-plane tree that git tracks is reported as a committed store.
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
        # Exempt from the disclosure rule, not from the shape checks.
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
    # content_tree entries. Enums are validated wherever they appear.
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
        # owner_side and on_delete are optional; validate enum membership when present.
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
# kind whose entries are accepted without per-key validation. `http_routes`
# additionally validates its `method` enum below.
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
# Everything above grades construction; what follows grades conformance: for each
# enumerable kind, the set the brief declares must equal the set the tree yields.
# A phantom is declared and not present; an omission is present and not declared.
#
# The extractor is declared, not executed: a kind names a glob and, optionally, a
# regex. The checker never runs a command from a document.

INVENTORY_MATCH_VALUES = {"path", "pattern"}

# `derivation: none — <reason>`: the oracle is not the tree. Reported on every run.
_DERIVATION_NONE_RE = re.compile(r"^none\s*[—-]\s*\S")

# `members_from: external_surface.cli_verbs[].verb` — one list, one scalar key from
# each of its entries.
_MEMBERS_FROM_RE = re.compile(r"^([A-Za-z_][\w.]*)\[\]\.([A-Za-z_]\w*)$")


def _glob_paths(repo_root: Path, expr: object, where: str, f: Findings) -> list[Path]:
    """Every committed path under ``repo_root`` matching ``expr``, sorted.

    Matching is ``pathlib`` globbing; scope is what git tracks. Where git cannot answer,
    every match is kept. An absolute pattern or one walking up out of the checkout is
    refused.
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

    Returns ``(id, repo-relative file, line)``; the line is 1.
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
    groups an ``id_format`` template composes with the path-derived fields.
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

    Where the frontmatter already carries the list (``external_surface`` ``cli_verbs``,
    ``scripts``, ``entities``), the inventory points at it.
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

    A kind with no extractor is admissible and reported as a warning.
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

    Required: a missing inventory is an error.
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
    """Resolve the paths the frontmatter names, and report an exemption that has expired.

    * ``external_surface.scripts[].path`` is resolved exactly as a repo-relative path.
      Uncommitted and undeclared in ``external_paths`` is a warning.
    * An ``external_paths`` entry the repo commits is a warning: it exempts nothing.

    Both rules ask git, never the filesystem, and abstain where git cannot answer.
    Reads the brief itself for line numbers.
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


# ---------------------------------------------------------------------------
# The generated inventory — a third bundle member no person edits
# ---------------------------------------------------------------------------
#
# `--apply` renders `<REPO>-INVENTORY.md` from the dossier's `inventory` declarations;
# the default run renders it in memory and fails when the committed bytes differ.
# Generating and checking share the extractor above.
#
# The generator reads and never runs the repo's code (llm-summary/SPEC.md). A column is
# one of three sources: a key of the matched JSON Lines row, the first docstring line of
# the member's file, or the `summary:` the lexicon node for a CLI verb declares.

INVENTORY_SUFFIX = "-INVENTORY.md"
REGENERATE = "python3 .racecar/scripts/check_brief.py --apply"
GENERATED_LINE = f"<!-- GENERATED — DO NOT EDIT. Regenerate:  {REGENERATE} -->"
_LEXICON_COLUMNS = {"summary"}
_EMPTY_CELL = "—"


def inventory_member(bundle: list[str]) -> str | None:
    """The bundle's generated member, named by its suffix; None on a two-file bundle."""
    found = [name for name in bundle if name.endswith(INVENTORY_SUFFIX)]
    return found[0] if found else None


def _column_spec(column: object, where: str, f: Findings) -> tuple[str, str] | None:
    """Parse one `columns` entry into ``(source, name)``, or report why it cannot be read."""
    if isinstance(column, str) and column.strip():
        name = column.strip()
        return ("docstring", name) if name == "docstring" else ("key", name)
    if isinstance(column, dict) and set(column) == {"lexicon"}:
        wanted = column["lexicon"]
        if wanted in _LEXICON_COLUMNS:
            return "lexicon", str(wanted)
        f.error(
            f"frontmatter: {where} asks the lexicon for {wanted!r}; it can answer "
            f"{sorted(_LEXICON_COLUMNS)}. A verb's usage line has no committed home — "
            f"`--help` is its one home, and the generator never runs the repo's code. "
            f"Read the flags from the spec row instead (e.g. its `params` key)."
        )
        return None
    f.error(
        f"frontmatter: {where} must be a row key, `docstring`, or `{{lexicon: summary}}`; "
        f"got {column!r}"
    )
    return None


def _cell_text(value: object) -> str:
    """One table cell: a list joined, nothing shown as a dash, pipes and newlines made safe."""
    if value is None or value == [] or value == "":
        return _EMPTY_CELL
    text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
    return " ".join(text.split()).replace("|", "\\|")


_LINES_CACHE: dict[Path, list[str]] = {}


def _lines_of(path: Path) -> list[str]:
    """A file's lines, read once per run."""
    if path not in _LINES_CACHE:
        try:
            _LINES_CACHE[path] = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            _LINES_CACHE[path] = []
    return _LINES_CACHE[path]


def _row_value(path: Path, line: int, key: str) -> str:
    """``key`` of the JSON Lines row on ``line`` of ``path``."""
    lines = _lines_of(path)
    try:
        row = json.loads(lines[line - 1])
    except (IndexError, ValueError):
        return f"(line {line} is not a JSON Lines row)"
    if not isinstance(row, dict) or key not in row:
        return f"(no `{key}` on this row)"
    return _cell_text(row[key])


def _first_doc_line(path: Path) -> str:
    """The first docstring line of a Python file, or the first comment of a shell script."""
    if path.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            doc = None
        first = doc.strip().splitlines()[0] if doc and doc.strip() else ""
        return _cell_text(first) if first else "(no docstring)"
    for raw in _lines_of(path)[:20]:
        text = raw.strip()
        if text.startswith("#!") or not text.startswith("#"):
            continue
        comment = text.lstrip("#").strip()
        if comment:
            return _cell_text(comment)
    return "(no leading comment)"


def _lexicon_summary(repo_root: Path, member: str) -> str:
    """The `summary:` of the lexicon node for a `python -m <module> <verb>` member.

    The verb-to-node mapping is asked of the delivered `lib.lexicon`.
    """
    parts = member.split()
    if len(parts) != 4 or parts[:2] != ["python", "-m"]:
        return f"(not a `python -m <module> <verb>` member: {member})"
    # Imported lazily: only a kind with a lexicon column needs the lexicon's import chain.
    lexicon = importlib.import_module("lib.lexicon")
    corpora = lexicon.lexicon_corpora(repo_root)
    if not corpora.entries:
        return f"(no lexicon at `{corpora.own.relative_to(repo_root).as_posix()}`)"
    module, verb = parts[2], parts[3]
    chain = module.split(".")[1:]
    noun = ".".join(chain) if chain else lexicon.root_noun(corpora)
    node = lexicon.verb_node(corpora, noun, verb)
    rel = node.relative_to(repo_root).as_posix()
    if not node.is_file():
        return f"(no lexicon node at `{rel}`)"
    summary = _frontmatter.load(node).get("summary")
    return _cell_text(summary) if summary else f"(`{rel}` declares no summary)"


def _cell(
    spec: tuple[str, str], repo_root: Path, member: str, located: tuple[str, int]
) -> str:
    """One column's value for one member, read from the home the column names."""
    source, name = spec
    path = repo_root / located[0]
    if source == "key":
        return _row_value(path, located[1], name)
    if source == "docstring":
        return _first_doc_line(path)
    return _lexicon_summary(repo_root, member)


def _kind_columns(
    entry: dict[str, Any], where: str, f: Findings
) -> list[tuple[str, str]]:
    """The parsed `columns` of one kind; an unreadable entry is reported and dropped."""
    raw = entry.get("columns", [])
    if not require_list(raw, f"{where}.columns", f):
        return []
    specs = [
        _column_spec(column, f"{where}.columns[{i}]", f) for i, column in enumerate(raw)
    ]
    return [spec for spec in specs if spec is not None]


def _source_globs(sources: list[object]) -> str:
    """The `yielded by` cell: every glob the kind's sources walk."""
    globs = [f"`{s['glob']}`" for s in sources if isinstance(s, dict) and s.get("glob")]
    return ", ".join(globs) or _EMPTY_CELL


def _render_kind(
    entry: dict[str, Any], repo_root: Path, where: str, f: Findings
) -> tuple[list[str], str] | None:
    """One kind's section and its `## Counts` row; None when the kind is declared-only."""
    kind = str(entry.get("kind")).strip()
    for stale in ("members", "members_from"):
        if stale in entry:
            f.error(
                f"frontmatter: {where}.{stale} may not appear when the bundle carries a "
                f"generated inventory: the generator emits the members, and a list written "
                f"beside it is a second home (P-02). Delete it."
            )
    sources = _kind_sources(entry, where, f)
    if sources is None:
        return None
    columns = _kind_columns(entry, where, f)
    yielded: dict[str, tuple[str, int]] = {}
    for j, source in enumerate(sources):
        for member, src_file, src_line in _source_ids(
            repo_root, source, f"{where}.sources[{j}]", f
        ):
            yielded.setdefault(member, (src_file, src_line))
    exempt = {
        e.strip()
        for e in (entry.get("exempt") or [])
        if isinstance(e, str) and e.strip()
    }
    for member in sorted(exempt - set(yielded)):
        f.warning(
            f"frontmatter: {where} (kind '{kind}') exempts '{member}', which no source "
            f"yields — a stale exemption."
        )
    kept = sorted(m for m in yielded if m not in exempt)
    header = ["member", *(name for _, name in columns), "yielded at"]
    lines = [
        f"## {kind}",
        "",
        _cell_text(entry.get("describes")),
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for member in kept:
        src_file, src_line = yielded[member]
        cells = [
            _cell_text(member),
            *(_cell(spec, repo_root, member, (src_file, src_line)) for spec in columns),
            f"`{src_file}:{src_line}`",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    count = (
        f"| {kind} | {len(kept)} | {len(exempt & set(yielded))} | "
        f"{_source_globs(sources)} |"
    )
    return lines, count


def _check_entity_kinds(data: dict[str, Any], kinds: set[str], f: Findings) -> None:
    """An entity's `inventory:` must name a declared kind; a typed `count` is refused."""
    for i, entity in enumerate(data.get("entities") or []):
        if not isinstance(entity, dict):
            continue
        where = f"entities[{i}]"
        if "count" in entity:
            f.error(
                f"frontmatter: {where}.count may not appear when the bundle carries a "
                f"generated inventory. Name the kind that enumerates this entity with "
                f"`inventory: <kind>`, and the inventory carries its count; a number typed "
                f"here is read by nothing and goes stale by the next commit."
            )
        named = entity.get("inventory")
        if named is not None and named not in kinds:
            f.error(
                f"frontmatter: {where}.inventory names {named!r}, which no `inventory` "
                f"kind declares; declared kinds are {sorted(kinds)}"
            )


def _inventory_frontmatter(repo: str, version: str, bundle: list[str]) -> list[str]:
    """The generated member's frontmatter, written as text: every value is a plain scalar."""
    return [
        "---",
        "role: inventory",
        "generator:",
        "  name: racecar-llm-summary",
        "target:",
        f"  repo: {repo}",
        f'  version: "{version}"',
        "bundle:",
        *(f"  - {name}" for name in bundle),
        "---",
    ]


def render_inventory(
    data: dict[str, Any], repo_root: Path, bundle: list[str], f: Findings
) -> str:
    """Render the inventory member from the dossier's declarations and the tree.

    Deterministic (P-05): sections follow the dossier's declaration order, rows sort by
    member, and nothing dated is written. The version is the version home's at
    generation; the runbook's stamp rewrite moves it with the other members' stamps.
    """
    _LINES_CACHE.clear()  # one render reads each file once, and never a previous render's copy
    raw_target = data.get("target")
    target: dict[str, Any] = raw_target if isinstance(raw_target, dict) else {}
    repo = str(target.get("repo") or repo_root.name.lower())
    version, _ = _version_home_value(repo_root)
    version = version or str(target.get("version") or "0.0.0")
    name = inventory_member(bundle) or ""
    position = bundle.index(name) + 1 if name in bundle else len(bundle)
    sections: list[str] = []
    counts: list[str] = []
    declared_only: list[str] = []
    kinds: set[str] = set()
    inventory = data.get("inventory")
    for i, entry in enumerate(inventory if isinstance(inventory, list) else []):
        where = f"inventory[{i}]"
        if not isinstance(entry, dict) or not str(entry.get("kind") or "").strip():
            f.error(f"frontmatter: {where} must be a mapping with a non-empty `kind`")
            continue
        kinds.add(str(entry["kind"]).strip())
        rendered = _render_kind(entry, repo_root, where, f)
        if rendered is None:
            declared_only.append(
                f"| {entry['kind']} | {_cell_text(entry.get('derivation'))} |"
            )
            continue
        sections.extend(rendered[0])
        counts.append(rendered[1])
    _check_entity_kinds(data, kinds, f)
    out = [
        *_inventory_frontmatter(repo, version, bundle),
        "",
        GENERATED_LINE,
        "",
        f"# {repo} — Knowledge Package ({position} of {len(bundle)}: the inventory)",
        "",
        # The version is stated once, in `target.version`, the line the stamp rewrite moves.
        "Every set the dossier declares, enumerated from the tree.",
        "",
        "## Counts",
        "",
        "| kind | members | exempt | yielded by |",
        "| --- | --- | --- | --- |",
        *counts,
        "",
        *sections,
    ]
    if declared_only:
        out += [
            "## Declared-only kinds",
            "",
            "| kind | reason |",
            "| --- | --- |",
            *declared_only,
            "",
        ]
    return "\n".join(out).rstrip("\n") + "\n"


def check_generated_inventory(
    data: dict[str, Any],
    repo_root: Path,
    dossier_path: Path,
    bundle: list[str],
    f: Findings,
) -> None:
    """Render the inventory in memory and fail when the committed copy differs by a byte."""
    name = inventory_member(bundle)
    if name is None:
        return
    rendered = render_inventory(data, repo_root, bundle, f)
    path = dossier_path.parent / name
    try:
        on_disk = path.read_text(encoding="utf-8")
    except OSError:
        on_disk = None
    if on_disk == rendered:
        return
    state = "is missing" if on_disk is None else "differs from a fresh render"
    f.error(
        f"{name}: {state} of the dossier's inventory against this tree. It is generated "
        f"and no person edits it; regenerate it with `{REGENERATE}` in the commit that "
        f"moved the tree."
    )


def apply_inventory(brief_path: Path, f: Findings) -> int:
    """Write the inventory member the bundle reached from ``brief_path`` names."""
    dossier = _bundle_dossier(brief_path)
    if dossier is None:
        f.error(
            f"{brief_path.name}: no `role: dossier` member in this bundle to render from"
        )
        return emit(f, "check_brief")
    path, data, bundle = dossier
    name = inventory_member(bundle)
    if name is None:
        f.error(
            f"{path.name}: `bundle` names no `*{INVENTORY_SUFFIX}` member, so there is "
            f"nothing to generate. Add `<REPO>{INVENTORY_SUFFIX}` to `bundle` in every "
            f"member to adopt the generated inventory."
        )
        return emit(f, "check_brief")
    rendered = render_inventory(data, find_repo_root(path.resolve().parent), bundle, f)
    if f.error_count:
        return emit(f, "check_brief")
    target = path.parent / name
    before = target.read_text(encoding="utf-8") if target.is_file() else None
    if before != rendered:
        target.write_text(rendered, encoding="utf-8")
    print(f"check_brief: {'unchanged' if before == rendered else 'wrote'} {target}")
    return emit(f, "check_brief")


def _bundle_dossier(brief_path: Path) -> tuple[Path, dict[str, Any], list[str]] | None:
    """The bundle's dossier: its path, its frontmatter, and the bundle it declares."""
    candidates = [brief_path]
    entry = _frontmatter.load(brief_path)
    if isinstance(entry.get("bundle"), list):
        candidates += [brief_path.parent / str(n) for n in entry["bundle"]]
    for path in candidates:
        if not path.is_file():
            continue
        data = _frontmatter.load(path)
        if not data or member_role(data) != "dossier":
            continue
        bundle = data.get("bundle")
        names = [str(n) for n in bundle] if isinstance(bundle, list) else []
        return path, data, names
    return None


# A bundle member's kind. `dossier` carries the enumerations and the inventory; `summary`
# carries synthesis and no enumerated list. Default `dossier`: a single-file brief is one.
ROLE_VALUES = {"dossier", "summary", "inventory"}


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

    ``repo_root`` is optional; it only sharpens the R-08 message.

    Two schemas, selected by ``role``. Identification (generator, target, bundle, the
    version stamp) is required of both members. Enumeration (``entities``,
    ``inventory``) is required of the dossier and forbidden on the summary.
    """
    try:
        data = _frontmatter.mapping(frontmatter_text)
    except _frontmatter.Invalid as exc:
        f.error(f"frontmatter: {exc}")
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
        validate_generator(data["generator"], f, role)
    if "target" not in data:
        f.error("frontmatter: target key is required")
    else:
        validate_target(data["target"], f, role)
    if "bundle" not in data:
        f.error("frontmatter: bundle key is required")
    if role == "inventory":
        extra = sorted(set(data) - {"role", "generator", "target", "bundle"})
        if extra:
            f.error(
                f"frontmatter: a `role: inventory` member carries role, generator, target "
                f"and bundle only; found {extra}. It is generated — regenerate it with "
                f"`{REGENERATE}`."
            )
    elif role == "summary":
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
                    f"'Three documents, split by writer')."
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
            _refuse_enumerated_surface(data, f)
    if "external_paths" in data:
        validate_external_paths(data["external_paths"], f)
    # `internal_contracts` and `configuration` are body-only (§2.5 and §2.6); a
    # frontmatter key by those names is accepted but not validated.
    return data


# The surface kinds a committed file enumerates. On a bundle with a generated inventory
# they are generated, so a hand-written list is refused. `library_exports`, `webhooks`
# and `signals` stay: no committed file lists them line by line.
GENERATED_SURFACE_KINDS = ("cli_verbs", "scripts", "http_routes", "mcp_tools")


def _refuse_enumerated_surface(data: dict[str, Any], f: Findings) -> None:
    """On a three-file bundle, refuse a surface list the inventory generates instead."""
    bundle = data.get("bundle")
    surface = data.get("external_surface")
    if not isinstance(bundle, list) or not isinstance(surface, dict):
        return
    if inventory_member([str(n) for n in bundle]) is None:
        return
    for key in GENERATED_SURFACE_KINDS:
        if key in surface:
            f.error(
                f"frontmatter: external_surface.{key} may not appear when the bundle "
                f"carries a generated inventory. Declare it as an `inventory` kind whose "
                f"source is the file that enumerates it, with `columns` for the values "
                f"beside each member, and run `{REGENERATE}`."
            )


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


STUB_RE = re.compile(r"^N/A\s+—\s+", re.MULTILINE)


def normalize_heading(text: str) -> str:
    """Lowercase + collapse whitespace for tolerant heading matching."""
    return re.sub(r"\s+", " ", text.strip().lower())


def check_required_headings(body: str, f: Findings) -> None:
    """Verify the markdown body carries every required narrative heading."""
    headings_by_text: dict[str, list[int]] = {}
    for heading in _markdown.parse(body, frontmatter=False).headings():
        headings_by_text.setdefault(normalize_heading(heading.title), []).append(
            heading.level
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
        if required_depth not in matches:
            actual_depths = sorted(set(matches))
            f.error(
                f"body: heading '{required_text}' present at depth(s) "
                f"{actual_depths} but spec requires H{required_depth}"
            )


LEAST, NOT_IN = "**Least confident**", "**Not in this brief**"
_BULLET_RE = re.compile(r"^[-*]\s+\S")


def confidence_bullets(body: str) -> dict[str, int] | None:
    """The top-level bullets under each Confidence marker, or None with no `## Confidence`.

    A marker the section does not carry is absent from the result. Bullets count from a
    marker to the next marker or the end of the section, which ends at the next `##`.
    """
    doc = _markdown.parse(body, frontmatter=False)
    section = next(
        (
            s
            for s in doc.sections(2)
            if s.heading is not None
            and normalize_heading(s.heading.title) == "confidence"
        ),
        None,
    )
    if section is None:
        return None
    counts: dict[str, int] = {}
    current: str | None = None
    for line in doc.within(section):
        if line.region != _markdown.TEXT:
            continue
        marker = next((m for m in (LEAST, NOT_IN) if m in line.text), None)
        if marker is not None:
            current = marker
            counts[marker] = 0
        elif current is not None and _BULLET_RE.match(line.text):
            counts[current] += 1
    return counts


def check_confidence(body: str, f: Findings) -> None:
    """Confidence section: ≥3 'Least confident' bullets + ≥1 'Not in this brief' bullet."""
    counts = confidence_bullets(body)
    if counts is None:
        # The required-headings pass already flagged this. Don't double-report.
        return
    if LEAST not in counts:
        f.error("body: ## Confidence section missing '**Least confident**' marker")
    if NOT_IN not in counts:
        f.error("body: ## Confidence section missing '**Not in this brief**' marker")
    if LEAST in counts and NOT_IN in counts:
        if counts[LEAST] < 3:
            f.error(
                f"body: ## Confidence requires ≥3 'Least confident' bullets; "
                f"found {counts[LEAST]}"
            )
        if counts[NOT_IN] < 1:
            f.error(
                f"body: ## Confidence requires ≥1 'Not in this brief' bullet; "
                f"found {counts[NOT_IN]}"
            )


# ---------------------------------------------------------------------------
# Bundle integrity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Repo-path resolution
# ---------------------------------------------------------------------------

# A backticked span is treated as a repo path only when it is unambiguously one.
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
    for lineno, raw, _ in _markdown.parse(body, frontmatter=False).text():
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
                continue  # glob or placeholder, e.g. docs/lexicon/**, src/<pkg>
            if "/" not in token and not token.endswith(_PATH_SUFFIXES):
                continue  # bare identifier, module path, or literal
            out.append((lineno, token))
    return out


def _under_hidden(token: str) -> bool:
    """Whether any segment of `token` starts with a dot, and so is outside the search.

    `repo_files` uses `glob`, which does not match a leading dot, so a path under a
    dot-directory reads as absent.
    """
    return any(part.startswith(".") for part in token.split("/"))


# One walk of the filesystem per repo root, per process; callers only read the set.
_SUFFIXES_CACHE: dict[str, set[str]] = {}


def _repo_path_suffixes(repo_root: Path) -> set[str]:
    """Every trailing path fragment of every file and directory in the repo.

    A brief names fragments (``host/api.py`` for ``src/racecar/host/api.py``), so matching
    is on suffixes. Uses the filesystem, not git: the set only decides whether to stay
    silent, so a wider set means fewer false warnings.
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

    A warning, never an error (R-06): a brief may name paths outside its repo. Those are
    declared in the frontmatter's ``external_paths``.
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
        # The message states what was searched: `repo_files` skips hidden directories.
        hidden = " (hidden paths are not searched)" if _under_hidden(token) else ""
        f.warning(
            f"body:{lineno}: names `{token}`, which nothing in the searched tree "
            f"matches{hidden} and which frontmatter `external_paths` does not declare — "
            f"stale reference, or an external path that should say so"
        )


# ---------------------------------------------------------------------------
# Dotted names in prose
# ---------------------------------------------------------------------------
#
# A backticked span with no `/` whose first segment is a package under `src/` is
# resolved here, to a module or to a top-level name in one. One that resolves to
# neither is a warning.

_DOTTED_RE = re.compile(
    r"^(?P<mod>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)(?:\.\*)?(?::(?P<attr>[A-Za-z_]\w*))?$"
)
_FILE_SUFFIX_WORDS = {s.lstrip(".") for s in _PATH_SUFFIXES}


def _top_level_names(path: Path) -> set[str]:
    """Every name a module binds at its top level, read with `ast`, never imported."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(t.id for t in targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


def _module_file(src: Path, parts: list[str]) -> Path | None:
    """The file a dotted module resolves to under ``src``, or None."""
    base = src.joinpath(*parts)
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return base if base.is_dir() else None


def resolves_dotted(src: Path, dotted: str, attr: str | None) -> bool:
    """Whether ``dotted`` (and ``attr``, when given) names a module or a name in one."""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        found = _module_file(src, parts[:cut])
        if found is None:
            continue
        rest = parts[cut:] + ([attr] if attr else [])
        if not rest:
            return True
        return found.is_file() and rest[0] in _top_level_names(found)
    return False


def check_dotted_names(body: str, repo_root: Path, f: Findings) -> None:
    """Warn on a backticked `pkg.mod.name` in the body that names nothing in the package."""
    src = package_root(repo_root)
    names = {p.name for p in packages(repo_root)}
    if not names:
        return
    seen: set[str] = set()
    for lineno, raw, _ in _markdown.parse(body, frontmatter=False).text():
        for span in _CODE_SPAN_RE.findall(raw):
            token = span.strip().rstrip(".,;)")
            m = _DOTTED_RE.match(token)
            if not m or token in seen:
                continue
            mod = m.group("mod")
            head, last = mod.split(".")[0], mod.split(".")[-1]
            if head not in names or last in _FILE_SUFFIX_WORDS:
                continue
            seen.add(token)
            if not resolves_dotted(src, mod, m.group("attr")):
                f.warning(
                    f"body:{lineno}: names `{token}`, which is neither a module of "
                    f"`{head}` nor a top-level name in one — a moved or renamed module"
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
# Section review records — which prose a change may have made false
# ---------------------------------------------------------------------------
#
# Each prose-carrying H2 and H3 of an authored member carries one comment line under its
# heading, a YAML flow mapping naming the version at its last review and git's object id,
# abbreviated, for every file or directory it describes:
#
#     ### The delivered checkers
#     <!-- {version: "0.164.0", sources: {"scripts/check_brief.py": "0a1b2c3d4e5f"}} -->
#
# `--stale` recomputes each id from the working tree and lists the sections whose ids
# moved, with the `git diff` to read; `--reviewed` records a review. A stale section is a
# warning.

_SECTION_COMMENT_RE = re.compile(r"^<!--\s*(\{.*\})\s*-->\s*$")
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
ABSENT_ID = (
    "0" * 12
)  # git's null id, abbreviated: the source was not in the tree at review
_UNREVIEWED_HEADINGS = {"confidence"}


@dataclass
class Section:
    """One H2 or H3 of an authored member: where it is, and what its comment records."""

    heading: str
    line: int  # the heading's line in the file, 1-based
    prose: bool
    comment_line: int | None = None
    version: str | None = None
    sources: dict[str, str] = field(default_factory=dict)


def _parse_section_comment(raw: str, where: str, f: Findings) -> dict[str, Any] | None:
    """The mapping a section comment carries, or None after reporting why it is unusable."""
    m = _SECTION_COMMENT_RE.match(raw.strip())
    if m is None:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        f.error(f"{where}: the section comment is not a YAML flow mapping ({exc})")
        return None
    if not isinstance(data, dict):
        f.error(
            f"{where}: the section comment must be a mapping of version and sources"
        )
        return None
    version = data.get("version")
    sources = data.get("sources")
    if not isinstance(version, str) or not RELEASE_RE.match(version):
        f.error(f"{where}: the section comment's `version` must be a quoted 'X.Y.Z'")
        return None
    if not isinstance(sources, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and _OBJECT_ID_RE.match(v)
        for k, v in sources.items()
    ):
        f.error(
            f"{where}: the section comment's `sources` must map each repo-relative path "
            f"to its 12-character object id, both quoted"
        )
        return None
    return data


def read_sections(text: str, where: str, f: Findings) -> list[Section]:
    """Every H2 and H3 of a member, with the review record each one carries.

    Prose-carrying means text before the next heading of any depth, other than the
    comment itself, a blank line, or an `N/A — ` stub. `## Confidence` records no review.
    """
    lines = text.splitlines()
    heads = _markdown.parse(text).headings()
    out: list[Section] = []
    for n, (no, depth, heading) in enumerate(heads):
        if depth not in (2, 3):
            continue
        index = no - 1
        end = heads[n + 1].no - 1 if n + 1 < len(heads) else len(lines)
        body = [ln for ln in lines[index + 1 : end] if ln.strip()]
        comment = (
            body[0] if body and _SECTION_COMMENT_RE.match(body[0].strip()) else None
        )
        rest = body[1:] if comment is not None else body
        prose = (
            bool(rest)
            and not STUB_RE.match(rest[0].strip())
            and normalize_heading(heading) not in _UNREVIEWED_HEADINGS
        )
        section = Section(heading, index + 1, prose)
        if comment is not None:
            section.comment_line = lines.index(comment, index + 1) + 1
            data = _parse_section_comment(comment, f"{where}:{section.comment_line}", f)
            if data is not None:
                section.version = data["version"]
                section.sources = dict(data["sources"])
        out.append(section)
    return out


def _oldest(versions: list[str]) -> str:
    """The lowest semver in ``versions``."""
    return min(versions, key=lambda v: tuple(int(p) for p in v.split(".")))


def check_sections(
    brief_path: Path, text: str, data: dict[str, Any], repo_root: Path, f: Findings
) -> None:
    """Hold an authored member's section review records to their construction rules.

    With none, the member gets one warning asking for them. With any, every
    prose-carrying H2 and H3 must carry one, every source must be committed, and
    ``target.version`` must be the oldest section's version.
    """
    name = brief_path.name
    sections = read_sections(text, name, f)
    recorded = [s for s in sections if s.comment_line is not None]
    if not recorded:
        f.warning(
            f"{name}: no section records which files it describes, so nothing can say "
            f"which prose a change made false and a refresh is a full re-read. Add a "
            f"review comment under each H2 and H3 and record it with `--reviewed` "
            f"(llm-summary/SPEC.md, 'Section review records')."
        )
        return
    for section in sections:
        if section.prose and section.comment_line is None:
            f.error(
                f"{name}:{section.line}: `{section.heading}` carries prose and no review "
                f"comment naming the files it describes"
            )
        for path in section.sources:
            if _git_tracks(repo_root, path) is False:
                f.error(
                    f"{name}:{section.comment_line}: `{section.heading}` names source "
                    f"`{path}`, which this repo does not commit"
                )
    versions = [s.version for s in recorded if s.version is not None]
    target = data.get("target")
    stamp = target.get("version") if isinstance(target, dict) else None
    if versions and stamp != _oldest(versions):
        f.error(
            f"{name}: target.version is {stamp} but its oldest section was reviewed at "
            f"{_oldest(versions)}. The stamp says when the file was last read as a whole, "
            f"so it is the oldest section's version; `--reviewed` recomputes it."
        )


def _records_reviews(text: str) -> bool:
    """Whether any section of this member carries a review comment."""
    return any(_SECTION_COMMENT_RE.match(ln.strip()) for ln in text.splitlines())


def _blob_id(path: Path) -> str:
    """git's blob id for one working-tree file; a symlink hashes its target text."""
    data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
    return sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _file_mode(path: Path) -> bytes:
    """The mode git records for a working-tree file."""
    if path.is_symlink():
        return b"120000"
    return b"100755" if path.stat().st_mode & 0o111 else b"100644"


def _tree_id(repo_root: Path, rel: str, tracked: frozenset[str]) -> str | None:
    """git's tree id for ``rel`` over the tracked files under it, from working-tree bytes.

    Equals ``git rev-parse HEAD:<rel>`` once the working tree is committed. None when
    nothing under ``rel`` is tracked.
    """
    head = rel.rstrip("/") + "/"
    children: dict[str, bool] = {}  # name -> is a directory
    for entry in tracked:
        if entry.startswith(head):
            part, _, deeper = entry[len(head) :].partition("/")
            children[part] = children.get(part, False) or bool(deeper)
    if not children:
        return None
    entries: list[tuple[bytes, bytes, bytes]] = []
    for part, is_dir in sorted(
        children.items(), key=lambda i: i[0] + ("/" if i[1] else "")
    ):
        child = f"{head}{part}"
        if is_dir:
            sub = _tree_id(repo_root, child, tracked)
            if sub is not None:
                entries.append((b"40000", part.encode(), bytes.fromhex(sub)))
            continue
        path = repo_root / child
        if not path.exists() and not path.is_symlink():
            continue  # deleted in the working tree: absent from the tree it would commit
        entries.append((_file_mode(path), part.encode(), bytes.fromhex(_blob_id(path))))
    body = b"".join(mode + b" " + name + b"\0" + oid for mode, name, oid in entries)
    return sha1(b"tree %d\0" % len(body) + body, usedforsecurity=False).hexdigest()


def object_id(repo_root: Path, rel: str, tracked: frozenset[str]) -> str:
    """The 12-character object id of a tracked file or directory now; the null id if absent."""
    rel = rel.rstrip("/")
    path = repo_root / rel
    if rel in tracked and (path.exists() or path.is_symlink()):
        return _blob_id(path)[:12]
    tree = _tree_id(repo_root, rel, tracked)
    return tree[:12] if tree else ABSENT_ID


def _in_store(repo_root: Path, oid: str) -> bool:
    """Whether git's object store still holds ``oid``, so `git diff` can read it."""
    if oid == ABSENT_ID:
        return False
    done = subprocess.run(
        ["git", "cat-file", "-e", oid],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def stale_sections(
    brief_path: Path, repo_root: Path, tracked: frozenset[str]
) -> list[tuple[Section, list[tuple[str, str, str]]]]:
    """Every recorded section whose sources moved: ``(section, [(path, old, new), ...])``."""
    text = brief_path.read_text(encoding="utf-8")
    out = []
    for section in read_sections(text, brief_path.name, Findings()):
        moved = [
            (path, old, new)
            for path, old in sorted(section.sources.items())
            if (new := object_id(repo_root, path, tracked)) != old
        ]
        if moved:
            out.append((section, moved))
    return out


def stale_lines(brief_path: Path, repo_root: Path) -> list[str]:
    """The `--stale` report for one member: a line per stale section, a diff per source."""
    tracked = _tracked_files(repo_root) or frozenset()
    lines: list[str] = []
    for section, moved in stale_sections(brief_path, repo_root, tracked):
        lines.append(
            f"stale: {brief_path.name}:{section.line} {section.heading} -- reviewed at "
            f"{section.version}; changed: {', '.join(path for path, _, _ in moved)}"
        )
        for path, old, new in moved:
            if old == ABSENT_ID:
                lines.append(f"  {path}: not in the tree at review; read it as it is")
            elif not _in_store(repo_root, old):
                lines.append(
                    f"  {path}: the reviewed object {old} is not in this repository's "
                    f"store; read it as it is"
                )
            else:
                lines.append(f"  git diff {old} {new}   # {path}")
    return lines


def _authored_members(brief_path: Path) -> list[Path]:
    """The bundle's members a person writes: every one but the generated inventory."""
    dossier = _bundle_dossier(brief_path)
    names = dossier[2] if dossier else [brief_path.name]
    return [
        brief_path.parent / n
        for n in names
        if not n.endswith(INVENTORY_SUFFIX) and (brief_path.parent / n).is_file()
    ]


def report_stale(brief_path: Path) -> int:
    """Print the stale sections of every authored member. Advisory: always exit 0."""
    repo_root = find_repo_root(brief_path.resolve().parent)
    found = [
        line
        for member in _authored_members(brief_path)
        for line in stale_lines(member, repo_root)
    ]
    for line in found:
        print(f"check_brief: {line}" if line.startswith("stale:") else line)
    if not found:
        print("check_brief: no section's sources changed since its review")
    return 0


def _set_target_version(text: str, version: str) -> str:
    """``text`` with its frontmatter ``target.version`` set to ``version``, anchored to
    the ``target:`` block so ``generator.version`` beside it never moves."""
    lines = text.splitlines(keepends=True)
    in_target = False
    for index, line in enumerate(lines):
        if re.match(r"^target:\s*$", line):
            in_target = True
            continue
        if in_target and line[:1] not in (" ", "\t"):
            break
        if in_target and re.match(r'^\s+version:\s*"[^"]*"\s*$', line):
            lines[index] = re.sub(r'"[^"]*"', f'"{version}"', line, count=1)
            break
    return "".join(lines)


def section_comment(version: str, sources: dict[str, str]) -> str:
    """The one line a section's review is recorded in."""
    pairs = ", ".join(f'"{path}": "{oid}"' for path, oid in sorted(sources.items()))
    return f'<!-- {{version: "{version}", sources: {{{pairs}}}}} -->'


def record_review(target: str, f: Findings) -> int:
    """`--reviewed <member>:<line>`: record one section as read at the version home now.

    Rewrites that section's comment line with the current version and object ids, then
    recomputes the member's ``target.version`` as the oldest section's. The comment must
    already exist.
    """
    member, _, raw_line = target.rpartition(":")
    path = Path(member)
    if not path.is_file() or not raw_line.isdigit():
        f.error(f"--reviewed takes <member>:<line>; got {target!r}")
        return emit(f, "check_brief")
    repo_root = find_repo_root(path.resolve().parent)
    text = path.read_text(encoding="utf-8")
    line = int(raw_line)
    section = next(
        (
            s
            for s in read_sections(text, path.name, f)
            if line in (s.line, s.comment_line)
        ),
        None,
    )
    if section is None or section.comment_line is None or section.version is None:
        f.error(
            f"{path.name}:{line}: no section with a review comment starts here. Write the "
            f"comment under the heading first, naming the files the section describes."
        )
        return emit(f, "check_brief")
    version, why = _version_home_value(repo_root)
    if version is None:
        f.error(f"--reviewed: no version to record -- {why}")
        return emit(f, "check_brief")
    tracked = _tracked_files(repo_root) or frozenset()
    sources = {p: object_id(repo_root, p, tracked) for p in section.sources}
    lines = text.splitlines(keepends=True)
    ending = "\n" if lines[section.comment_line - 1].endswith("\n") else ""
    lines[section.comment_line - 1] = section_comment(version, sources) + ending
    text = "".join(lines)
    versions = [s.version for s in read_sections(text, path.name, f) if s.version]
    path.write_text(_set_target_version(text, _oldest(versions)), encoding="utf-8")
    print(
        f"check_brief: reviewed {path.name}:{section.line} {section.heading} at {version}"
    )
    return emit(f, "check_brief")


# ---------------------------------------------------------------------------
# Handoff challenge
# ---------------------------------------------------------------------------
#
# Probes derived from the brief's own body, printed to the author and never written into
# the file. The author asks them at handoff and holds the answers. Deterministic and
# body-anchored. Reports only; never fails the run.


def _last_h3(body: str) -> str | None:
    """The text of the final H3 heading — a tail anchor a truncated read cannot reach."""
    h3s = _markdown.parse(body, frontmatter=False).headings(3)
    return h3s[-1].title if h3s else None


def _counts_rows(body: str) -> int:
    """The number of kind rows in a generated inventory's ``## Counts`` table."""
    doc = _markdown.parse(body, frontmatter=False)
    section = doc.section("Counts")
    if section is None:
        return 0
    rows = [ln for ln in doc.within(section) if ln.text.startswith("| ")]
    return max(len(rows) - 2, 0)  # the header and its separator


def _challenge_member(brief_path: Path) -> list[str]:
    """The probe lines for one bundle member, answers included.

    Four structural questions, three anchored past the middle of the file so a partial
    read fails at least one.
    """
    text = brief_path.read_text(encoding="utf-8")
    _, body = _frontmatter.split(text)
    counts = confidence_bullets(body) or {}
    doubts = counts.get(LEAST, 0) if NOT_IN in counts else 0
    headings = _markdown.parse(body, frontmatter=False).headings()
    token = sha256(text.encode("utf-8")).hexdigest()[:12]
    fourth = f"  4. How many bullets follow '**Least confident**'? -> {doubts}"
    if member_role(_frontmatter.parse(text)[0] or None) == "inventory":
        # The generated member has no Confidence section; count its Counts table rows.
        fourth = f"  4. How many kinds does '## Counts' list? -> {_counts_rows(body)}"
    return [
        f"challenge: {brief_path.name}  token {token}",
        f"  1. How many H2 sections does {brief_path.name} carry? -> "
        f"{sum(1 for h in headings if h.level == 2)}",
        f"  2. How many H3 subsections in total? -> "
        f"{sum(1 for h in headings if h.level == 3)}",
        f"  3. What is its LAST H3 heading? -> {_last_h3(body)}",
        fourth,
    ]


def challenge(brief_path: Path) -> int:
    """Print handoff probes for every bundle member, for the author to hold.

    Each member's token identifies the revision the recipient holds; it is not evidence
    of reading.
    """
    # Stale sections first.
    repo_root = find_repo_root(brief_path.resolve().parent)
    for member in _authored_members(brief_path):
        for line in stale_lines(member, repo_root):
            print(f"challenge: {line}" if line.startswith("stale:") else line)
    print(
        "challenge: ask these before anything else; the answers are yours, not theirs"
    )
    for line in _challenge_member(brief_path):
        print(line)
    members = _frontmatter.load(brief_path).get("bundle")
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
    parser.add_argument(
        "--stale",
        action="store_true",
        help=(
            "List the authored sections whose declared sources changed since their "
            "review, with the git diff to read. Advisory; exits 0."
        ),
    )
    parser.add_argument(
        "--reviewed",
        metavar="MEMBER:LINE",
        help=(
            "Record the section at MEMBER:LINE as reviewed at the version home now, and "
            "recompute that member's target.version."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write the bundle's generated inventory member from the dossier's "
            "`inventory` declarations and the tree. The default run compares it."
        ),
    )
    return parser.parse_args(argv)


def validate_member(brief_path: Path, f: Findings, is_entry: bool) -> list[str]:
    """Validate one bundle member; return the ``bundle`` list it declares.

    ``is_entry`` marks the file the caller named or discovery found. Only it checks
    bundle membership against the directory.
    """
    text = brief_path.read_text(encoding="utf-8")
    frontmatter_text, body = _frontmatter.split(text)

    if frontmatter_text is None:
        f.error(
            f"frontmatter: no YAML frontmatter block found at top of {brief_path} "
            f"(expected '---' fences before the H1)"
        )
        # Still attempt body checks against the entire text.
        check_required_headings(text, f)
        check_confidence(text, f)
        return []

    repo_root = find_repo_root(brief_path.resolve().parent)
    data = validate_frontmatter(frontmatter_text, f, repo_root)
    role = member_role(data)
    if role == "inventory":
        # Generated: graded by the byte comparison the dossier runs.
        if data is not None:
            check_version_stamp(data, repo_root, f)
        return validate_bundle(data["bundle"], f) if data and "bundle" in data else []
    if role == "dossier":
        # The §N.M spine is the dossier's shape; a summary is not held to it.
        check_required_headings(body, f)
    check_confidence(body, f)
    external_paths = (
        validate_external_paths(data["external_paths"], f)
        if data is not None and "external_paths" in data
        else []
    )
    check_repo_paths(body, repo_root, external_paths, f)
    check_dotted_names(body, repo_root, f)
    if data is not None:
        check_sections(brief_path, text, data, repo_root, f)
        for section, moved in stale_sections(
            brief_path, repo_root, _tracked_files(repo_root) or frozenset()
        ):
            f.warning(
                f"{brief_path.name}:{section.line}: `{section.heading}` was reviewed at "
                f"{section.version} and {', '.join(p for p, _, _ in moved)} changed since; "
                f"`--stale` prints the diffs, `--reviewed` records a re-read"
            )
    bundle: list[str] = []
    if data is not None and isinstance(data.get("bundle"), list):
        bundle = validate_bundle(data["bundle"], f)
        if is_entry:
            check_bundle_membership(brief_path, bundle, f)
    if data is not None:
        check_frontmatter_paths(data, repo_root, brief_path, external_paths, f)
        if role == "dossier" and inventory_member(bundle) is not None:
            check_generated_inventory(data, repo_root, brief_path, bundle, f)
        elif role == "dossier":
            check_inventory(data, repo_root, brief_path, text.splitlines(), f)
            if not _records_reviews(text):
                check_version_stamp(data, repo_root, f)
            f.warning(
                f"{brief_path.name}: this bundle has no generated inventory, so only the "
                f"member ids are graded and every value beside them is read by nothing. "
                f"Add `<REPO>{INVENTORY_SUFFIX}` to `bundle`, move the member lists into "
                f"`inventory` kinds with `columns`, and run `{REGENERATE}` "
                f"(llm-summary/SPEC.md, 'Three documents, split by writer')."
            )
        elif inventory_member(bundle) is None and not _records_reviews(text):
            # A summary of a two-file bundle: its stamp rides with the version home.
            # Not compared beside a generated inventory, nor when its sections record
            # reviews (its stamp is then the oldest of them).
            check_version_stamp(data, repo_root, f)
    return bundle


def main(argv: list[str] | None = None) -> int:
    """Validate every member of the bundle reached from ``docs/summary/<REPO>.md``.

    The unit is the bundle named by ``bundle:``, not the file.
    """
    # One read-only scope for the whole run.
    with git_scope():
        args = parse_args(argv if argv is not None else sys.argv[1:])
        f = Findings()
        if args.reviewed:
            return record_review(args.reviewed, f)

        if args.path:
            brief_path = Path(args.path)
            if not brief_path.is_file():
                f.error(f"discovery: brief not found at {brief_path}")
                return emit(f, "check_brief")
        else:
            discovered = discover_brief_path()
            if discovered is None:
                # Every candidate, not just the first.
                expected = " or ".join(
                    str(c) for c in brief_candidates(find_repo_root())
                )
                f.error(f"discovery: no brief found at conventional path {expected}")
                return emit(f, "check_brief")
            brief_path = discovered

        # Each mode does one thing; modes are never combined.
        modes: dict[str, Callable[[Path, Findings], int]] = {
            "challenge": lambda path, _: challenge(path),
            "apply": apply_inventory,
            "stale": lambda path, _: report_stale(path),
        }
        chosen = next(
            (mode for name, mode in modes.items() if getattr(args, name)), None
        )
        return (chosen or validate_bundle_from)(brief_path, f)


def validate_bundle_from(brief_path: Path, f: Findings) -> int:
    """The default run: validate the member at ``brief_path`` and every sibling it names."""
    bundle = validate_member(brief_path, f, is_entry=True)
    for name in bundle:
        sibling = brief_path.parent / name
        if sibling.resolve() == brief_path.resolve():
            continue
        if not sibling.is_file():
            continue  # already reported by check_bundle_membership
        validate_member(sibling, f, is_entry=False)
    return emit(f, "check_brief")


def _version_home_value(root: Path) -> tuple[str | None, str | None]:
    """Return the repo's current version via `lib/shared/_commits.py`'s `version_home`.

    Returns `(version, None)` when it resolves, and `(None, reason)` when it cannot; the
    stamp check is then skipped and the skip reported.
    """
    home = _commits.version_home(root)
    if home is None:
        return (
            None,
            "no version home in this repo (no [project].version, no VERSION file)",
        )
    return home[1], None


def check_version_stamp(data: dict[str, Any], root: Path, f: Findings) -> None:
    """Fail when the brief's ``target.version`` has fallen behind the version home.

    Compares ``target.version`` (the described repo's version), never
    ``generator.version`` (racecar's). Called only on the stamps a bump writes: a
    generated inventory's and a two-file bundle's. A lag is an error. An authored member
    beside a generated inventory is never passed here.
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


if __name__ == "__main__":
    sys.exit(main())
