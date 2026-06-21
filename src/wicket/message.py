"""Provider-neutral message identity and the observation/settlement join.

One home (ADR 0002) for the three things the unified manifest needs:

  * the **portable message key** — the normalized RFC ``Message-ID``, with a
    provider-tagged fallback — that replaces Gmail's ``X-GM-MSGID`` as the
    cross-store, cross-provider key;
  * the **field-ownership** split between *observation*, *settlement*, and
    *derived* fields.

Pure stdlib, no wicket imports: this sits in the shared layer below the verbs
and is imported by ``catalog.lib`` and ``fetch.lib``.
"""

from __future__ import annotations

import email.utils

# Field ownership (ADR 0002 §2). Module-level tuples, not enums.
IDENTITY_FIELDS = ("id", "msgid", "thread_id", "account")
OBSERVATION_FIELDS = ("date", "from", "to", "subject", "size", "labels", "deleted")
SETTLEMENT_FIELDS = ("downloaded", "path")
DERIVED_FIELDS = ("domain",)


def normalize_message_id(raw: str | None) -> str | None:
    """Return the bare RFC ``Message-ID`` (``<...>`` stripped, lowercased).

    Returns None when the header is absent or carries no usable token, so the
    caller can fall back to a provider-tagged key. A ``Message-ID`` may arrive
    wrapped in comments or whitespace; ``parseaddr`` pulls the addr-spec out of
    the angle brackets, and a manual strip covers the rest.
    """
    if not raw:
        return None
    _, addr = email.utils.parseaddr(raw)
    candidate = (addr or raw).strip().strip("<>").strip().lower()
    return candidate or None


def message_key(*, message_id: str | None, provider: str, native_id: str) -> str:
    """The provider-neutral key for one message.

    The normalized ``Message-ID`` when present (portable across providers),
    else ``"<provider>:<native_id>"`` so every message still has a stable,
    unique key even when the header is missing or malformed.
    """
    normalized = normalize_message_id(message_id)
    if normalized:
        return normalized
    return f"{provider}:{native_id}"
