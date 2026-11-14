"""Which store a thread belongs in, and the search a domain filter becomes.

There are no folders and no filing rank (ADR-MAIL, 2026-11-14): a message's one file
is ``<store>/messages/YYYY/MM/<stem>.eml``, and a row is linked to every domain it
touches by its ``from``/``to``/``cc``/``bcc``.

- **The router** (`route_threads`): which store a thread belongs in.
- **The thread opener** (`opener`): a thread's earliest-``date`` row, whose thread id a
  local export's thread adopts.
- **The domain filter** (`build_domain_query`): the Gmail search ``--domains`` becomes.

Shared, so every verb reads one rule; it imports only the shared layer below it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone

from wicket.domains import DOMAIN_RE, expand_domain
from wicket.manifest import Row

# Resolves an address to the store that owns it, or None when nothing does. Supplied
# by an api (`env.account_of` bound to the alias map and the stores that exist), so a
# worker reads neither config files nor the disk to route.
AccountFor = Callable[[str], str | None]


# --- The domain filter ----------------------------------------------------


def build_domain_query(
    domains: list[str], domain_aliases: dict[str, str] | None = None
) -> str:
    """Build a Gmail search expression for messages from/to any of these domains.

    Each input domain is expanded to include its alias group. Uses Gmail's
    ``{X Y}`` OR-syntax, matching at the message level.
    """
    aliases = domain_aliases or {}
    expanded: set[str] = set()
    for d in domains:
        if not DOMAIN_RE.match(d):
            raise ValueError(f"invalid domain: {d!r}")
        for variant in expand_domain(d, aliases):
            expanded.add(variant)
    flat = sorted(expanded)
    terms = [f"from:{d}" for d in flat] + [f"to:{d}" for d in flat]
    return "{" + " ".join(terms) + "}"


# --- The thread rule -----------------------------------------------------


def _when(row: Row) -> datetime:
    """A row's ``date`` as an aware UTC datetime; an unreadable one sorts last."""
    try:
        parsed = datetime.fromisoformat(str(row.get("date", "")))
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def opener(rows: Iterable[Row]) -> Row | None:
    """The thread's opener: its earliest-``date`` row, ties broken by ``id``; None if empty."""
    return min(rows, key=lambda r: (_when(r), str(r.get("id", ""))), default=None)


# --- The router ----------------------------------------------------------


def participants(
    messages: list[dict[str, object]], account_for: AccountFor
) -> Counter[str]:
    """Per account, how many of these messages it took part in — sent or received.

    ``account_for`` resolves an address to the account that owns it
    (`env.account_of`): the alias map first, then the clear line where an
    address names its own store. An address nothing claims counts for nothing, so
    this is a comparison between destinations that actually exist.

    **The sender counts, not only the recipients.** Mail *you sent* is addressed
    entirely to counterparties, so a recipients-only tally leaves every outbound
    thread ownerless — which is most of a correspondence you started. Each message
    carries ``rcpt`` (every delivered-to address) and ``from``.
    """
    tally: Counter[str] = Counter()
    for m in messages:
        rcpt: list[str] = m["rcpt"]  # type: ignore[assignment]
        addresses = [*rcpt, str(m["from"])]
        owners = {a for addr in addresses if (a := account_for(addr)) is not None}
        tally.update(owners)
    return tally


def route_threads(
    members_by_thread: dict[str, list[dict[str, object]]],
    account_for: AccountFor,
    route_for: AccountFor = lambda _: None,
) -> tuple[dict[str, list[str]], list[str]]:
    """Assign each thread to the store it belongs in.

    **The thread is the routing unit, not the message.** A conversation where you
    were addressed on some replies and merely cc'd on others would otherwise
    split across two stores, breaking the one invariant the archive relies on:
    a thread has one ``thread_id`` and one folder. So every member votes, and the
    whole thread files where the vote lands.

    Two rules, and **the mailbox wins**:

    1. ``account_for`` — the account that took part in the thread, by identity.
    2. ``route_for`` — the counterparty map (`domains.load_domain_routes`), used
       only when no mailbox claims the thread: an order to a burner, a catch-all
       subdomain, a drag-export of a mailbox wicket cannot reach.

    The order matters and it is not a preference. A manifest is the observation
    record *of a mailbox*: `catalog` sweeps Gmail and writes what it sees into
    Gmail's manifest. If a counterparty rule could pull a Gmail-owned message into
    ``shopping``, the next catalog would re-observe it in the mailbox and write it
    back into Gmail's manifest, and the same message would live in two stores
    permanently. So a bucket is the *physical* home only for mail no mailbox owns;
    topical grouping of mailbox-owned mail is a view over it, never a relocation.

    Either resolver only ever names a destination that has a store, so a bucket
    you have not created yet is not a destination and its mail stays unrouted
    rather than minting a directory. Ties break toward the destination named on
    the thread's earliest message, then alphabetically, so a rerun always lands
    the same way. Each member carries ``date`` and ``id`` beside ``rcpt``/``from``.

    Returns ``({destination: [thread_id, ...]}, [unroutable thread_id, ...])``.
    """
    routed: dict[str, list[str]] = {}
    unrouted: list[str] = []
    for tid, members in members_by_thread.items():
        earliest = min(members, key=lambda m: (m["date"], str(m["id"])))
        for resolve in (account_for, route_for):
            scored = participants(members, resolve)
            if scored:
                first_choice = set(participants([earliest], resolve))
                routed.setdefault(_winner(scored, first_choice), []).append(tid)
                break
        else:
            unrouted.append(tid)
    return routed, unrouted


def _winner(scored: Counter[str], first_choice: set[str]) -> str:
    """Most members addressed, then the earliest message's account, then the name."""
    return min(scored, key=lambda a: (-scored[a], a not in first_choice, a))
