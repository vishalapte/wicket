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
import os
import re
from pathlib import Path

from wicket.env import (
    ALIASES_FILENAME,
    DOMAIN_ALIASES_FILE_ENV_VAR,
    DOMAIN_ROUTES_FILE_ENV_VAR,
    DOMAIN_ROUTES_FILENAME,
    flatten_aliases,
    is_destination,
)

# A bare domain: dot-joined [A-Za-z0-9-] labels ending in a 2+ alpha TLD.
# Validates alias-file entries and --domains query inputs.
DOMAIN_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
# A subdomain wildcard alias: "*.parent.com" folds every subdomain into primary.
WILDCARD_RE = re.compile(r"^\*\.[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def domain_routes_path(mail_root: Path) -> Path:
    """The one home for the counterparty-routing file.

    ``$WICKET_DOMAIN_ROUTES_FILE``, when set, names the file directly (any name,
    any location) and wins over ``mail_root``.
    """
    override = os.environ.get(DOMAIN_ROUTES_FILE_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return mail_root / DOMAIN_ROUTES_FILENAME


def domain_aliases_path(mail_root: Path) -> Path:
    """The one home for the subdomain-folding file (``<mail-root>/domain-aliases.json``).

    At the mail root, not under a per-account state dir: a *bucket* (``travel``)
    has no mailbox and therefore no credentials directory, yet it is exactly where
    folding matters most, since airline mail arrives from a different marketing
    subdomain every time. ``$WICKET_DOMAIN_ALIASES_FILE``, when set, names the
    file directly and wins over ``mail_root``.
    """
    override = os.environ.get(DOMAIN_ALIASES_FILE_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return mail_root / ALIASES_FILENAME


def load_domain_routes(path: Path | None) -> dict[str, str]:
    """Load ``counterparty domain -> destination``, or ``{}`` when absent.

    A *different* rule from the account-alias map, and the distinction is load
    bearing. The alias map lists addresses that are **you**; this lists domains
    that are **not** you, and says which store their mail belongs in:

        {"shopping": ["ikea.com", "llbean.com", "*.thuma.co"]}

    Putting a counterparty in the alias map instead would tell the filing rule
    that IKEA *is* you, which makes every message from IKEA look outbound and
    files it under the recipient's domain rather than ``ikea.com``. Same file
    shape, opposite meaning, so they get separate homes.

    Keys are bare domains or ``*.parent.com`` wildcards; a destination is an
    account address or a bucket name.
    """
    if path is None or not path.exists():
        return {}
    return flatten_aliases(
        json.loads(path.read_text(encoding="utf-8")),
        path,
        primary_ok=is_destination,
        alias_ok=lambda d: isinstance(d, str)
        and bool(DOMAIN_RE.match(d) or WILDCARD_RE.match(d)),
        noun="domain",
    )


def route_of(
    address: str, routes: dict[str, str], aliases: dict[str, str] | None = None
) -> str | None:
    """The destination an address's *domain* routes to, or None.

    The domain is folded to its primary first (``t.delta.com`` -> ``delta.com``),
    so the routes file lists only primaries and never has to repeat every
    marketing subdomain that the fold map already knows about. Exact match, then
    ``*.parent`` wildcard. Unlike `canonical_domain`, an unmatched domain returns
    None rather than itself: nothing claims it.
    """
    _, _, domain = address.strip().lower().partition("@")
    domain = canonical_domain(domain or address.strip().lower(), aliases or {})
    if domain in routes:
        return routes[domain]
    for pattern, destination in routes.items():
        if pattern.startswith("*.") and domain.endswith(pattern[1:]):
            return destination
    return None


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
    return flatten_aliases(
        json.loads(path.read_text(encoding="utf-8")),
        path,
        primary_ok=lambda d: isinstance(d, str) and bool(DOMAIN_RE.match(d)),
        alias_ok=lambda d: isinstance(d, str)
        and bool(DOMAIN_RE.match(d) or WILDCARD_RE.match(d)),
        noun="domain",
    )
