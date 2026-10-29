"""The alias worker: the computation, with no knowledge of who calls it.

No argument parsing, no printing, no default chosen on a caller's behalf. ``api`` imports
from here and reaches no deeper; the renderers under ``lib/renderer/`` import only
``lib.results``.
"""

from __future__ import annotations

from wicket.maps import Map

from .results import AliasRow


def rows(mapping: Map) -> list[AliasRow]:
    """Every entry of the domain-aliases map, sorted by primary."""
    return [{"primary": k, "domains": sorted(mapping[k])} for k in sorted(mapping)]
