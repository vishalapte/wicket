"""Sender-domain canonicalization shared across the wicket tools.

The alias rule has one home here. A domain maps to its *primary* through an
owner-authored alias file (``domain-aliases.json``); a domain that isn't
listed maps to itself. Folding ``us.icapenergy.com`` into ``icapenergy.com``
or ``airindia.in`` into ``airindia.com`` is an explicit owner decision recorded
in the alias file, never inferred. An alias may be a literal domain or the
wildcard ``*.parent.com``, which folds every subdomain of ``parent.com`` into
the primary (an owner-authorized subdomain strip, scoped to that parent).

Consumer: ``wicket.fetch`` (domain classification + search expansion). The
module depends on
nothing above ``config``, so it sits in the shared layer below the tools.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# A bare domain: dot-joined [A-Za-z0-9-] labels ending in a 2+ alpha TLD.
# Validates alias-file entries and --domains query inputs.
DOMAIN_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
# A subdomain wildcard alias: "*.parent.com" folds every subdomain into primary.
WILDCARD_RE = re.compile(r"^\*\.[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def canonical_domain(domain: str, aliases: dict[str, str]) -> str:
    """Map a domain to its primary: exact alias first, then ``*.parent`` wildcard.

    A domain with no exact or wildcard match returns unchanged.
    """
    if domain in aliases:
        return aliases[domain]
    for pattern, primary in aliases.items():
        if pattern.startswith("*.") and domain.endswith(pattern[1:]):
            return primary
    return domain


def expand_domain(domain: str, aliases: dict[str, str]) -> list[str]:
    """Return [primary, *concrete secondaries] for `domain`'s alias group.

    Wildcard (``*.``) members are skipped: they are not literal domains and so
    cannot be turned into search terms. If `domain` isn't aliased, returns
    ``[domain]``.
    """
    primary = canonical_domain(domain, aliases)
    group = {primary}
    for alias, target in aliases.items():
        if target == primary and not alias.startswith("*."):
            group.add(alias)
    return sorted(group)


def load_domain_aliases(path: Path | None) -> dict[str, str]:
    """Load alias→primary lookup from a JSON file, or return ``{}``.

    File shape: ``{"primary.com": ["alias1.com", "alias2.com"], ...}``.
    Returns the flattened mapping where every entry — including each
    primary — maps to its canonical form. Validates that no domain is
    both a primary and an alias, and no two primaries claim the same
    alias. Missing file is silent (returns ``{}``); malformed file raises.
    """
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level value must be an object")
    mapping: dict[str, str] = {}
    for primary, aliases in raw.items():
        if not isinstance(aliases, list):
            raise ValueError(f"{path}: value for {primary!r} must be a list")
        if not DOMAIN_RE.match(primary):
            raise ValueError(f"{path}: invalid primary domain {primary!r}")
        if primary in mapping and mapping[primary] != primary:
            raise ValueError(
                f"{path}: {primary!r} is both a primary and an alias of "
                f"{mapping[primary]!r}"
            )
        mapping[primary] = primary
        for alias in aliases:
            if not (DOMAIN_RE.match(alias) or WILDCARD_RE.match(alias)):
                raise ValueError(f"{path}: invalid alias domain {alias!r}")
            existing = mapping.get(alias)
            if existing is not None and existing != primary:
                raise ValueError(
                    f"{path}: {alias!r} aliased to both {existing!r} and "
                    f"{primary!r}"
                )
            mapping[alias] = primary
    return mapping
