#!/usr/bin/env python3
"""Mechanical validator for a racecar-llm-summary brief bundle.

Validates a brief produced by the ``racecar-llm-summary`` generator against
the schema and structural budget declared in
``llm-summary/README.md`` (sections ``## Frontmatter (YAML)`` and
``## Structural budget``). That spec is the contract; this script only
mechanizes it.

Checks performed:

  1. Frontmatter YAML parses and matches the declared schema:
     - ``generator.name``, ``generator.version`` (semver "X.Y.Z").
     - ``target.repo``, ``target.date`` (ISO YYYY-MM-DD).
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
       ``signals``; enum members for HTTP ``method``.

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
    python3 <path-to>/check_brief.py [<bundle-path>]
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date as date_cls
from pathlib import Path

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


def find_repo_root(start: Path | None = None) -> Path:
    """Nearest ancestor of ``start`` (default CWD) that contains ``.git``."""
    start = start or Path.cwd()
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return start


def derive_repo_slug(repo_root: Path) -> tuple[str, str]:
    """Return ``($repo, $REPO)`` per the spec's slug rules."""
    base = repo_root.name.lower()
    slug = re.sub(r"[^a-z0-9_-]", "-", base)
    return slug, slug.upper()


def discover_brief_path() -> Path | None:
    """Return the conventional brief path ``docs/summary/$REPO.md`` or None."""
    root = find_repo_root()
    _, slug_upper = derive_repo_slug(root)
    candidate = root / "docs" / "summary" / f"{slug_upper}.md"
    return candidate if candidate.is_file() else None


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
SURFACE_KINDS = {
    "http_routes",
    "cli_verbs",
    "mcp_tools",
    "library_exports",
    "webhooks",
    "signals",
}


def require_mapping(value: object, where: str, f: Findings) -> bool:
    """Assert `value` is a dict; record an error at `where` and return False if not."""
    if not isinstance(value, dict):
        f.error(f"frontmatter: {where} must be a mapping; got {type(value).__name__}")
        return False
    return True


def require_list(value: object, where: str, f: Findings) -> bool:
    """Assert `value` is a list; record an error at `where` and return False if not."""
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


def validate_entity(entity: object, idx: int, f: Findings) -> None:
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
    if case == "content_tree":
        if "mutability" in entity and entity.get("mutability") not in MUTABILITY_VALUES:
            f.error(
                f"frontmatter: {where}.mutability must be one of "
                f"{sorted(MUTABILITY_VALUES)} when present; "
                f"got {entity.get('mutability')!r}"
            )


def validate_entities(entities: object, f: Findings) -> None:
    """Validate the `entities` list and each entry within it."""
    if not require_list(entities, "entities", f):
        return
    assert isinstance(entities, list)
    for i, entity in enumerate(entities):
        validate_entity(entity, i, f)


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


def validate_external_surface(surface: object, f: Findings) -> None:
    """Validate the `external_surface` block grouping endpoints by surface kind."""
    if not require_mapping(surface, "external_surface", f):
        return
    assert isinstance(surface, dict)
    for key, entries in surface.items():
        if key not in SURFACE_KINDS:
            # §2.4 rule: any kind with >5 entries must be a recognized
            # first-class key. Free-form sub-keys are allowed only at small size.
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
            continue
        if not require_list(entries, f"external_surface.{key}", f):
            continue
        assert isinstance(entries, list)
        if key == "http_routes":
            for i, entry in enumerate(entries):
                where = f"external_surface.http_routes[{i}]"
                if not require_mapping(entry, where, f):
                    continue
                assert isinstance(entry, dict)
                if entry.get("method") not in HTTP_METHODS:
                    f.error(
                        f"frontmatter: {where}.method must be one of {sorted(HTTP_METHODS)}; "
                        f"got {entry.get('method')!r}"
                    )
                for required_key in ("path", "view"):
                    if not isinstance(entry.get(required_key), str) or not entry.get(
                        required_key
                    ):
                        f.error(
                            f"frontmatter: {where}.{required_key} must be a non-empty string"
                        )
        elif key == "cli_verbs":
            for i, entry in enumerate(entries):
                where = f"external_surface.cli_verbs[{i}]"
                if not require_mapping(entry, where, f):
                    continue
                assert isinstance(entry, dict)
                for required_key in ("verb", "module", "behavior"):
                    if not isinstance(entry.get(required_key), str) or not entry.get(
                        required_key
                    ):
                        f.error(
                            f"frontmatter: {where}.{required_key} must be a non-empty string"
                        )
        elif key == "library_exports":
            for i, entry in enumerate(entries):
                where = f"external_surface.library_exports[{i}]"
                if not require_mapping(entry, where, f):
                    continue
                assert isinstance(entry, dict)
                for required_key in ("name", "module", "signature", "behavior"):
                    if not isinstance(entry.get(required_key), str) or not entry.get(
                        required_key
                    ):
                        f.error(
                            f"frontmatter: {where}.{required_key} must be a non-empty string"
                        )
        elif key == "signals":
            for i, entry in enumerate(entries):
                where = f"external_surface.signals[{i}]"
                if not require_mapping(entry, where, f):
                    continue
                assert isinstance(entry, dict)
                for required_key in ("name", "sender", "handler", "behavior"):
                    if not isinstance(entry.get(required_key), str) or not entry.get(
                        required_key
                    ):
                        f.error(
                            f"frontmatter: {where}.{required_key} must be a non-empty string"
                        )
        elif key == "webhooks":
            for i, entry in enumerate(entries):
                where = f"external_surface.webhooks[{i}]"
                if not require_mapping(entry, where, f):
                    continue
                assert isinstance(entry, dict)
                for required_key in ("source", "path", "behavior"):
                    if not isinstance(entry.get(required_key), str) or not entry.get(
                        required_key
                    ):
                        f.error(
                            f"frontmatter: {where}.{required_key} must be a non-empty string"
                        )


def validate_frontmatter(frontmatter_text: str, f: Findings) -> dict | None:
    """Parse and validate the YAML frontmatter; return it as a dict, or None."""
    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        f.error(f"frontmatter: YAML parse error — {exc}")
        return None
    if not isinstance(data, dict):
        f.error("frontmatter: top-level must be a mapping")
        return None
    # Required keys.
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
    if "entities" not in data:
        f.error("frontmatter: entities key is required (use [] only if truly none)")
    else:
        validate_entities(data["entities"], f)
    # Optional sections.
    if "relationships" in data:
        validate_relationships(data["relationships"], f)
    if "external_surface" in data:
        validate_external_surface(data["external_surface"], f)
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Validate the brief bundle at docs/summary/<REPO>.md; return an exit code."""
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
            root = find_repo_root()
            _, slug_upper = derive_repo_slug(root)
            expected = root / "docs" / "summary" / f"{slug_upper}.md"
            f.error(f"discovery: no brief found at conventional path {expected}")
            return emit(f)
        brief_path = discovered

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
        return emit(f)

    data = validate_frontmatter(frontmatter_text, f)
    check_required_headings(body, f)
    check_confidence(body, f)
    if data is not None and isinstance(data.get("bundle"), list):
        bundle = validate_bundle(data["bundle"], f)
        check_bundle_membership(brief_path, bundle, f)

    return emit(f)


def emit(f: Findings) -> int:
    """Print all findings and return 1 if any error was recorded, else 0."""
    for severity, msg in f.entries:
        print(f"check_brief: {severity}: {msg}")
    if f.error_count == 0 and f.warning_count == 0:
        print("check_brief: OK")
        return 0
    print(f"check_brief: {f.error_count} errors, {f.warning_count} warnings")
    return 1 if f.error_count else 0


if __name__ == "__main__":
    sys.exit(main())
