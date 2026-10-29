"""The one error this package raises: a request it refuses, said in a sentence.

`racecar.surface` turns a `SurfaceError` into its own `ApiError` at the boundary;
`scripts/surface.py` prints it and exits 2.
"""

from __future__ import annotations


class SurfaceError(Exception):
    """A refusal: the arguments name something this package will not do, and why."""
