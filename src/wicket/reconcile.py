"""Idempotent local reconcile of the store, plus the filing-domain rule.

No IMAP. Across every shard, ``reconcile`` sets ``downloaded`` to whatever the
dest tree says (ground truth) and derives ``domain`` from observation. These are
the offline, deterministic truths a verb re-establishes before acting (ADR
0002): running it twice changes nothing.

The domain rule lives here because reconcile and fetch both need it, and it sits
below both verbs in the layering.
"""

from __future__ import annotations

from email.utils import getaddresses
from pathlib import Path

from wicket.config import normalize_account
from wicket.domains import DOMAIN_RE, canonical_domain, expand_domain
from wicket.manifest import Row, read_shard, store_shards, write_shard

# --- Filing-domain rule --------------------------------------------------


def _domain_of(address: str) -> str | None:
    address = address.strip().lower()
    if "@" not in address:
        return None
    return address.split("@", 1)[1] or None


def _parse_header_addresses(header_value: str) -> list[str]:
    if not header_value:
        return []
    return [addr.lower() for _, addr in getaddresses([header_value]) if addr]


def _canonical_external(addr: str, me: str, aliases: dict[str, str]) -> str | None:
    """Canonical domain of ``addr``, or None if it is ``me`` or malformed.

    The result becomes a filesystem path segment (`<domain>/<month>/<msgid>.eml`),
    so a crafted header like `x@../../etc` must never pass: DOMAIN_RE forbids `/`
    and `..`, and a rejected address just drops out.
    """
    if normalize_account(addr) == me:
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
) -> str | None:
    """Return the alias-canonical domain a thread files under, or None (ADR 0002).

    Direction decides the counterparty. For **inbound** mail (From is not you)
    the domain is the sender's, and ``To`` (including any forwarding address) is
    ignored. For **outbound** mail (From is you) it is the single external
    recipient's domain, or None when you wrote to several distinct domains.
    Applied to a thread's earliest message. ``imap_email`` is your own address
    (the mailbox owner); `+tag` aliasing is normalized.
    """
    aliases = domain_aliases or {}
    me = normalize_account(imap_email)
    from_addrs = _parse_header_addresses(from_header)
    sender = from_addrs[0] if from_addrs else ""
    if sender and normalize_account(sender) == me:
        domains = {
            d
            for addr in _parse_header_addresses(to_header)
            if (d := _canonical_external(addr, me, aliases)) is not None
        }
        return next(iter(domains)) if len(domains) == 1 else None
    return _canonical_external(sender, me, aliases)


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


# --- Reconcile -----------------------------------------------------------


def _thread_domain(
    observed: list[Row],
    imap_email: str,
    domain_aliases: dict[str, str],
) -> str | None:
    """Filing domain for a thread, from its earliest observed message."""
    observed.sort(key=lambda r: str(r.get("date", "")))
    first = observed[0]
    to_value = first.get("to")
    to_header = (
        ", ".join(str(x) for x in to_value)
        if isinstance(to_value, list)
        else str(to_value or "")
    )
    return compute_domain(
        str(first.get("from", "")), to_header, imap_email, domain_aliases
    )


def reconcile(
    store_dir: Path,
    dest: Path,
    imap_email: str,
    domain_aliases: dict[str, str],
) -> dict[str, int]:
    """Verify ``downloaded`` against disk and derive ``domain`` from observation.

    Per shard, every row's ``downloaded`` is set to whether its ``.eml`` is on
    disk, and every not-yet-downloaded row's ``domain`` is derived from its
    thread's earliest observed message (the From-first rule; None when it can't
    be resolved). A downloaded row keeps its settled domain: its ``.eml`` path is
    committed and is not moved. Offline, deterministic,
    idempotent.
    """
    rows_total = downloaded_fixed = domain_set = 0
    for shard in store_shards(store_dir):
        rows = read_shard(shard)
        new_rows: dict[str, Row] = {id_: dict(row) for id_, row in rows.items()}
        for row in new_rows.values():
            path = row.get("path")
            on_disk = bool(path) and (dest / str(path)).exists()
            if row.get("downloaded", False) != on_disk:
                downloaded_fixed += 1
            row["downloaded"] = on_disk
        by_thread: dict[str, list[Row]] = {}
        for row in new_rows.values():
            by_thread.setdefault(str(row.get("thread_id", "")), []).append(row)
        for members in by_thread.values():
            observed = [m for m in members if "from" in m]
            thread_domain = (
                _thread_domain(observed, imap_email, domain_aliases)
                if observed
                else None
            )
            for m in members:
                prior = m.get("domain")
                if m.get("downloaded"):
                    # The .eml path is committed; its first segment is the domain.
                    path = m.get("path")
                    domain = str(path).split("/", 1)[0] if path else prior
                elif observed:
                    domain = thread_domain  # derived (None when unresolvable)
                else:
                    domain = prior  # no observation, no path: keep
                if prior != domain:
                    domain_set += 1
                m["domain"] = domain
        rows_total += len(new_rows)
        write_shard(shard, new_rows)
    return {
        "rows": rows_total,
        "downloaded_fixed": downloaded_fixed,
        "domain_set": domain_set,
    }
