"""The __NOUN__ api: the one place a request becomes a decision.

Every surface (this noun's cli, an MCP tool, a REST route) calls these functions and
nothing below them. Each verb is one function returning one result type from
``lib.results``, plain JSON. A request the api cannot run raises ``ApiError``. A verb
``racecar.surface`` adds arrives as a stub: its signature is the params the lexicon
declares for it, and what it computes, and the fields its result carries, are the
author's.
"""

from __future__ import annotations

from collections.abc import Callable

# CLI spelling -> the function it binds. A verb spelled like a keyword or a builtin
# (`import`, `list`) is a key here rather than a function name; the lexicon reads this
# literal to find each verb.
VERBS: dict[str, Callable[..., object]] = {}
