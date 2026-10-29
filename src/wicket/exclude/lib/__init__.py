"""The exclude worker: the computation, with no knowledge of who calls it.

No argument parsing, no printing, no default chosen on a caller's behalf. ``api`` imports
from here and reaches no deeper; the renderers under ``lib/renderer/`` import only
``lib.results``.
"""

from __future__ import annotations

from wicket.maps import Map

from .results import ExcludeRow


def rows(mapping: Map) -> list[ExcludeRow]:
    """Every entry of the excluded-lists map: ``*`` first (it sorts first), then by account."""
    return [{"account": k, "lists": sorted(mapping[k])} for k in sorted(mapping)]
