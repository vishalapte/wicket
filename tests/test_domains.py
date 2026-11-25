"""Tests for domain canonicalization, including wildcard subdomain aliases."""

from __future__ import annotations

import json
from pathlib import Path

from wicket.domains import (
    canonical_domain,
    expand_domain,
    load_domain_aliases,
    route_of,
)


def test_exact_alias_takes_precedence() -> None:
    aliases = {"emirates.com": "emirates.com", "emirates.email": "emirates.com"}
    assert canonical_domain("emirates.email", aliases) == "emirates.com"
    assert canonical_domain("emirates.com", aliases) == "emirates.com"


def test_wildcard_folds_subdomains_but_not_the_parent() -> None:
    aliases = {"delta.com": "delta.com", "*.delta.com": "delta.com"}
    assert canonical_domain("o.delta.com", aliases) == "delta.com"
    assert canonical_domain("email.x.delta.com", aliases) == "delta.com"
    assert canonical_domain("delta.com", aliases) == "delta.com"  # parent untouched
    assert canonical_domain("notdelta.com", aliases) == "notdelta.com"


def test_the_most_specific_of_two_matching_wildcards_wins() -> None:
    """File order does not decide: the less specific wildcard is listed first here."""
    aliases = {"*.delta.com": "delta.com", "*.t.delta.com": "t-delta.com"}
    assert canonical_domain("x.t.delta.com", aliases) == "t-delta.com"
    assert canonical_domain("t.delta.com", aliases) == "delta.com"
    assert canonical_domain("x.o.delta.com", aliases) == "delta.com"


def test_a_domain_with_no_parent_is_unchanged() -> None:
    aliases = {"*.com": "dotcom", "*.delta.com": "delta.com"}
    assert canonical_domain("localhost", aliases) == "localhost"
    assert canonical_domain("", aliases) == ""
    assert canonical_domain("com", aliases) == "com"
    assert canonical_domain("delta.com", aliases) == "dotcom"


def test_route_of_folds_then_matches_exact_or_wildcard() -> None:
    aliases = {"*.delta.com": "delta.com"}
    routes = {"delta.com": "travel", "*.amazon.com": "shopping"}
    assert route_of("a@o.delta.com", routes, aliases) == "travel"
    assert route_of("a@smile.amazon.com", routes, aliases) == "shopping"
    assert route_of("a@amazon.com", routes, aliases) is None
    assert route_of("a@notdelta.com", routes, aliases) is None
    routes = {"*.delta.com": "travel", "*.t.delta.com": "deals"}
    assert route_of("a@x.t.delta.com", routes) == "deals"


def test_expand_domain_skips_wildcards() -> None:
    aliases = {"delta.com": "delta.com", "*.delta.com": "delta.com"}
    assert expand_domain("delta.com", aliases) == ["delta.com"]  # no "*." term


def test_load_accepts_wildcard_alias(tmp_path: Path) -> None:
    f = tmp_path / "aliases.json"
    f.write_text(json.dumps({"delta.com": ["*.delta.com"]}), encoding="utf-8")
    loaded = load_domain_aliases(f)
    assert loaded["*.delta.com"] == "delta.com"
    assert canonical_domain("g.delta.com", loaded) == "delta.com"
