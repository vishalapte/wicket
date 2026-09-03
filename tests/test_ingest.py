"""Tests for the ingest verb: the account guard, identity-aware filing, additivity.

The guard exists because ``--account`` is a *destination*, not a filter: it names
the store to write into and says nothing about the folder. Naming the wrong one
files an entire export into the wrong mailbox, silently and additively.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from wicket.env import Identities, account_of
from wicket.ingest.cli import _render
from wicket.ingest.lib import (
    AccountFor,
    Target,
    check_account_matches,
    ingest_folder,
    ingest_routed,
)
from wicket.manifest import load_store

ME = "you@gmail.com"
WORK = "work@corp.com"
# Only the exceptions: a burner, and a catch-all subdomain routed to a bucket.
# you@gmail.com and work@corp.com are "clear lines" and are deliberately absent.
ALIASES = {
    "burner@duck.com": ME,
    "*@shop.you.com": "shopping",
    "shopping": "shopping",
}
ACCOUNTS = {ME, WORK, "shopping"}  # the stores that exist


def _account_for(accounts: set[str] | None = None) -> AccountFor:
    live = ACCOUNTS if accounts is None else accounts
    return lambda address: account_of(address, ALIASES, live)


def _identities() -> Identities:
    return Identities(
        frozenset({ME, WORK, "burner@duck.com"}), frozenset({"shop.you.com"})
    )


def _eml(folder: Path, name: str, *, frm: str, to: str, cc: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.eml"
    headers = [
        f"Message-ID: <{name}@test>",
        f"From: {frm}",
        f"To: {to}",
        "Date: Mon, 06 Jul 2026 09:00:00 +0000",
        f"Subject: {name}",
    ]
    if cc:
        headers.insert(3, f"Cc: {cc}")
    path.write_text("\n".join(headers) + "\n\nbody\n", encoding="utf-8")
    return path


FOLDS = {"*.delta.com": "delta.com", "delta.com": "delta.com"}


def _target(
    root: Path,
    account: str = ME,
    identities: Identities | None = None,
    folds: dict[str, str] | None = None,
    *,
    filing_domain: str | None = None,
    labels: list[str] | None = None,
) -> Target:
    return Target(
        store_dir=root / account / "manifest",
        archive_dir=root / account / "archive",
        account=account,
        identities=_identities() if identities is None else identities,
        domain_aliases=FOLDS if folds is None else folds,
        filing_domain=filing_domain,
        labels=labels,
    )


def _parsed(folder: Path) -> list[dict[str, object]]:
    from wicket.ingest.lib import _dedup_folder

    messages, _, _ = _dedup_folder(sorted(folder.glob("*.eml")))
    return messages


# --- the account guard ----------------------------------------------------


def test_guard_refuses_when_the_folder_belongs_to_another_account(
    tmp_path: Path,
) -> None:
    _eml(tmp_path, "a", frm="them@acme.com", to="work@corp.com")
    _eml(tmp_path, "b", frm="them@acme.com", to="work@corp.com")
    _eml(tmp_path, "c", frm="them@acme.com", to=ME)

    complaint = check_account_matches(_parsed(tmp_path), ME, _account_for())
    assert complaint is not None
    assert "work@corp.com" in complaint  # names the account that actually owns it


def test_guard_allows_the_account_the_folder_is_addressed_to(tmp_path: Path) -> None:
    _eml(tmp_path, "a", frm="them@acme.com", to="work@corp.com")
    _eml(tmp_path, "b", frm="them@acme.com", to=ME)
    _eml(tmp_path, "c", frm="them@acme.com", to=ME)

    assert check_account_matches(_parsed(tmp_path), ME, _account_for()) is None


def test_guard_counts_a_burner_for_the_account_that_owns_it(tmp_path: Path) -> None:
    # Addressed to a burner, so the naive read is "not for you@gmail.com at all";
    # the alias map says otherwise, and the guard must not fire.
    _eml(tmp_path, "a", frm="them@acme.com", to="burner@duck.com")
    _eml(tmp_path, "b", frm="them@acme.com", to="burner@duck.com")

    assert check_account_matches(_parsed(tmp_path), ME, _account_for()) is None


def test_guard_counts_cc_not_just_to(tmp_path: Path) -> None:
    _eml(tmp_path, "a", frm="them@acme.com", to="x@acme.com", cc="work@corp.com")
    complaint = check_account_matches(_parsed(tmp_path), ME, _account_for())
    assert complaint is not None and "work@corp.com" in complaint


def test_guard_is_silent_when_no_recipient_is_a_known_account(tmp_path: Path) -> None:
    # Nothing to compare against: the map cannot say this folder is misdirected.
    _eml(tmp_path, "a", frm="them@acme.com", to="stranger@nowhere.com")
    assert check_account_matches(_parsed(tmp_path), ME, _account_for(set())) is None


def test_ingest_folder_refuses_a_mismatch_and_force_overrides(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to="work@corp.com")
    target = _target(tmp_path)

    with pytest.raises(ValueError, match="account mismatch"):
        ingest_folder(src=src, target=target, account_for=_account_for())
    assert not target.archive_dir.exists()  # refused before anything was written

    stats = ingest_folder(
        src=src, target=target, account_for=_account_for(), force=True
    )
    assert stats["added"] == 1
    assert stats["warning"]  # forced, but the complaint is still surfaced


# --- identity-aware filing ------------------------------------------------


def test_outbound_to_a_burner_files_under_the_counterparty(tmp_path: Path) -> None:
    src = tmp_path / "src"
    # You wrote to a client and to a burner of your own. Without the identity
    # set, that is two external domains, so the thread files nowhere (_unfiled);
    # with it, the burner is you and the client is the counterparty.
    _eml(src, "a", frm=ME, to="client@acme.com, burner@duck.com")

    stats = ingest_folder(src=src, target=_target(tmp_path), account_for=_account_for())
    assert stats["added"] == 1
    assert (tmp_path / ME / "archive" / "acme.com").is_dir()
    assert not (tmp_path / ME / "archive" / "duck.com").exists()
    assert stats["unfiled"] == 0


def test_without_the_identity_set_the_same_thread_goes_unfiled(tmp_path: Path) -> None:
    # The pre-alias behavior, pinned: this is the defect the identity set fixes.
    src = tmp_path / "src"
    _eml(src, "a", frm=ME, to="client@acme.com, burner@duck.com")

    bare = Identities(frozenset({ME}), frozenset())  # no burner, no catch-all
    stats = ingest_folder(
        src=src,
        target=_target(tmp_path, identities=bare),
        account_for=_account_for(),
    )
    assert stats["unfiled"] == 1


# --- additivity (the property the whole verb rests on) --------------------


def test_ingest_is_idempotent(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)
    target = _target(tmp_path)

    assert (
        ingest_folder(src=src, target=target, account_for=_account_for())["added"] == 1
    )
    assert (
        ingest_folder(src=src, target=target, account_for=_account_for())["added"] == 0
    )


def test_a_re_run_counts_what_it_skipped(tmp_path: Path) -> None:
    # The gap between what a run reads and what it adds. Left uncounted, a run
    # that trashes every source file while adding fewer rows than it read is
    # indistinguishable from one that lost mail.
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)
    _eml(src, "b", frm="them@acme.com", to=ME)
    target = _target(tmp_path)

    first = ingest_folder(src=src, target=target, account_for=_account_for())
    assert (first["added"], first["skipped"]) == (2, 0)

    _eml(src, "c", frm="them@acme.com", to=ME)
    again = ingest_folder(src=src, target=target, account_for=_account_for())
    assert (again["unique"], again["added"], again["skipped"]) == (3, 1, 2)


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)

    stats = ingest_folder(
        src=src, target=_target(tmp_path), account_for=_account_for(), dry_run=True
    )
    assert stats["added"] == 1
    assert not (tmp_path / ME / "archive").exists()
    assert not (tmp_path / ME / "manifest").exists()


# --- routing (the thread is the unit) -------------------------------------


def _routed(tmp_path: Path, **kw: object) -> dict[str, object]:
    targets = {a: _target(tmp_path / "mail", account=a) for a in ACCOUNTS}
    return ingest_routed(
        src=tmp_path / "src",
        targets=targets,
        account_for=_account_for(),
        **kw,  # type: ignore[arg-type]
    )


def test_routing_splits_a_mixed_folder_by_account(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)
    _eml(src, "b", frm="them@acme.com", to=WORK)

    stats = _routed(tmp_path, to_trash=False)
    by_account = {a["account"]: a["added"] for a in stats["accounts"]}  # type: ignore[attr-defined]
    assert by_account == {ME: 1, WORK: 1}  # one folder, two stores


def test_a_thread_is_not_split_across_accounts(tmp_path: Path) -> None:
    # Root is to WORK; the reply cc's ME. Per-message routing would tear the
    # thread in half; per-thread routing keeps it whole, in WORK's store.
    src = tmp_path / "src"
    _eml(src, "root", frm="them@acme.com", to=WORK)
    reply = src / "reply.eml"
    reply.write_text(
        "Message-ID: <reply@test>\n"
        "In-Reply-To: <root@test>\n"
        "From: them@acme.com\n"
        f"To: {WORK}\n"
        f"Cc: {ME}\n"
        "Date: Tue, 07 Jul 2026 09:00:00 +0000\n"
        "Subject: re\n\nbody\n",
        encoding="utf-8",
    )

    stats = _routed(tmp_path, to_trash=False)
    by_account = {a["account"]: a["added"] for a in stats["accounts"]}  # type: ignore[attr-defined]
    assert by_account == {WORK: 2}
    assert ME not in by_account


def test_a_wildcard_alias_routes_to_its_bucket(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="deals@shop.com", to="anything@shop.you.com")

    stats = _routed(tmp_path, to_trash=False)
    by_account = {a["account"]: a["added"] for a in stats["accounts"]}  # type: ignore[attr-defined]
    assert by_account == {"shopping": 1}  # a bucket, not a mailbox


def test_unclaimed_mail_is_left_alone(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to="stranger@nowhere.com")

    stats = _routed(tmp_path, to_trash=False)
    assert stats["added"] == 0
    assert stats["unrouted"] == 1
    assert (src / "a.eml").exists()  # never trashed: it has no home to go to


def test_a_bucket_without_a_store_is_not_a_destination(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="deals@shop.com", to="anything@shop.you.com")

    # "shopping" is in the alias map but has no store dir: it must not be created.
    targets = {a: _target(tmp_path / "mail", account=a) for a in (ME, WORK)}
    stats = ingest_routed(
        src=src,
        targets=targets,
        account_for=_account_for({ME, WORK}),
        to_trash=False,
    )
    assert stats["unrouted"] == 1
    assert not (tmp_path / "mail" / "shopping").exists()


# --- trashing -------------------------------------------------------------


def test_archived_sources_are_trashed_and_unrouted_ones_are_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / ".Trash"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    src = tmp_path / "src"
    _eml(src, "mine", frm="them@acme.com", to=ME)
    _eml(src, "orphan", frm="them@acme.com", to="stranger@nowhere.com")

    stats = _routed(tmp_path, to_trash=True)
    assert stats["trashed"] == 1
    assert not (src / "mine.eml").exists()  # durably archived -> trashed
    assert (src / "orphan.eml").exists()  # nobody claimed it -> untouched
    assert (bin_dir / "mine.eml").exists()  # moved, never unlinked


def test_dry_run_trashes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    src = tmp_path / "src"
    _eml(src, "mine", frm="them@acme.com", to=ME)

    stats = _routed(tmp_path, dry_run=True, to_trash=True)
    assert stats["trashable"] == 1 and stats["trashed"] == 0
    assert (src / "mine.eml").exists()


def test_mail_you_sent_routes_to_the_account_you_sent_it_from(tmp_path: Path) -> None:
    # Every recipient is a counterparty, so a recipients-only tally would strand
    # this thread. The sender is the owner.
    src = tmp_path / "src"
    _eml(src, "a", frm=ME, to="client@acme.com")

    stats = _routed(tmp_path, to_trash=False)
    by_account = {a["account"]: a["added"] for a in stats["accounts"]}  # type: ignore[attr-defined]
    assert by_account == {ME: 1}
    assert stats["unrouted"] == 0


# --- counterparty routes (the domain map) ---------------------------------


def _route_for(routes: dict[str, str]) -> AccountFor:
    from wicket.domains import route_of

    return lambda address: route_of(address, routes)


ROUTES = {"ikea.com": "shopping", "*.thuma.co": "shopping", "shopping": "shopping"}


def test_the_mailbox_wins_over_a_counterparty_route(tmp_path: Path) -> None:
    # IKEA mail that landed in a real mailbox stays there. If the bucket took it,
    # the next catalog of that mailbox would re-observe the message and write it
    # back into the mailbox's manifest, and it would live in two stores forever.
    # Topical grouping of mailbox-owned mail is a view, never a relocation.
    src = tmp_path / "src"
    _eml(src, "a", frm="offers@ikea.com", to=ME)

    targets = {a: _target(tmp_path / "mail", account=a) for a in ACCOUNTS}
    stats = ingest_routed(
        src=src,
        targets=targets,
        account_for=_account_for(),
        route_for=_route_for(ROUTES),
        to_trash=False,
    )
    by_account = {a["account"]: a["added"] for a in stats["accounts"]}  # type: ignore[attr-defined]
    assert by_account == {ME: 1}


def test_a_counterparty_route_matches_a_subdomain(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="hello@mail.thuma.co", to="kaveri@hey.com")

    targets = {a: _target(tmp_path / "mail", account=a) for a in ACCOUNTS}
    stats = ingest_routed(
        src=src,
        targets=targets,
        account_for=_account_for(),
        route_for=_route_for(ROUTES),
        to_trash=False,
    )
    by_account = {a["account"]: a["added"] for a in stats["accounts"]}  # type: ignore[attr-defined]
    assert by_account == {"shopping": 1}


def test_a_counterparty_is_not_an_identity(tmp_path: Path) -> None:
    # The whole reason the two maps are separate: if ikea.com were in the alias
    # map it would count as *you*, the message would look outbound, and it would
    # file under the recipient's domain instead of ikea.com.
    src = tmp_path / "src"
    _eml(src, "a", frm="offers@ikea.com", to=ME)

    targets = {a: _target(tmp_path / "mail", account=a) for a in ACCOUNTS}
    ingest_routed(
        src=src,
        targets=targets,
        account_for=_account_for(),
        route_for=_route_for(ROUTES),
        to_trash=False,
    )
    # The mailbox owns it, and inside that store it files under the counterparty.
    assert (tmp_path / "mail" / ME / "archive" / "ikea.com").is_dir()
    assert not (tmp_path / "mail" / ME / "archive" / "gmail.com").exists()


# --- subdomain folding (ingest used to ignore domain-aliases entirely) ----


def test_a_marketing_subdomain_folds_to_its_primary(tmp_path: Path) -> None:
    # Without the fold map a bucket fragments: t.delta.com, email.delta.com,
    # mail.delta.com, one folder per campaign host.
    src = tmp_path / "src"
    _eml(src, "a", frm="offers@t.delta.com", to=ME)

    stats = ingest_folder(src=src, target=_target(tmp_path), account_for=_account_for())
    assert stats["added"] == 1
    assert (tmp_path / ME / "archive" / "delta.com").is_dir()
    assert not (tmp_path / ME / "archive" / "t.delta.com").exists()


def test_without_the_fold_map_the_subdomain_stands(tmp_path: Path) -> None:
    # The pre-fix behavior, pinned.
    src = tmp_path / "src"
    _eml(src, "a", frm="offers@t.delta.com", to=ME)

    ingest_folder(
        src=src, target=_target(tmp_path, folds={}), account_for=_account_for()
    )
    assert (tmp_path / ME / "archive" / "t.delta.com").is_dir()


# --- a single .eml is a folder of one -------------------------------------


def test_src_may_be_one_eml_file(tmp_path: Path) -> None:
    # The common case: one message dragged out of a client, not a whole export.
    one = _eml(tmp_path / "src", "a", frm="them@acme.com", to=ME)

    stats = ingest_folder(src=one, target=_target(tmp_path), account_for=_account_for())
    assert stats["files"] == 1
    assert stats["added"] == 1
    assert (tmp_path / ME / "archive" / "acme.com").is_dir()


def test_a_routed_run_takes_one_eml_file_too(tmp_path: Path) -> None:
    one = _eml(tmp_path / "src", "a", frm="them@acme.com", to=WORK)

    stats = ingest_routed(
        src=one,
        targets={a: _target(tmp_path, a) for a in sorted(ACCOUNTS)},
        account_for=_account_for(),
    )
    assert stats["unrouted"] == 0
    assert (tmp_path / WORK / "archive" / "acme.com").is_dir()


def test_a_file_that_is_not_an_eml_is_refused(tmp_path: Path) -> None:
    # Pointing at a .pdf should say so, not file an empty message.
    other = tmp_path / "statement.pdf"
    other.write_bytes(b"%PDF-1.4\n")

    with pytest.raises(ValueError, match="not a .eml file"):
        ingest_folder(src=other, target=_target(tmp_path), account_for=_account_for())


def test_a_path_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no such file or folder"):
        ingest_folder(
            src=tmp_path / "nope.eml",
            target=_target(tmp_path),
            account_for=_account_for(),
        )


# --- forced filing domain (--domain) --------------------------------------


def test_forced_domain_overrides_the_computed_counterparty(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)

    stats = ingest_folder(
        src=src,
        target=_target(tmp_path, filing_domain="vendor.com"),
        account_for=_account_for(),
    )
    assert stats["added"] == 1
    assert (tmp_path / ME / "archive" / "vendor.com").is_dir()
    assert not (tmp_path / ME / "archive" / "acme.com").exists()


def test_forced_domain_rescues_what_would_be_unfiled(tmp_path: Path) -> None:
    # Outbound to two distinct externals computes to no domain (_unfiled); the
    # forced domain gives the whole run one home instead.
    src = tmp_path / "src"
    _eml(src, "a", frm=ME, to="one@alpha.com, two@beta.com")

    stats = ingest_folder(
        src=src,
        target=_target(tmp_path, filing_domain="vendor.com"),
        account_for=_account_for(),
    )
    assert stats["unfiled"] == 0
    assert (tmp_path / ME / "archive" / "vendor.com").is_dir()


def test_forced_domain_yields_to_an_archived_thread_anchor(tmp_path: Path) -> None:
    # First file the root under its computed domain, then ingest a reply with a
    # forced domain: the reply must stay with its thread, not split to vendor.com.
    src = tmp_path / "src"
    _eml(src, "root", frm="them@acme.com", to=ME)
    ingest_folder(src=src, target=_target(tmp_path), account_for=_account_for())

    reply = src / "reply.eml"
    reply.write_text(
        "Message-ID: <reply@test>\n"
        "In-Reply-To: <root@test>\n"
        "From: them@acme.com\n"
        f"To: {ME}\n"
        "Date: Tue, 07 Jul 2026 09:00:00 +0000\n"
        "Subject: re\n\nbody\n",
        encoding="utf-8",
    )
    stats = ingest_folder(
        src=src,
        target=_target(tmp_path, filing_domain="vendor.com"),
        account_for=_account_for(),
    )
    assert stats["added"] == 1  # only the reply is new
    assert (tmp_path / ME / "archive" / "acme.com").is_dir()
    assert not (tmp_path / ME / "archive" / "vendor.com").exists()


# --- tags (--tags -> labels) ----------------------------------------------


def test_tags_are_recorded_as_labels(tmp_path: Path) -> None:

    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)

    ingest_folder(
        src=src,
        target=_target(tmp_path, labels=["receipts", "2026"]),
        account_for=_account_for(),
    )
    rows = list(load_store(tmp_path / ME / "manifest").values())
    assert len(rows) == 1
    assert rows[0]["labels"] == ["receipts", "2026"]


def test_no_tags_leaves_labels_null(tmp_path: Path) -> None:

    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)

    ingest_folder(src=src, target=_target(tmp_path), account_for=_account_for())
    rows = list(load_store(tmp_path / ME / "manifest").values())
    assert rows[0]["labels"] is None


# --- filed filenames grouped by directory ---------------------------------


def test_filed_lists_filenames_grouped_by_directory(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)
    _eml(src, "b", frm="them@acme.com", to=ME)

    stats = ingest_folder(src=src, target=_target(tmp_path), account_for=_account_for())
    filed: dict[str, list[str]] = stats["filed"]  # type: ignore[assignment]
    assert list(filed) == ["acme.com/2026-07"]  # one domain/month directory
    names = filed["acme.com/2026-07"]
    assert len(names) == 2
    assert all(n.endswith(".eml") for n in names)
    assert names == sorted(names)  # name-sorted, so a rerun renders the same


def test_dry_run_reports_filed_without_writing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)

    stats = ingest_folder(
        src=src, target=_target(tmp_path), account_for=_account_for(), dry_run=True
    )
    filed: dict[str, list[str]] = stats["filed"]  # type: ignore[assignment]
    assert list(filed) == ["acme.com/2026-07"]
    assert not (tmp_path / ME / "archive").exists()  # nothing actually written


def test_routed_filed_is_one_index_across_accounts(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "a", frm="them@acme.com", to=ME)
    _eml(src, "b", frm="them@globex.com", to=WORK)

    stats = _routed(tmp_path, to_trash=False)
    filed: dict[str, list[str]] = stats["filed"]  # type: ignore[assignment]
    # Both accounts' folders appear in the single top-level index.
    assert set(filed) == {"acme.com/2026-07", "globex.com/2026-07"}
    assert all(len(names) == 1 for names in filed.values())


# --- the summary the owner reads ------------------------------------------


def test_a_routed_re_run_aggregates_skipped_like_added(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _eml(src, "mine", frm="them@acme.com", to=ME)
    _eml(src, "work", frm="them@acme.com", to=WORK)

    assert _routed(tmp_path)["skipped"] == 0
    assert _routed(tmp_path)["skipped"] == 2  # both stores, second pass


def test_render_names_the_already_archived_share(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _render(
        {
            "files": 7,
            "unique": 7,
            "dup": 0,
            "unrouted": 0,
            "trashed": 7,
            "accounts": [
                {
                    "account": ME,
                    "added": 4,
                    "skipped": 3,
                    "unfiled": 0,
                    "added_by_year": {2026: 4},
                    "filed": {},
                }
            ],
        },
        Namespace(dry_run=False, no_delete=False),
    )
    out = capsys.readouterr().out
    # 7 read, 4 added, 7 trashed only adds up once the 3 are named.
    assert f"added 4 to {ME} (2026:4); 3 already archived" in out
