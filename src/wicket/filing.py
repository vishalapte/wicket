"""Where a message is filed: the one home for every filing rule (plan rule 8).

Filing decides a thread's folder (``<archive>/<folder>/YYYY-MM/<msgid>.eml``) and
records which rule chose it as the row's ``filed``. The rank, first match wins:

1. **anchor**: a downloaded member of the thread, its ``.eml`` on disk; new members
   join its folder and copy its ``filed`` (nothing when it has none);
2. **forced**: ``ingest --domain D``;
3. **self**: the opener is from you and every recipient is you, or there is none;
4. **sent**: the opener is from you and its ``To``, folded, without your addresses,
   spans two or more domains;
5. **rule**: the computed counterparty domain (`compute_domain`);
6. **fallback**: your own domain and addresses stripped from ``To``, one domain left;
7. **unfiled**: ``_unfiled``.

The **opener** is a thread's earliest-``date`` row across every shard of the store
(thread ids are per store), so the rule reads the message that started the thread,
whichever of its members is being filed. Steps 3-7 are `place`: what the maps and
identities say, with no anchor and no override.

Also here: the search a domain filter becomes (`build_domain_query`) and the
**router** (`route_threads`), which picks the store a thread belongs in. Shared, so
every verb that files reads one rule; it imports only the shared layer below it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import getaddresses

from wicket.domains import DOMAIN_RE, canonical_domain, expand_domain
from wicket.env import normalize_account
from wicket.manifest import Row

UNFILED = "_unfiled"
SELF = "self"
SENT = "sent"

# A row's ``filed``: which rank placed it. An anchored member copies its anchor's.
FILED_RULE = "rule"
FILED_FORCED = "forced"
FILED_SELF = "self"
FILED_SENT = "sent"
FILED_FALLBACK = "fallback"
FILED_UNFILED = "unfiled"
FILED_KINDS = (
    FILED_RULE,
    FILED_FORCED,
    FILED_SELF,
    FILED_SENT,
    FILED_FALLBACK,
    FILED_UNFILED,
)

# Resolves an address to the store that owns it, or None when nothing does. Supplied
# by an api (`env.account_of` bound to the alias map and the stores that exist), so a
# worker reads neither config files nor the disk to route.
AccountFor = Callable[[str], str | None]


# --- The domain rule -----------------------------------------------------


def _domain_of(address: str) -> str | None:
    address = address.strip().lower()
    if "@" not in address:
        return None
    return address.split("@", 1)[1] or None


def _parse_header_addresses(header_value: str) -> list[str]:
    if not header_value:
        return []
    return [addr.lower() for _, addr in getaddresses([header_value]) if addr]


def _canonical_external(
    addr: str, me: str, aliases: dict[str, str], mine: Container[str] = frozenset()
) -> str | None:
    """Canonical domain of ``addr``, or None if it is ``me`` or malformed.

    ``mine`` is the owner's other addresses (`env.identities`); one of them is
    no more a counterparty than ``me`` is. Empty by default, so a caller that
    knows only the mailbox address gets the historical behavior.

    The result becomes a filesystem path segment (`<domain>/<month>/<msgid>.eml`),
    so a crafted header like `x@../../etc` must never pass: DOMAIN_RE forbids `/`
    and `..`, and a rejected address just drops out.
    """
    canonical = normalize_account(addr)
    if canonical == me or canonical in mine:
        return None
    domain = _domain_of(addr)
    if domain and DOMAIN_RE.match(domain):
        return canonical_domain(domain, aliases)
    return None


def compute_domain(
    from_header: str,
    to_header: str,
    imap_email: str,
    domain_aliases: dict[str, str] | None = None,
    identities: Container[str] = frozenset(),
) -> str | None:
    """Return the alias-canonical domain a thread files under, or None (ADR 0002).

    Direction decides the counterparty. For **inbound** mail (From is not you)
    the domain is the sender's, and ``To`` (including any forwarding address) is
    ignored. For **outbound** mail (From is you) it is the single external
    recipient's domain, or None when you wrote to several distinct domains.
    Applied to a thread's opener. ``imap_email`` is your own address
    (the mailbox owner); `+tag` aliasing is normalized. ``identities`` widens
    "you" to every address you receive at (`env.identities`), so mail sent to
    a burner or forwarding address of yours still files under the counterparty
    rather than under your own alias domain.
    """
    aliases = domain_aliases or {}
    me = normalize_account(imap_email)
    from_addrs = _parse_header_addresses(from_header)
    sender = from_addrs[0] if from_addrs else ""
    if sender and (normalize_account(sender) == me or sender in identities):
        domains = {
            d
            for addr in _parse_header_addresses(to_header)
            if (d := _canonical_external(addr, me, aliases, identities)) is not None
        }
        return next(iter(domains)) if len(domains) == 1 else None
    return _canonical_external(sender, me, aliases, identities)


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


def recipients(row: Row) -> str:
    """A row's ``to`` as one header value (a row stores it as a list of addresses)."""
    value = row.get("to")
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return str(value or "")


def thread_domain(
    observed: Iterable[Row], imap_email: str, domain_aliases: dict[str, str]
) -> str | None:
    """The counterparty domain of a thread's opener, or None (reconcile's prediction)."""
    first = opener(observed)
    if first is None:
        return None
    return compute_domain(
        str(first.get("from", "")), recipients(first), imap_email, domain_aliases
    )


# --- The rank ------------------------------------------------------------


@dataclass(frozen=True)
class Owner:
    """Who *you* are in one store, which every rule below the anchor needs.

    ``account`` is the store's own name (an address, or a bucket's slug);
    ``identities`` is `env.identities` (every address that is you, wildcards too);
    ``domain_aliases`` folds a subdomain onto its primary (``t.delta.com`` ->
    ``delta.com``).
    """

    account: str
    identities: Container[str]
    domain_aliases: dict[str, str]

    def __contains__(self, address: object) -> bool:
        if not isinstance(address, str):
            return False
        return (
            normalize_account(address) == normalize_account(self.account)
            or address in self.identities
        )


def _fallback(to_addrs: list[str], owner: Owner) -> str | None:
    """Owner rule: strip yourself from To; if one external domain remains, use it.

    "Yourself" is your own domain plus every address in ``owner.identities``: without
    the latter, a message to a burner address of yours leaves that burner's domain
    standing as the lone "external" one and files under it. The survivor is folded to
    its primary (``t.delta.com`` -> ``delta.com``), as the main rule folds.
    """
    account = owner.account
    own = account.split("@", 1)[1] if "@" in account else ""
    external = {
        canonical_domain(d, owner.domain_aliases)
        for a in to_addrs
        if (d := _domain_of(a))
        and DOMAIN_RE.match(d)
        and d != own
        and a not in owner.identities
    }
    return next(iter(external)) if len(external) == 1 else None


def place(from_header: str, to_header: str, owner: Owner) -> tuple[str, str]:
    """``(folder, filed)`` for a thread whose opener has these ``From`` and ``To``.

    The rank below the anchor and the override: self, sent, the computed domain,
    the fallback, ``_unfiled``. Self and sent apply only to an opener from you. "You"
    is the store's account and every address in ``owner.identities``.
    """
    senders = _parse_header_addresses(from_header)
    to_addrs = _parse_header_addresses(to_header)
    if senders and senders[0] in owner:
        others = [a for a in to_addrs if a not in owner]
        if not others:
            return SELF, FILED_SELF
        me = normalize_account(owner.account)
        domains = {
            d
            for a in others
            if (d := _canonical_external(a, me, owner.domain_aliases)) is not None
        }
        if len(domains) >= 2:
            return SENT, FILED_SENT
    domain = compute_domain(
        from_header, to_header, owner.account, owner.domain_aliases, owner.identities
    )
    if domain is not None:
        return domain, FILED_RULE
    fallback = _fallback(to_addrs, owner)
    if fallback is not None:
        return fallback, FILED_FALLBACK
    return UNFILED, FILED_UNFILED


def anchor_folder(anchor: Row) -> str:
    """The folder an anchor's thread lives in: its committed path's first segment."""
    return str(anchor.get("path") or "").split("/", 1)[0]


def file_thread(
    *,
    anchor: Row | None,
    forced: str | None,
    first: Row | None,
    owner: Owner,
) -> tuple[str, str | None]:
    """``(folder, filed)`` for a thread, by the whole rank (module docstring).

    ``anchor`` is the thread's earliest downloaded row whose ``.eml`` is on disk, or
    None; ``forced`` is ``--domain``; ``first`` is the thread's opener. An anchored
    thread copies the anchor's ``filed``, which is None when the anchor has none.
    """
    if anchor is not None:
        filed = anchor.get("filed")
        return anchor_folder(anchor), str(filed) if filed else None
    if forced:
        return forced, FILED_FORCED
    if first is None:
        return UNFILED, FILED_UNFILED
    return place(str(first.get("from", "")), recipients(first), owner)


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
