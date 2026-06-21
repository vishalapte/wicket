"""Tests for domain canonicalization, including wildcard subdomain aliases."""

from __future__ import annotations

import json
from pathlib import Path

from wicket.domains import canonical_domain, expand_domain, load_domain_aliases


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


def test_expand_domain_skips_wildcards() -> None:
    aliases = {"delta.com": "delta.com", "*.delta.com": "delta.com"}
    assert expand_domain("delta.com", aliases) == ["delta.com"]  # no "*." term


def test_load_accepts_wildcard_alias(tmp_path: Path) -> None:
    f = tmp_path / "aliases.json"
    f.write_text(json.dumps({"delta.com": ["*.delta.com"]}), encoding="utf-8")
    loaded = load_domain_aliases(f)
    assert loaded["*.delta.com"] == "delta.com"
    assert canonical_domain("g.delta.com", loaded) == "delta.com"
