#!/usr/bin/env python3
"""Content-blindness guard: no tracked file's prose may embed a real figure.

The rule is defined in ``docs-orchestrator/CONTENT_BLINDNESS.md``. This checker is the
structural tier, which needs no private corpus: formulae and worked examples in prose
are written in variables, not numbers, and only structural constants (calendar,
arithmetic) may appear as literals. The blocklist tier needs the private corpus and
stays in the adopter repo.

Two arms:
  - FIGURE: reads amount-shaped numbers. Opt-in.
  - IDENTIFIER: reads structured identifiers (payment card, IBAN, SSN, GSTIN). Its
    checksum-anchored types run unless the repo declines.

Policy is read from the repo-root ``README.md`` YAML frontmatter:

    content_blind: true                    # opt IN to the FIGURE rule
                                           # absent  => figure rule off, anchored
                                           #            identifier rule on
                                           # false   => both off (declined)
    content_blind_exempt:                  # paths exempt from the prose rule
      - scripts/tests/macro/test_check_content_blind.py
    content_blind_placeholders: [4111111111111111]  # documented synthetic VALUES
    content_blind_identifiers_off: [card]  # identifier TYPES this domain carries
    content_blind_known_names:             # the repo's OWN name series, as regexes
      - 'ADR[0-9]+'
    content_blind_structural: [7.0]        # extra structural constants (opt)

A key that is present but unreadable (``ture``) reads as enabled and is reported.

Prose scanned:
  - Python: every comment and docstring line, never code.
  - Markdown: every line outside a fenced code block.

Files scanned: what git would publish (tracked + new-and-not-ignored); on a
non-git tree, every text file under the root minus hidden dirs.

Output:
  - One line per finding: ``check_content_blind: <severity>: <message>``.
  - Summary: ``check_content_blind: OK`` (exit 0) or
    ``check_content_blind: N errors`` (exit 1).

Usage:
    python3 <path-to>/check_content_blind.py [--root <path>]

Complexity: O(enabled patterns x lines); content_blind_identifiers_off excludes a
disabled type's pattern from the scan itself, not just from the result.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from collections.abc import Callable, Iterator
from pathlib import Path

from check_docs import ignore_patterns
from check_red import is_test
from identifiers import BY_NAME
from identifiers import scan as identifier_scan
from lib.shared import _frontmatter, _markdown
from lib.shared._constants import DELIVERED_RECORDS
from lib.shared._files import git_ls, repo_files
from lib.shared._report import Findings, emit
from lib.shared._root import find_repo_root

TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".cfg", ".ini", ".txt"}
)

# Structural constants: from the calendar or from arithmetic, carrying no deal
# information. A repo may extend the set via `content_blind_structural` in frontmatter.
STRUCTURAL = frozenset({0.0, 1.0, 2.0, 12.0, 100.0, 360.0, 365.0, 1000.0})

# Shaped patterns: each fires on how a person wrote the number (comma-grouped,
# underscore-grouped, four or more decimal places).
COMMA_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")  # comma-grouped thousands
UNDERSCORE_GROUPED = re.compile(
    r"\b\d+_\d{3}(?:_\d{3})*\b"
)  # underscore-grouped thousands
PRECISE_DECIMAL = re.compile(r"(?<![\d.])\d*\.\d{4,}(?![\d])")  # a many-place decimal

# Unshaped pattern: a bare run of 4 to 9 digits. Past nine digits a run is a token
# (hash, id, epoch), so `12000000000` written bare passes; grouped and decimal forms are
# caught at any magnitude.
LARGE_INTEGER = re.compile(r"(?<![\d.\w$§])\d{4,9}(?:\.\d+)?(?![\d\w])")

# A currency symbol or code marks an amount on its own, and no exemption applies to it:
# `$2,028` is money even though 2028 is a year.
CURRENCY_MARKED = re.compile(
    r"[$£€¥₹]\s?\d[\d,_]*(?:\.\d+)?\s*(?:bn|mm|k|m|b)?"
    r"|\d[\d,_]*(?:\.\d+)?\s?(?:USD|GBP|EUR|JPY|INR|CHF|CAD|AUD)\b",
    re.IGNORECASE,
)

# A bare magnitude suffix (`53k paths`) is often a count, so it is read only in a money
# sentence.
SUFFIX_FIGURE = re.compile(r"\d[\d,_]*(?:\.\d+)?\s*(?:bn|mm|k|m)\b", re.IGNORECASE)

# Money context: an unmarked figure is read only where the sentence says money (`fee`,
# `notional`, `carry`, `per month`).
MONEY_CONTEXT = re.compile(
    r"\b(?:fees?|notional|balances?|amounts?|principal|interest|yields?"
    r"|pric(?:e|ed|ing)|costs?|revenue|salar(?:y|ies)|valuation"
    r"|royalt(?:y|ies)|commitments?|facilit(?:y|ies)|carry|premiums?|spreads?|margins?"
    r"|discounts?|invoices?|budgets?|fundings?|payments?|rates?|cap|floor|arr|mrr"
    r"|usd|gbp|eur)\b"
    r"|\bper\s+(?:month|year|annum|unit)\b"
    r"|/mo\b|[$£€¥₹]",
    re.IGNORECASE,
)

# A percentage is read only in a money sentence: `20% carry`, not `improved by 15%`.
PERCENT_FIGURE = re.compile(r"\d[\d,_]*(?:\.\d+)?\s?%")

# A money-marked literal that states no quantity is a unit or a format (see
# `unit_or_format`): every digit zero (`$000s`, `$0.00`), or an ascending counting run
# carried by a separator (`$1,234.00`). A round amount (`$1,000`) still fires.
DIGIT_RUN = "123456789"

# A magnitude suffix states size, so `$1,234k` is an amount however its digits fall.
MAGNITUDE_SUFFIX = re.compile(r"(?:bn|mm|[kmb])\s*$", re.IGNORECASE)

# An inline code span is a literal, not narration; figures inside it are not read.
CODE_SPAN = re.compile(r"`[^`\n]*`")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
_BOOL_TRUE = frozenset({"true", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "no", "off"})


def parse_policy(frontmatter: str) -> dict[str, object]:
    """Parse the flat content-blind keys from a frontmatter block, stdlib-only.

    Handles a boolean scalar (`content_blind: true`), an inline list (`key: [a, b]`),
    and a block list (`key:` then `  - item` lines).
    """
    policy: dict[str, object] = {}
    keys = {
        "content_blind",
        "content_blind_exempt",
        "content_blind_placeholders",
        "content_blind_structural",
        "content_blind_identifiers_off",
        "content_blind_known_names",
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
            # Present and not explicitly false means true (R-10): a typo such as `ture`
            # must not disable the scan. Strip a trailing comment first so
            # `false  # why` reads as false.
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

    Read through `check_docs.ignore_patterns()`. A path declared out of scope for every
    checker is out of scope here too; `content_blind_exempt` names a path only this
    guard skips.
    """
    patterns = ignore_patterns(root)
    return lambda rel: any(p.search(rel) for p in patterns)


def published_files(root: Path) -> list[Path]:
    """Every text file git would publish; fall back to an rglob on a non-git tree."""
    out = git_ls(root, "--cached", "--others", "--exclude-standard")
    if out is None:
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
    """Every text file under `root`, from the shared walk; the no-git fallback.

    The walk keeps hidden files and prunes only hidden directories.
    """
    out_of_scope = _declared_out_of_scope(root)
    return [
        path
        for path in repo_files(
            root, *(f"*{suffix}" for suffix in sorted(TEXT_SUFFIXES))
        )
        if path.is_file() and not out_of_scope(path.relative_to(root).as_posix())
    ]


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
    """Yield (lineno, text) for every text line of a markdown file: not frontmatter, not code."""
    doc = _markdown.parse(path.read_text(encoding="utf-8", errors="ignore"))
    yield from ((line.no, line.text) for line in doc.text())


def unquoted(line: str) -> str:
    """The line with inline code spans blanked out, positions preserved.

    Applied to every figure arm, so a figure written in backticks (`37500`) is not read.
    """
    return CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line)


def unit_or_format(raw: str) -> bool:
    """True when a money-marked literal states a unit or a format rather than an amount.

    Every digit zero: `$000s`, `$0.00`. A counting run carried by a separator:
    `$1,234.00`, `$12,345.67`. Everything else is an amount, including a round one.
    """
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return False
    if set(digits) == {"0"}:
        return True
    if MAGNITUDE_SUFFIX.search(raw) or not any(sep in raw for sep in (",", "_")):
        return False
    whole, _, frac = re.sub(r"[^\d.]", "", raw).partition(".")
    if len(whole) < 3 or whole != DIGIT_RUN[: len(whole)]:
        return False
    return set(frac) <= {"0"} or frac == DIGIT_RUN[len(whole) : len(whole) + len(frac)]


def deal_figures_in(line: str, structural: frozenset[float]) -> list[str]:
    """Return the literals in `line` that look like a deal term, not a constant.

    1. A currency symbol or code on the figure is read always; the exemptions below do
       not apply to it.
    2. Suffix, percent and digit-shape patterns are read only where the sentence says
       money (`MONEY_CONTEXT`).

    Code spans are blanked in both, and `unit_or_format` literals are dropped.
    """
    found: list[str] = []
    subject = unquoted(line)

    def take(pattern: re.Pattern[str]) -> None:
        for match in pattern.finditer(subject):
            raw = match.group(0).strip()
            if raw and not unit_or_format(raw) and raw not in found:
                found.append(raw)

    take(CURRENCY_MARKED)
    if not MONEY_CONTEXT.search(line):
        return found
    take(SUFFIX_FIGURE)
    take(PERCENT_FIGURE)
    for pattern in (COMMA_GROUPED, UNDERSCORE_GROUPED, PRECISE_DECIMAL, LARGE_INTEGER):
        subject = unquoted(line)
        for match in pattern.finditer(subject):
            raw = match.group(0)
            try:
                value = float(raw.replace(",", "").replace("_", ""))
            except ValueError:
                continue
            if value in structural or unit_or_format(raw):
                continue
            # A year or an ISO month key: 1899 (Excel's serial epoch) to 2100.
            if value.is_integer() and 1899 <= value <= 2100:
                continue
            # A compact date key, YYYYMM or YYYYMMDD, is a calendar value. A domain
            # constant (port, byte size) belongs in backticks or in
            # `content_blind_structural`.
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
            if raw not in found:
                found.append(raw)
    return found


# ---------------------------------------------------------------------------
# Structured identifiers
#
# The rules live in data: `scripts/identifiers.json` declares each type (shape, check
# algorithm, issuing authority) and `scripts/identifiers.py` implements the algorithms.
# Adding a jurisdiction is a row in that file.
#
# An `anchored` type has a structural signal besides the checksum and runs unless the
# repo declines. A `bare` type has only the checksum and runs only with
# `content_blind: true`. Excluded types and their reasons are under `excluded` in
# identifiers.json.
# ---------------------------------------------------------------------------


def identifiers_in(
    line: str,
    anchored_only: bool,
    disabled: frozenset[str],
    placeholders: frozenset[str],
    known: tuple[re.Pattern[str], ...] = (),
) -> list[tuple[str, str]]:
    """Return (type, value) for every structured identifier the line carries.

    Read everywhere, including inside backticks: a checksum-valid value is a disclosure
    wherever it sits. Tokens matching a `known` regex are skipped first.
    """
    found: list[tuple[str, str]] = []
    for name, raw in identifier_scan(
        _not_known_names(line, known), anchored_only=anchored_only, disabled=disabled
    ):
        if re.sub(r"[ .\-/]", "", raw) in placeholders:
            continue
        found.append((name, raw))
    return found


def _not_known_names(line: str, known: tuple[re.Pattern[str], ...]) -> str:
    """`line` with each token a known name matches blanked: `(ADR12),` is token `ADR12`.

    Every other token stays in its place, so a value written across spaces is read whole.
    """

    def keep(token: re.Match[str]) -> str:
        word = token[0]
        hit = any(p.fullmatch(word.strip("`'\"()[]{}<>,.;:!?")) for p in known)
        return " " * len(word) if hit else word

    return re.sub(r"\S+", keep, line) if known else line


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# Path lists a sync writes (one repo-relative path per line, `#` comments allowed), from
# their one home in `lib.shared._constants`.
_DELIVERED_RECORDS = DELIVERED_RECORDS

# The canonical delivery manifest in racecar itself: one JSON object per line, whose
# `source` is the file in this repo.
_DELIVERY_MANIFEST = "scripts/racecar-manifest.jsonl"


def delivered_received(root: Path) -> frozenset[str]:
    """Repo-relative paths a sync wrote into this repo: copies, never sources.

    The narrower half of `delivered_exempt`. `make fmt` uses this set, because only a
    copy is overwritten by the next sync; in racecar every manifest `source` is a file
    racecar authors.
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

    `make fmt` takes the complement (never rewrite a file the next sync overwrites); the
    checking targets take the delivered set. NUL-separated so a path with a space
    survives.
    """
    received = delivered_received(root)
    kept = [
        path
        for path in raw.split(b"\0")
        if path
        and (path.decode("utf-8", "surrogateescape") in received) is keep_delivered
    ]
    return b"\0".join(kept) + b"\0" if kept else b""


def drop_missing(root: Path, raw: bytes) -> bytes:
    """NUL-separated paths in; only those present on disk, out.

    `git ls-files --cached` lists a tracked file deleted but not yet staged, and black
    refuses a missing path. The skipped count goes to stderr with the repair.
    """
    kept, missing = [], 0
    for path in raw.split(b"\0"):
        if not path:
            continue
        if (root / path.decode("utf-8", "surrogateescape")).exists():
            kept.append(path)
        else:
            missing += 1
    if missing:
        print(
            f"check_content_blind: skipped {missing} tracked path(s) not on disk — "
            "deletions not yet staged. `git add -A` records them.",
            file=sys.stderr,
        )
    return b"\0".join(kept) + b"\0" if kept else b""


def delivered_ignore_regex(root: Path) -> str:
    """An anchored alternation of the delivered paths, for `--ignore-paths` and mypy.

    Empty when nothing was delivered; callers then add no exclusion rather than an
    empty pattern.
    """
    paths = sorted(delivered_received(root))
    return "|".join("^" + re.escape(p) + "$" for p in paths)


def delivered_exempt(root: Path) -> frozenset[str]:
    """Repo-relative paths racecar delivered here, read from the delivery records.

    The guard never scans them: the repo cannot edit a file the next sync overwrites. A
    path list gives its first whitespace-separated token; the canonical manifest gives
    its `source` field.
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
    block = _frontmatter.block(readme.read_text(encoding="utf-8"))
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
    identifiers: (
        tuple[bool, frozenset[str], frozenset[str], tuple[re.Pattern[str], ...]] | None
    ) = None,
) -> list[str]:
    """Return one `rel:lineno: value | line` line per finding, over both arms.

    `structural` is None when the figure arm is off; `identifiers` is None when the
    identifier arm is off.
    """
    offenders: list[str] = []
    # This file quotes the shapes it forbids, so it always exempts itself.
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
        # Test files: the figure arm skips them (their figures are fixture literals);
        # the identifier arm still reads them.
        test_file = is_test(rel)
        for lineno, line in prose:
            if structural is not None and not test_file:
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

    # Before the policy read: these modes answer from the delivery record alone, even in
    # a repo that opted out of the scan.
    if args.delivered_regex:
        print(delivered_ignore_regex(root))
        return 0
    if args.exclude_delivered or args.only_delivered:
        sys.stdout.buffer.write(
            filter_delivered(
                root,
                drop_missing(root, sys.stdin.buffer.read()),
                keep_delivered=args.only_delivered,
            )
        )
        sys.stdout.buffer.flush()
        return 0

    f = Findings()

    policy = load_policy(root)
    malformed = policy.get("content_blind_malformed")
    if malformed is not None:
        # Reported and enabled: an unreadable value must not disable the scan.
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

    # Absent policy runs the anchored identifier types; an explicit
    # `content_blind: false` turns both arms off (shared/OWNERSHIP.md).
    known, refused = ((), []) if declined else known_names(policy)
    for problem in refused:
        f.error(problem)
    disabled, placeholders = disabled_identifiers(policy), placeholder_set(policy)
    identifiers = None if declined else (not opted_in, disabled, placeholders, known)

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
    return emit(
        f,
        "check_content_blind",
        ". A formula or worked example in prose must be written in VARIABLES, not "
        "numbers; a value that satisfies a published checksum belongs in no tracked "
        "prose at all (CONTENT_BLINDNESS.md).",
    )


def disabled_identifiers(policy: dict[str, object]) -> frozenset[str]:
    """Identifier type ids this repo has turned off, from `content_blind_identifiers_off`.

    Per type; `content_blind_placeholders` exempts one documented value instead.
    """
    raw = policy.get("content_blind_identifiers_off", [])
    return (
        frozenset(str(x).strip().lower() for x in raw)
        if isinstance(raw, list)
        else frozenset()
    )


def known_names(
    policy: dict[str, object],
) -> tuple[tuple[re.Pattern[str], ...], list[str]]:
    """The repo's own names (`content_blind_known_names`), and any entry that is not a regex.

    One regex per entry, matched against a whole token (`ADR[0-9]+`); see
    `_not_known_names`. An entry that does not compile is reported and matches nothing.
    """
    raw = policy.get("content_blind_known_names", [])
    patterns: list[re.Pattern[str]] = []
    refused: list[str] = []
    for entry in map(str, raw if isinstance(raw, list) else []):
        try:
            patterns.append(re.compile(entry))
        except re.error as exc:
            refused.append(
                f"README.md frontmatter: content_blind_known_names: {entry!r} is not a "
                f"valid regex ({exc}); it matches nothing until it is fixed."
            )
    return tuple(patterns), refused


def placeholder_set(policy: dict[str, object]) -> frozenset[str]:
    """Documented synthetic values, separators stripped, from `content_blind_placeholders`.

    Read by the identifier arm.
    """
    raw = policy.get("content_blind_placeholders", [])
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(re.sub(r"[ .\-/]", "", str(x)) for x in raw)


if __name__ == "__main__":
    sys.exit(main())
