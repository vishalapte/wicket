"""The exclude noun: each verb returns the type it declares, and publishes its schema.

What every node shares (bare invocation, argument errors) is tested once, over all nodes,
in ``test_cli.py``. This file holds what only exclude knows.
"""

from __future__ import annotations

from typing import Any, get_type_hints

import pytest

import wicket.schema
from wicket.exclude import __main__ as cli
from wicket.exclude import api

# One real call per verb, for the test that checks each result against its type.
SAMPLES: dict[str, dict[str, Any]] = {
    "create": {"list_": "made-of-meat.googlegroups.com"},
    "delete": {"list_": "made-of-meat.googlegroups.com"},
    "list": {},
}


@pytest.mark.parametrize("verb", sorted(api.VERBS))
def test_exclude_verbs_return_the_type_they_declare(verb: str) -> None:
    fn = api.VERBS[verb]
    result = fn(**SAMPLES[verb])
    assert wicket.schema.conforms(result, get_type_hints(fn)["return"]) == []


def test_exclude_output_publishes_one_schema_per_verb() -> None:
    phases = sorted(str(params["phase"]) for params, _ in cli.output())
    assert phases == sorted(api.VERBS)
