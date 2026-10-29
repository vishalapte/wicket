"""The __NOUN__ noun: each verb returns the type it declares, and publishes its schema.

What every node shares (bare invocation, argument errors) is tested once, over all nodes,
in ``test_cli.py``. This file holds what only __NOUN__ knows.
"""

from __future__ import annotations

from typing import Any, get_type_hints

import pytest

import __PKG__.schema
from __CLI__ import __main__ as cli
from __MODULE__ import api

# One real call per verb, for the test that checks each result against its type.
SAMPLES: dict[str, dict[str, Any]] = {}


@pytest.mark.parametrize("verb", sorted(api.VERBS))
def test___NOUN_ID___verbs_return_the_type_they_declare(verb: str) -> None:
    fn = api.VERBS[verb]
    result = fn(**SAMPLES[verb])
    assert __PKG__.schema.conforms(result, get_type_hints(fn)["return"]) == []


def test___NOUN_ID___output_publishes_one_schema_per_verb() -> None:
    phases = sorted(str(params["phase"]) for params, _ in cli.output())
    assert phases == sorted(api.VERBS)
