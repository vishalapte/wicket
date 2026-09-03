#!/usr/bin/env python3
"""Content-blindness guard: no tracked file's prose may embed a real figure.

The reusable, frontmatter-parameterized generalization of a confidential-data adopter's
``scripts/tests/test_check_content_blind.py`` (see
``docs-orchestrator/CONTENT_BLINDNESS.md`` for the one-home rule definition).
It implements the tier of that guard that GENERALIZES — the always-runs
STRUCTURAL rule that needs no private corpus:

    Formulae, worked examples and illustrations in PROSE must be written in
    VARIABLES, not numbers. A number that looks like a rate, price, notional,
    balance, threshold, share or capacity is a leak, even one the author
    believes they invented. Only content-blind structural constants (from the
    calendar or from arithmetic) may appear as literals.

The blocklist tier of a confidential-data adopter's guard (diff the private corpus against the
published tree) is inherently repo-specific — it needs the gitignored data —
and stays in the consuming repo. This checker is the tier every governed repo
can run identically, so it lives once in racecar.

TWO ARMS, ONE FILE, DIFFERENT DEFAULTS. The FIGURE arm above is opt-in. The
IDENTIFIER arm reads structured identifiers — a payment card, an IBAN, an SSN, a
GSTIN — and the checksum-anchored half of it runs unless the repo declines,
because its error rate is a property of a published format rather than of a
threshold someone picked. The argument for the split is at the IDENTIFIERS
section below; the short version is that a leak is wider than an amount, and the
wider part is the part that is cheap to detect.

Policy is read from the repo-root ``README.md`` YAML frontmatter, never
hardcoded here (CONTENT_BLINDNESS.md, "Declaration"):

    content_blind: true                    # opt IN to the FIGURE rule
                                           # absent  => figure rule off, anchored
                                           #            identifier rule on
                                           # false   => both off (declined)
    content_blind_exempt:                  # paths exempt from the prose rule
      - scripts/tests/test_check_content_blind.py
    content_blind_placeholders: [4111111111111111]  # documented synthetic VALUES
    content_blind_identifiers_off: [card]  # identifier TYPES this domain carries
    content_blind_structural: [7.0]        # extra structural constants (opt)

The FIGURE arm is off by default, and that is a deliberate per-repo opt-in: the
discipline over-fires on the legitimate figures many domains carry (dates, ports,
versions, documented constants), and a checker that cries wolf gets switched off.

Absence and ``false`` therefore mean different things to the IDENTIFIER arm.
Absent, the repo has declared nothing and the checksum-anchored types run. An
explicit ``content_blind: false`` is the owner answering, and racecar advises
rather than overrules (``shared/OWNERSHIP.md``), so both arms go quiet.

A key that is PRESENT but unreadable (``ture``) reads as ENABLED and reports the
bad value. The two mistakes are not symmetric: a mistyped ``true`` taken as false
hides exactly the leak this guard exists for, while a mistyped ``false`` taken as
true costs some findings and a correction (``architecture/R10-trust/README.md``).

Prose scanned:
  - Python: every comment and docstring line (never code — a test asserting
    ``approx(625.0)`` is doing its job; prose is where a formula gets
    "helpfully" illustrated with a real number).
  - Markdown: every line OUTSIDE a fenced code block (a fence is a config
    example, i.e. data).

Files scanned: what git would publish (tracked + new-and-not-ignored); on a
non-git tree, every text file under the root minus hidden dirs.

Output:
  - One line per finding: ``check_content_blind: <severity>: <message>``.
  - Summary: ``check_content_blind: OK`` (exit 0) or
    ``check_content_blind: N errors`` (exit 1).

Usage:
    python3 <path-to>/check_content_blind.py [--root <path>]

Complexity: O(enabled patterns x lines); content_blind_identifiers_off excludes a
disabled type's pattern from the scan itself (issue #69), not just from the result.
"""

# ONE FILE ON PURPOSE, past the module-length cap. This is a DELIVERED script: an
# adopter receives it as a single file and runs it with no racecar installed, so a
# split would either hand them two files to keep in step or duplicate the half both
# arms share -- the publish query, the prose extraction, the frontmatter policy, the
# delivered-file exemption and `scan` itself. The two arms are separate SUBJECTS and
# one MECHANISM, and the mechanism is what a file boundary would cut.

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from collections.abc import Callable, Iterator
from pathlib import Path

from check_docs import ignore_patterns
from check_packaging_rules._root import find_repo_root
from identifiers import BY_NAME
from identifiers import scan as identifier_scan

TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".cfg", ".ini", ".txt"}
)

# Content-blind STRUCTURAL constants: from the calendar or from arithmetic,
# carrying no information about any deal. Everything else that LOOKS like a deal
# term is out. Mirrors a confidential-data adopter's STRUCTURAL set; a repo may extend it via
# `content_blind_structural` in frontmatter.
STRUCTURAL = frozenset({0.0, 1.0, 2.0, 12.0, 100.0, 360.0, 365.0, 1000.0})

# THE THREE SHAPED PATTERNS. Each fires on how a human WROTE the number -- grouped by
# commas, grouped by underscores, carried to four decimal places -- and that writing is the
# evidence. Nobody groups a port or an id; a person groups a number because they meant it
# to be read as an amount. Shape survives quoting, so these are read everywhere.
COMMA_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")  # comma-grouped thousands
UNDERSCORE_GROUPED = re.compile(
    r"\b\d+_\d{3}(?:_\d{3})*\b"
)  # underscore-grouped thousands
PRECISE_DECIMAL = re.compile(r"(?<![\d.])\d*\.\d{4,}(?![\d])")  # a many-place decimal

# THE UNSHAPED ARM, and the one that has to be handled carefully, because it fires on a run
# of digits and nothing else. Every exemption below hangs off it -- a year, a YYYYMM key, a
# YYYYMMDD key are all runs of digits that are not quantities -- and each arrived as its own
# patch. Left unbounded it is a rule against writing numbers, and this file's own docstring
# admits as much ("over-fires on the legitimate figures many domains carry"). The answer to
# that was to make the whole guard opt-in, which is not the same thing as making it right: a
# checker that cries wolf gets switched off, and then the leak it existed for ships.
#
# BOUNDED ABOVE. Past nine digits an unseparated run is not something a person wrote to be
# read as an amount; it is a token -- a hash, an id, an epoch (ten digits in seconds,
# thirteen in milliseconds), an account or invoice number. racecar's own `repo_id` is twelve
# hex characters, so about one repo in a thousand hashes to twelve digits, and this guard
# would then refuse every document naming it, permanently, for a reason connected to no
# deal at all.
#
# WHAT THAT GIVES UP, stated because a guard that quietly stops guarding is worse than one
# that was never there: an unseparated integer of ten digits or more is no longer read as a
# figure, so `12000000000` written bare in prose now passes. Grouped and decimal forms are
# still caught at any magnitude, so the whole of the loss is "a very large number written
# without separators" -- which is also the form nobody uses when they mean an amount.
LARGE_INTEGER = re.compile(r"(?<![\d.\w$§])\d{4,9}(?:\.\d+)?(?![\d\w])")

# An inline code span. Markdown's own way of saying "this is a literal, not narration", and
# the guard already honours the block form of exactly that (fenced code is not prose). In
# Python it honours the language's boundary too, reading only comments and docstrings. This
# is the markdown half catching up: a port, a size, a flag default and an id are written in
# backticks, and a fee is not.
CODE_SPAN = re.compile(r"`[^`\n]*`")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def read_frontmatter_block(text: str) -> str | None:
    """Return the raw YAML frontmatter block of a doc, or None if absent."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    return m.group(1) if m else None


_BOOL_TRUE = frozenset({"true", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "no", "off"})


def parse_policy(frontmatter: str) -> dict[str, object]:
    """Parse the flat content-blind keys from a frontmatter block, stdlib-only.

    Handles exactly the shapes CONTENT_BLINDNESS.md declares: a boolean scalar
    (`content_blind: true`), an inline list (`key: [a, b]`), and a block list
    (`key:` then `  - item` lines). No general YAML dependency — this checker
    stays stdlib-only like its doc-coherence peers.
    """
    policy: dict[str, object] = {}
    keys = {
        "content_blind",
        "content_blind_exempt",
        "content_blind_placeholders",
        "content_blind_structural",
        "content_blind_identifiers_off",
    }
    lines = frontmatter.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m or m.group(1) not in keys:
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        if key == "content_blind":
            # PRESENT AND NOT EXPLICITLY FALSE MEANS TRUE. The old rule was
            # anything-but-true means false, so `content_blind: ture` disabled the
            # whole scan and exited 0 — fail-open on the key whose entire job is
            # closed-by-default. The two ways to be wrong are not symmetric: a
            # mistyped `true` read as false hides a leak, a mistyped `false` read as
            # true costs the author some findings and a correction. TRUST.md decides
            # it — the behaviour when a check cannot run IS the policy.
            #
            # Absent still means off. Opt-in is deliberate and argued above; this
            # governs only a key the author took the trouble to write.
            # Strip a trailing comment first. The old rule tolerated `false  # why`
            # by accident — anything that was not `true` was false — so inverting the
            # rule without this read racecar's own README as unreadable and turned the
            # scan on across the repo.
            literal = re.split(r"\s+#", rest, maxsplit=1)[0].strip().lower()
            if literal not in _BOOL_TRUE | _BOOL_FALSE:
                policy["content_blind_malformed"] = rest
            policy[key] = literal not in _BOOL_FALSE
        elif rest.startswith("["):
            policy[key] = _parse_inline_list(rest)
        elif not rest:
            items, i = _consume_block_list(lines, i + 1)
            policy[key] = items
            continue
        else:
            policy[key] = [rest]
        i += 1
    return policy


def _parse_inline_list(rest: str) -> list[str]:
    """Parse a `[a, b, c]` inline YAML list into a list of stripped strings."""
    inner = rest.strip().lstrip("[").rstrip("]")
    return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]


def _consume_block_list(lines: list[str], start: int) -> tuple[list[str], int]:
    """Consume `  - item` lines starting at `start`; return (items, next_index)."""
    items: list[str] = []
    i = start
    while i < len(lines):
        item = re.match(r"^\s*-\s+(.*)$", lines[i])
        if not item:
            break
        items.append(item.group(1).strip().strip("'\""))
        i += 1
    return items, i


def _declared_out_of_scope(root: Path) -> Callable[[str], bool]:
    """A predicate over repo-relative paths, from the repo's own `ignore-paths`.

    Reads the declaration through `check_docs.ignore_patterns()` rather than growing a
    third reader of the same key -- that function exists to be the one home, and
    `check_file_placement` already imports it for exactly this.

    This is narrower than `content_blind_exempt`, which stays what it was: a path the repo
    DOES grade but content-blindness should not. A path the repo has already declared out
    of scope for every other checker should not have to be named twice, in two
    vocabularies, to mean the same thing.
    """
    patterns = ignore_patterns(root)
    return lambda rel: any(p.search(rel) for p in patterns)


def published_files(root: Path) -> list[Path]:
    """Every text file git would publish; fall back to an rglob on a non-git tree."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return _rglob_text(root)
    out_of_scope = _declared_out_of_scope(root)
    files = []
    for name in out.splitlines():
        if not name or out_of_scope(name):
            continue
        path = root / name
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
    return files


def _rglob_text(root: Path) -> list[Path]:
    """Every text file under `root`, skipping hidden directories."""
    out_of_scope = _declared_out_of_scope(root)
    files = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if out_of_scope(rel.as_posix()):
            continue
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
    return files


# ---------------------------------------------------------------------------
# Prose extraction
# ---------------------------------------------------------------------------


def py_prose(path: Path) -> Iterator[tuple[int, str]]:
    """Yield (lineno, text) for every comment and docstring line in a python file."""
    source = path.read_text(encoding="utf-8", errors="ignore")
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                yield token.start[0], token.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc and node.body:
                start = node.body[0].lineno
                for offset, line in enumerate(doc.splitlines()):
                    yield start + offset, line


def md_prose(path: Path) -> Iterator[tuple[int, str]]:
    """Yield (lineno, text) for every markdown line OUTSIDE a fenced code block."""
    fenced = False
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
    ):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            yield lineno, line


def unquoted(line: str) -> str:
    """The line with inline code spans blanked out, positions preserved.

    Applied to the UNSHAPED arm only, and the asymmetry is the whole argument. A grouped or
    many-place figure carries its own evidence and is a deal term wherever it sits, quoted
    or not -- nobody writes a port as `1,234`. A bare run of digits carries no evidence at
    all, so where the author put it is the only signal available, and markdown already has
    a way of saying "literal, not narration". Reading it is not an exemption for
    convenience; refusing to read it is treating a marked-up document as a flat stream of
    characters.

    What it gives up: a bare figure someone quoted, `37500`, is no longer read. That is a
    real hole and it is narrow -- an author narrating a fee does not put it in backticks,
    and one who does has quoted it as a literal, which is what the markup means.
    """
    return CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line)


def deal_figures_in(line: str, structural: frozenset[float]) -> list[str]:
    """Return the literals in `line` that look like a deal term, not a constant."""
    found = []
    for pattern in (COMMA_GROUPED, UNDERSCORE_GROUPED, PRECISE_DECIMAL, LARGE_INTEGER):
        subject = unquoted(line) if pattern is LARGE_INTEGER else line
        for match in pattern.finditer(subject):
            raw = match.group(0)
            try:
                value = float(raw.replace(",", "").replace("_", ""))
            except ValueError:
                continue
            if value in structural:
                continue
            # A year or an ISO month key: 1899 (Excel's serial epoch) to 2100.
            if value.is_integer() and 1899 <= value <= 2100:
                continue
            # A compact date key — YYYYMM (paper ids, ISO month keys) or YYYYMMDD —
            # is a calendar value, not a deal term, by the same reasoning as the year
            # exemption above. These three cover the 4-to-9-digit band, which is the only
            # band the unshaped arm now reads at all.
            #
            # Domain magic constants (ports, byte sizes, TTLs) are not guessable here, and
            # `content_blind_structural` is where a repo may still list one. It should be
            # needed far less often now: those are written in backticks in every codebase
            # racecar has seen, and `unquoted` reads them as the literals they are rather
            # than sending the author to maintain an allowlist of their own port numbers.
            if value.is_integer():
                iv = int(value)
                is_yyyymm = 189901 <= iv <= 210012 and 1 <= iv % 100 <= 12
                is_yyyymmdd = (
                    18990101 <= iv <= 21001231
                    and 1 <= (iv // 100) % 100 <= 12
                    and 1 <= iv % 100 <= 31
                )
                if is_yyyymm or is_yyyymmdd:
                    continue
            # A residual or a tolerance (1e-3 and below) is a measure of error.
            if abs(value) <= 1e-3:
                continue
            found.append(raw)
    return found


# ---------------------------------------------------------------------------
# THE SECOND ARM: structured identifiers
#
# The figure arm above reads a number and guesses whether a human meant it as an amount.
# That guess is unavoidable -- ANY number could be an amount -- and its imprecision is what
# made this whole guard opt-in. The imprecision is not evenly distributed, though, and
# bundling both arms behind one switch meant the precise half only ever ran in the repos
# that were already careful.
#
# A national identity number, a taxpayer id, a bank routing number, an account number, a
# payment card, a company registration number: each is a disclosure with consequences an
# amount does not have, and none of them is a rate, a price, a notional or a balance.
#
# THE RULES THEMSELVES LIVE IN DATA, not here. `scripts/identifiers.json` declares every
# type -- its shape, the algorithm that confirms it, the issuing authority whose rule it is
# and where that rule is published -- and `scripts/identifiers.py` implements the algorithms
# that file names. This module is a CONSUMER. Adding a jurisdiction is a row in that file,
# not a patch to this one, and every row can be audited against its own authority without
# reading any code.
#
# TWO TIERS, AND THE LINE IS EVIDENCE, NOT SEVERITY. An `anchored` type carries a structural
# signal besides the checksum -- a letter alphabet, a required separator, a closed
# enumeration -- so a false positive needs the structure AND the check to coincide. Those
# run whether or not a repo opted in. A `bare` type has no such signal: a naked run of
# digits, or a body so unrestricted that an ordinary token satisfies everything but the
# check digit. What is left is arithmetic a coincidence clears about one time in ten, which
# is a threshold rather than evidence, so those wait for `content_blind: true`. Neither
# claim is taken on trust -- identifiers.json records both measured rates per row, and
# scripts/tests/test_identifiers.py refuses a table whose two tiers overlap.
#
# NOT IMPLEMENTED, AND SAID OUT LOUD: DUNS, bare SSN, bare EIN, Indian MICR, UK UTR and
# sort code. The reasons are recorded per type under `excluded` in identifiers.json, and
# they are all the same reason: a shape with no check and no enumeration accepts every
# string of that shape, which is a rule against writing numbers rather than a detector.
# ---------------------------------------------------------------------------


def identifiers_in(
    line: str,
    anchored_only: bool,
    disabled: frozenset[str],
    placeholders: frozenset[str],
) -> list[tuple[str, str]]:
    """Return (type, value) for every structured identifier the line carries.

    Read everywhere, including inside backticks, and the asymmetry with the bare-figure
    arm is the same one that arm already makes: a value that satisfies a published
    checksum carries its own evidence and is a disclosure wherever it sits. `unquoted`
    exists because a naked run of digits has no evidence and its POSITION is the only
    signal; that reasoning does not reach a value which has proved what it is.
    """
    found: list[tuple[str, str]] = []
    for name, raw in identifier_scan(
        line, anchored_only=anchored_only, disabled=disabled
    ):
        if re.sub(r"[ .\-/]", "", raw) in placeholders:
            continue
        found.append((name, raw))
    return found


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class Findings:
    """Accumulator for severity-tagged findings (errors and info notes)."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def error(self, msg: str) -> None:
        """Record an error-severity finding."""
        self.entries.append(("error", msg))

    def info(self, msg: str) -> None:
        """Record an info-severity note."""
        self.entries.append(("info", msg))

    @property
    def error_count(self) -> int:
        """Number of error-severity findings recorded."""
        return sum(1 for sev, _ in self.entries if sev == "error")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# Where a repo records the files racecar put in it. Two kinds of file, because the answer
# differs by which repo this is running in and how recently it was synced.
#
# Path lists — one repo-relative path per line, `#` comments allowed:
#   - `scripts/.racecar-delivered.txt` — what a sync writes today.
#   - `scripts/racecar-manifest.txt` — in an ADOPTER, the same record under its old name.
#     (In racecar itself that name is retired; the canonical manifest is the JSONL below.)
_DELIVERED_RECORDS = (
    "scripts/.racecar-delivered.txt",
    "scripts/racecar-manifest.txt",
)

# The canonical delivery manifest, present in RACECAR ITSELF: one JSON object per line,
# whose `source` is the file in this repo. A separate constant from the path lists because
# it is a separate format, read by key — not a shape the line-splitting rule can absorb.
_DELIVERY_MANIFEST = "scripts/racecar-manifest.jsonl"


def delivered_received(root: Path) -> frozenset[str]:
    """Repo-relative paths a SYNC WROTE INTO this repo -- copies, never sources.

    The narrower half of `delivered_exempt` below, and separate because two consumers ask
    two different questions of one record. The content-blind guard asks *did racecar write
    this prose*, which is as true of a file racecar SHIPS as of a copy it delivered, so it
    wants both halves. `make fmt` asks *would the next sync overwrite my edit*, which is
    true only of the copies: in racecar itself every `source` in the canonical manifest is
    a file racecar AUTHORS, so answering the formatter with the wider set would stop
    racecar formatting its own `scripts/` while `fmt-check` stayed green.

    One parser, two answers. The docstring below records what a second reader of these
    records cost the last time there was one.
    """
    paths: set[str] = set()
    for rel in _DELIVERED_RECORDS:
        record = root / rel
        if not record.is_file():
            continue
        try:
            text = record.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(
                f"check_content_blind: {rel} is not valid UTF-8 ({exc}); skipping it",
                file=sys.stderr,
            )
            continue
        paths |= {
            line.split()[0]
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    return frozenset(paths)


def filter_delivered(root: Path, raw: bytes, *, keep_delivered: bool = False) -> bytes:
    """NUL-separated paths in; either the delivered ones or everything else, out.

    Lives beside the record's parser because that is the one home for its format; a
    filter that re-read the record in Make would be the second reader this file's history
    argues against. NUL in and NUL out for the same reason the file list uses `-z` and
    `xargs -0`: a path with a space in it must survive the round trip whole.

    Both directions, because the gates need both populations and neither may be derived by
    re-reading the record somewhere else. `make fmt` takes the complement (never rewrite a
    file the next sync overwrites); the checking gates take the delivered set and grade it
    under canon's own configuration.
    """
    received = delivered_received(root)
    kept = [
        path
        for path in raw.split(b"\0")
        if path
        and (path.decode("utf-8", "surrogateescape") in received) is keep_delivered
    ]
    return b"\0".join(kept) + b"\0" if kept else b""


def delivered_ignore_regex(root: Path) -> str:
    """An anchored alternation of the delivered paths, for `--ignore-paths` and mypy.

    pylint and mypy take a scope as DIRECTORIES and subtract by regex, where the formatters
    take an explicit file list. Same fact, the shape each tool can consume -- and computed
    here rather than assembled in Make, so there is still exactly one thing that knows how a
    delivery record is read. Empty when nothing was delivered, which every caller must treat
    as "add no exclusion" rather than as an empty pattern matching everything.
    """
    paths = sorted(delivered_received(root))
    return "|".join("^" + re.escape(p) + "$" for p in paths)


def delivered_exempt(root: Path) -> frozenset[str]:
    """Repo-relative paths racecar delivered here, read from the delivery records.

    racecar-delivered files (the synced check scripts, `racecar.mk`) are tooling the
    repo owns no prose in and cannot edit without the next sync clobbering it, so the
    guard never scans them — a stray figure-shaped comment in canon must never turn a
    downstream repo's gate red. racecar does not edit the repo's owned README to record
    this (that would break the no-clobber contract); instead `sync_scripts.py` writes
    the record of what it delivered, and the guard exempts exactly that set. The
    record is rewritten on every sync, so the exemption is always current.

    Two formats, each read as what it is. A path list gives its first whitespace-separated
    token; the canonical manifest gives its `source` field. Reading both by one rule is
    what failed before: the guard took the whole LINE, which held for as long as every
    record was nothing but paths, and the day the canonical manifest grew a destination
    and a digest every exempt path became `"<path> <dest> <digest>"`, matched no file, and
    racecar's own self-exemption died without a symptom. `.get` on the manifest keeps a
    field added later from doing it again.
    """
    paths: set[str] = set(delivered_received(root))
    manifest = root / _DELIVERY_MANIFEST
    if manifest.is_file():
        try:
            manifest_text = manifest.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(
                f"check_content_blind: {_DELIVERY_MANIFEST} is not valid UTF-8 "
                f"({exc}); skipping it",
                file=sys.stderr,
            )
            manifest_text = ""
        for line in manifest_text.splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            source = obj.get("source") if isinstance(obj, dict) else None
            if isinstance(source, str) and source:
                paths.add(source)
    return frozenset(paths)


def load_policy(root: Path) -> dict[str, object]:
    """Read the content-blind policy from the repo-root README.md frontmatter."""
    readme = root / "README.md"
    if not readme.is_file():
        return {}
    block = read_frontmatter_block(readme.read_text(encoding="utf-8"))
    return parse_policy(block) if block else {}


def structural_set(policy: dict[str, object]) -> frozenset[float]:
    """Return STRUCTURAL extended by any `content_blind_structural` frontmatter."""
    extra = policy.get("content_blind_structural", [])
    values: set[float] = set(STRUCTURAL)
    if isinstance(extra, list):
        for item in extra:
            try:
                values.add(float(item))
            except (TypeError, ValueError):
                continue
    return frozenset(values)


def scan(
    root: Path,
    exempt: frozenset[str],
    structural: frozenset[float] | None,
    identifiers: tuple[bool, frozenset[str], frozenset[str]] | None = None,
) -> list[str]:
    """Return one `rel:lineno: value | line` line per finding, over both arms.

    `structural` is None when the figure arm is off (the repo did not opt in) and
    `identifiers` is None when the identifier arm is off (the repo opted OUT). The two
    are independent because their evidence is: see the IDENTIFIERS section above.
    """
    offenders: list[str] = []
    # This guard necessarily quotes the very shapes it forbids to explain them,
    # so it always exempts its own file (a confidential-data adopter's PROSE_EXEMPT_FILES pattern) —
    # otherwise it would flag itself once synced into a content-blind adopter.
    self_path = Path(__file__).resolve()
    for path in published_files(root):
        if path.resolve() == self_path:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in exempt:
            continue
        if path.suffix == ".py":
            prose = py_prose(path)
        elif path.suffix == ".md":
            prose = md_prose(path)
        else:
            continue
        for lineno, line in prose:
            if structural is not None:
                for figure in deal_figures_in(line, structural):
                    offenders.append(
                        f"deal-shaped figure in prose — {rel}:{lineno}: {figure}"
                        f"  |  {line.strip()[:80]}"
                    )
            if identifiers is not None:
                for name, value in identifiers_in(line, *identifiers):
                    offenders.append(
                        f"structured identifier in prose — {rel}:{lineno}: {value} "
                        f"reads as {_note_for(name)}  |  {line.strip()[:80]}"
                    )
    return offenders


def _note_for(name: str) -> str:
    """The human sentence for an identifier type; the type id alone helps nobody."""
    return BY_NAME[name].note


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the content-blind check."""
    parser = argparse.ArgumentParser(
        description="Assert no tracked file's prose embeds a real figure "
        "(content-blindness Tier 2)."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root to scan. Default: discovered via .git walk-up from CWD.",
    )
    parser.add_argument(
        "--exclude-delivered",
        action="store_true",
        help="Filter mode: read NUL-separated paths on stdin and write through those no "
        "sync delivered here. racecar.mk pipes its file list through it so no formatter "
        "is ever handed a file the next sync overwrites.",
    )
    parser.add_argument(
        "--only-delivered",
        action="store_true",
        help="The complement of --exclude-delivered: write through only what a sync "
        "delivered here, which the checking gates grade under canon's own config.",
    )
    parser.add_argument(
        "--delivered-regex",
        action="store_true",
        help="Print an anchored alternation of the delivered paths, for pylint's "
        "--ignore-paths and mypy's exclude. Empty output means nothing was delivered.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the content-blind prose scan when opted in; return an exit code."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    root = args.root.resolve() if args.root else find_repo_root()

    # Before the policy read, because this verb answers from the delivery record alone and
    # must work in a repo that has opted out of the scan -- the formatter needs the answer
    # either way.
    if args.delivered_regex:
        print(delivered_ignore_regex(root))
        return 0
    if args.exclude_delivered or args.only_delivered:
        sys.stdout.buffer.write(
            filter_delivered(
                root, sys.stdin.buffer.read(), keep_delivered=args.only_delivered
            )
        )
        sys.stdout.buffer.flush()
        return 0

    f = Findings()

    policy = load_policy(root)
    malformed = policy.get("content_blind_malformed")
    if malformed is not None:
        # Reported AND enabled. Erroring and returning would have been the same
        # fail-open in a louder voice: the author learns the value is wrong, and the
        # scan they asked for still does not run while they get around to it.
        f.error(
            f"README.md frontmatter: content_blind is {malformed!r}, which is neither "
            f"true nor false. Read as ENABLED, because a value nobody can read must "
            f"not silently disable the guard. Write `true` or `false` "
            f"(CONTENT_BLINDNESS.md)."
        )
    exempt_raw = policy.get("content_blind_exempt", [])
    exempt = frozenset(exempt_raw if isinstance(exempt_raw, list) else [])
    exempt = exempt | delivered_exempt(root)

    opted_in = bool(policy.get("content_blind"))
    declined = "content_blind" in policy and not opted_in

    # THE IDENTIFIER ARM'S DEFAULT, and the one asymmetry in it. Absent means the repo
    # has declared nothing, so the anchored types run: their error rate is a property of
    # the format, and a guard that only runs where someone already opted in never
    # protects the repo that needed it. An explicit `content_blind: false` is not
    # silence — it is the owner declining, and racecar advises rather than overrules
    # (shared/OWNERSHIP.md), so that answer is honoured for both arms.
    identifiers = (
        None
        if declined
        else (not opted_in, disabled_identifiers(policy), placeholder_set(policy))
    )

    if not opted_in:
        f.info(
            "content_blind not enabled in README.md frontmatter; the figure rule is off"
            + (
                " and so is the identifier rule (declined explicitly)"
                if declined
                else "; the checksum-anchored identifier rule still runs"
            )
            + " (see CONTENT_BLINDNESS.md)"
        )

    offenders = scan(
        root, exempt, structural_set(policy) if opted_in else None, identifiers
    )
    for offender in offenders:
        f.error(offender)
    return emit(f)


def disabled_identifiers(policy: dict[str, object]) -> frozenset[str]:
    """Identifier type ids this repo has turned off, from `content_blind_identifiers_off`.

    Per TYPE rather than per repo, because a domain legitimately carrying one carries one:
    a payments library will quote test cards forever and has no reason to stop reading
    IBANs. `content_blind_placeholders` is the other half — a documented synthetic VALUE,
    for the case where the exception is one string rather than a whole class.
    """
    raw = policy.get("content_blind_identifiers_off", [])
    return (
        frozenset(str(x).strip().lower() for x in raw)
        if isinstance(raw, list)
        else frozenset()
    )


def placeholder_set(policy: dict[str, object]) -> frozenset[str]:
    """Documented synthetic values, separators stripped, from `content_blind_placeholders`.

    Previously parsed and never read — the key was declared in CONTENT_BLINDNESS.md and
    consulted by nothing, which is config that reads as a control and is not one. The
    identifier arm is what it was waiting for: a checksum-valid value a repo has argued
    for in writing is exactly the exception this arm cannot infer.
    """
    raw = policy.get("content_blind_placeholders", [])
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(re.sub(r"[ .\-/]", "", str(x)) for x in raw)


def emit(f: Findings) -> int:
    """Print all findings and return 1 if any error was recorded, else 0."""
    for severity, msg in f.entries:
        print(f"check_content_blind: {severity}: {msg}")
    if not f.error_count:
        print("check_content_blind: OK")
        return 0
    print(
        f"check_content_blind: {f.error_count} errors. A formula or worked example in "
        "prose must be written in VARIABLES, not numbers; a value that satisfies a "
        "published checksum belongs in no tracked prose at all (CONTENT_BLINDNESS.md)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
