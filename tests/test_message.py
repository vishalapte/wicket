"""Tests for the provider-neutral key and the observation/settlement join."""

from __future__ import annotations

import pytest

from wicket.message import message_key, normalize_message_id


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<CAa+1X@mail.gmail.com>", "caa+1x@mail.gmail.com"),
        ("  <abc@x.com>  ", "abc@x.com"),
        ("Message body (comment) <Def@Y.COM>", "def@y.com"),
        ("", None),
        (None, None),
        ("no-brackets-token", "no-brackets-token"),
    ],
)
def test_normalize_message_id(raw: str | None, expected: str | None) -> None:
    assert normalize_message_id(raw) == expected


def test_message_key_prefers_message_id() -> None:
    assert (
        message_key(message_id="<ABC@x.com>", provider="gmail", native_id="18f2")
        == "abc@x.com"
    )


def test_message_key_falls_back_to_provider_tagged_native_id() -> None:
    assert (
        message_key(message_id=None, provider="gmail", native_id="18f2a3")
        == "gmail:18f2a3"
    )
    # Malformed/empty header is treated the same as missing.
    assert (
        message_key(message_id="   ", provider="fastmail", native_id="42")
        == "fastmail:42"
    )
