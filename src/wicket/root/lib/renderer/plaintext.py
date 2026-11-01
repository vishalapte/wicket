"""The wicket views as terminal text.

Every line this noun's cli prints is written here. Each ``print_<verb>`` takes that verb's
result and writes it; it reads no store and decides nothing, and it reads the same types
the api returns, so what it prints cannot drift from them.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..results import (
    AccountCounts,
    AccountsView,
    BucketView,
    BusyStore,
    ByYearView,
    CatalogRoot,
    FetchRoot,
    IngestRoot,
    IngestStore,
    ReportRoot,
)


def halt_text(line: str, path: str) -> str:
    """A HALT's first line, its file, and the command that clears it (named by account)."""
    return (
        f"halted: {line}\n"
        f"HALT at {path}; clear it with `python -m wicket.halt clear --mail-account "
        f"{Path(path).name}` (if the provider rejected the app password, re-seed it first)"
    )


def print_catalog(result: CatalogRoot) -> None:
    """``wicket catalog`` as text: the summary on stdout, each warning on stderr.

    An account with no folder prints nothing.
    """
    if not result["account_linked"]:
        return
    verb = "would write" if result["dry_run"] else "wrote"
    print(
        f"done: observed {result['messages']} message(s); {verb} "
        f"{result['shards']} year shard(s) under {result['store_dir']}"
        + (f"; removed {result['removed']} stale shard(s)" if result["removed"] else "")
        + (
            f"; {result['boundary_drops']} boundary overlap(s) deduped"
            if result["boundary_drops"]
            else ""
        )
    )
    if result["parse_skips"]:
        print(
            f"warning: {result['parse_skips']} unparseable FETCH response(s)",
            file=sys.stderr,
        )
    if result["label_misses"]:
        print(
            f"warning: {result['label_misses']} row(s) with labels unrecoverable "
            "(labels: null)",
            file=sys.stderr,
        )


def fetch_lines(result: FetchRoot) -> list[str]:
    """The run summary: one pass with a filter, every year's passes without.

    "got" is mail already staged, filed, excluded or set aside as a duplicate, never
    "held", which means filed in the archive. The api says these lines too, before the
    HALT, when a run halts partway.
    """
    # Said only when non-zero, so a run that links nothing prints what it always did.
    linked = result["linked"]
    link_note = f" {linked} linked to an archived copy." if linked else ""
    if result["filtered"]:
        span = f"{result['got']} already got."
    else:
        passes = result["passes"]
        years = len({p["year"] for p in passes})
        span = f"over {len(passes)} pass(es) in {years} year(s)."
    if result["dry_run"]:
        return [
            f"done (dry-run): {result['pending']} to download, {span}{link_note} "
            "Nothing written."
        ]
    lines = [
        f"done: {result['downloaded']} downloaded, {result['failed']} failed, {span}"
        f"{link_note}"
    ]
    if result["downloaded"]:
        lines.append(
            "staged, not yet filed: wicket ingest --staged --mail-account "
            f"{result['account'] or '<account>'}"
        )
    return lines


def print_fetch(result: FetchRoot) -> None:
    """``wicket fetch`` as text: the run summary; an account with no folder prints none."""
    if not result["account_linked"]:
        return
    for line in fetch_lines(result):
        print(line)


def _filed_lines(filed: dict[str, list[str]]) -> list[str]:
    """The filed ``.eml`` under each archive directory, both name-sorted."""
    lines = []
    for directory in sorted(filed):
        lines.append(f"    {directory}/")
        lines += [f"      {name}" for name in filed[directory]]
    return lines


def _single(result: IngestRoot) -> IngestStore:
    """A single-target run's one store, shaped like one entry of a routed run's list."""
    return {
        "account": result["account"],
        "added": result["added"],
        "unfiled": result["unfiled"],
        "added_by_year": result["added_by_year"],
        "top_domains": result["top_domains"],
        "by_rule": result["by_rule"],
        "filed": result["filed"],
        "skipped": result["skipped"],
        "excluded": result["excluded"],
        "excluded_lists": result["excluded_lists"],
    }


def ingest_lines(result: IngestRoot) -> list[str]:
    """A ``--src`` run's summary: what was parsed, where it went, what was left.

    The api says these lines too, before each busy store, when a routed run skips one.
    """
    verb = "would add" if result["dry_run"] else "added"
    lines = [
        f"read: {result['files']} file(s), {result['unique']} unique, "
        f"{result['dup']} in-folder dup(s)"
    ]
    stores = result["accounts"] if result["mode"] == "routed" else [_single(result)]
    for got in stores:
        by_year = ", ".join(f"{y}:{n}" for y, n in got["added_by_year"].items())
        line = f"  {verb} {got['added']} to {got['account']}"
        if by_year:
            line += f" ({by_year})"
        if got["skipped"]:
            line += f"; {got['skipped']} already archived"
        if got["excluded"]:
            line += f"; {got['excluded']} excluded, skipped"
        if got["excluded_lists"]:
            line += f"; {got['excluded_lists']} from an excluded mailing list, skipped"
        if got["unfiled"]:
            line += f"; {got['unfiled']} unfiled"
        lines.append(line)
        lines += _filed_lines(got["filed"])
    if result["unrouted"]:
        lines.append(
            f"  left alone: {result['unrouted']} message(s) addressed to no known "
            "account (add an alias, or create the store, to claim them)"
        )
    if result["to_trash"]:
        moved = "would trash" if result["dry_run"] else "trashed"
        count = result["trashable"] if result["dry_run"] else result["trashed"]
        lines.append(f"  {moved}: {count} source file(s) -> ~/.Trash")
    return lines


def busy_lines(busy: list[BusyStore]) -> list[str]:
    """Each store a routed run skipped, its holder, and the messages left for next time."""
    return [
        f"busy: {store['account']} is locked by pid {store['pid'] or '?'}; its "
        f"{store['messages']} message(s) were left for the next run"
        for store in busy
    ]


def _print_staged(result: IngestRoot) -> None:
    """A staged run: what was staged, what was filed where, what was left."""
    verb = "would file" if result["dry_run"] else "filed"
    by_year = ", ".join(f"{y}:{n}" for y, n in result["added_by_year"].items())
    line = (
        f"staged: {result['staged']} file(s); {verb} {result['added']} to "
        f"{result['account']}"
    )
    if by_year:
        line += f" ({by_year})"
    print(line)
    rules = result["by_rule"]
    if rules:
        print("  by rule: " + ", ".join(f"{k} {n}" for k, n in rules.items()))
    for count, label in (
        (result["archived"], "already archived (staged copy removed)"),
        (result["duplicates"], "set aside as duplicates"),
        (result["excluded"], "excluded, skipped and left staged"),
        (result["excluded_lists"], "from an excluded mailing list, left staged"),
        (result["unfiled"], "unfiled"),
    ):
        if count:
            print(f"  {count} {label}")
    for names, label in (
        (result["refused"], "refused (a different file is in the way; left staged)"),
        (result["unrecognized"], "not a staged name (left alone)"),
    ):
        if names:
            print(f"  {len(names)} {label}:", file=sys.stderr)
            for name in names:
                print(f"    {name}", file=sys.stderr)
    for filed in _filed_lines(result["filed"]):
        print(filed)


def print_ingest(result: IngestRoot) -> None:
    """``wicket ingest`` as text: the run summary on stdout, a warning on stderr."""
    if result["mode"] == "staged":
        _print_staged(result)
        return
    if result.get("warning"):
        print(f"warning: {result['warning']}", file=sys.stderr)
    for line in ingest_lines(result):
        print(line)


def _print_bucket(view: BucketView) -> None:
    """Per store: how many messages the bucket claims, and from which domains."""
    if not view["stores"]:
        print(f"{view['name']}: no mail in any store")
        return
    for store in view["stores"]:
        print(
            f"{store['account']}: {store['messages']} message(s), "
            f"{store['held']} held locally"
        )
        for row in store["domains"]:
            print(f"  {row['count']:>6}  {row['domain']}")
    print(
        f"{view['name']}: {view['total']} message(s) across {len(view['stores'])} "
        "store(s)"
    )


def _print_by_year(view: ByYearView) -> None:
    """Accounts in rows, years in columns, each cell ``local/not local``, then totals."""
    years = view["years"]
    table = [["account", *map(str, years), "total"]]
    for row in view["accounts"]:
        cells = {c["year"]: f"{c['local']}/{c['not_local']}" for c in row["years"]}
        table.append(
            [
                row["account"],
                *(cells.get(year, "-") for year in years),
                f"{row['local']}/{row['not_local']}",
            ]
        )
    totals = {year: [0, 0] for year in years}
    for row in view["accounts"]:
        for c in row["years"]:
            totals[c["year"]][0] += c["local"]
            totals[c["year"]][1] += c["not_local"]
    table.append(
        [
            "total",
            *(f"{totals[year][0]}/{totals[year][1]}" for year in years),
            f"{view['local']}/{view['not_local']}",
        ]
    )
    widths = [max(len(line[i]) for line in table) for i in range(len(table[0]))]
    print("local/not local, by account and year")
    for line in table:
        first, *rest = line
        print(
            "  ".join(
                [first.ljust(widths[0])]
                + [cell.rjust(widths[i + 1]) for i, cell in enumerate(rest)]
            )
        )


def _print_accounts(view: AccountsView) -> None:
    """Accounts in rows; downloaded, excluded, not downloaded and total in columns."""
    table = [["account", "downloaded", "excluded", "not downloaded", "total"]]
    total: AccountCounts = {
        "account": "total",
        "downloaded": view["downloaded"],
        "excluded": view["excluded"],
        "not_downloaded": view["not_downloaded"],
        "total": view["total"],
    }
    for row in [*view["accounts"], total]:
        table.append(
            [
                row["account"],
                str(row["downloaded"]),
                str(row["excluded"]),
                str(row["not_downloaded"]),
                str(row["total"]),
            ]
        )
    widths = [max(len(line[i]) for line in table) for i in range(len(table[0]))]
    for line in table:
        first, *rest = line
        print(
            "  ".join(
                [first.ljust(widths[0])]
                + [cell.rjust(widths[i + 1]) for i, cell in enumerate(rest)]
            )
        )


def print_report(result: ReportRoot) -> None:
    """``wicket report`` as text: the summary, one record per line, a bucket, by year,
    or every account's counts."""
    if result["view"] == "accounts":
        _print_accounts(result["accounts"])
    elif result["view"] == "by_year":
        _print_by_year(result["by_year"])
    elif result["view"] == "bucket":
        _print_bucket(result["bucket"])
    elif result["view"] == "senders":
        for row in result["senders"]:
            print(f"{row['count']}\t{row['address']}")
    elif result["view"] == "addresses":
        for address in result["addresses"]:
            print(address)
    else:
        counts = result["counts"]
        print(f"manifest: {result['store_dir']}")
        print(f"  messages           {counts['messages']:>8}")
        print(f"  downloaded         {counts['downloaded']:>8}  (held locally)")
        print(f"    gone from mailbox{counts['downloaded_gone']:>8}  (only copy)")
        print(f"    still in mailbox {counts['downloaded_present']:>8}")
        print(f"  observed only      {counts['observed_only']:>8}  (not held)")
        print(
            f"  excluded           {counts['excluded']:>8}  (duplicates, excluded lists)"
        )
        print(f"  distinct senders   {counts['senders']:>8}")
        print(f"  distinct addresses {counts['addresses']:>8}")


def error(message: str) -> None:
    """A refusal the api raised, on stderr, so stdout stays empty."""
    print(message, file=sys.stderr)
