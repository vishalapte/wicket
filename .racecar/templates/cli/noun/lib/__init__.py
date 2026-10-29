"""The __NOUN__ worker: the computation, with no knowledge of who calls it.

No argument parsing, no printing, no default chosen on a caller's behalf. ``api`` imports
from here and reaches no deeper; the renderers under ``lib/renderer/`` import only
``lib.results``.
"""
