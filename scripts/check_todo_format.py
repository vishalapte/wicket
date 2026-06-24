#!/usr/bin/env python3
"""Mechanical check for federated TODO work against the racecar source schema.

Enforces the on-disk schema defined in `shared/TODO_FORMAT.md`, "Source schema".
Open work is federated by concern: a concern doc (LABELS.md, a subsystem
DESIGN.md, ...) co-locates a `## TODO` backlog and a `## PLAN` schedule, and the
repo-root `TODO.md` is the resolver/index. The item schema is invariant wherever
items live.

Nothing here is repo-specific: the script discovers the repo root at runtime, so
the same file works for racecar and any consumer that adopts the standard.

Checks (each independently failable):

  1. If a repo-root `TODO.md` exists it carries a `## TODO` heading (the index).
     A repo with no root `TODO.md` is silent (exit 0).
  2. In every markdown file, for every `## TODO` section, each `### {id} — {title}`
     item carries `Prio:` (literally P0..P3), `Depends:` (ids / none / LAST), and
     `Updated:` (an ISO date). A section with no items is a resolver and is fine.
  3. Item ids are unique within their file.

Fenced code blocks (``` ... ```) are skipped, so documentation examples do not
self-trip. Hidden directories are skipped.

Exit 0 if clean, 1 if any drift is found.

Usage:
    python3 <path-to>/check_todo_format.py
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path


def _find_repo_root() -> Path:
    start = Path.cwd()
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return start


REPO_ROOT = _find_repo_root()

H2_RE = re.compile(r"^##\s+(.*?)\s*$")
H3_RE = re.compile(r"^###\s+(.*?)\s*$")
ITEM_RE = re.compile(r"^(?P<id>\S+)\s+[—-]\s+(?P<title>.+?)\s*$")
# Fields may be plain (`Prio: P0`) or markdown list items (`- Prio: P0`).
PRIO_RE = re.compile(r"^[-*]?\s*Prio:\s*(P[0-3])\s*$")
DEPENDS_RE = re.compile(r"^[-*]?\s*Depends:\s*(.+?)\s*$")
UPDATED_RE = re.compile(r"^[-*]?\s*Updated:\s*(\d{4}-\d{2}-\d{2})\s*$")
UPDATED_KEY_RE = re.compile(r"^[-*]?\s*Updated:")
FENCE_RE = re.compile(r"^\s*```")


class Finding:
    """A single TODO-format violation at a file path and line."""

    def __init__(self, rel: str, line: int, message: str) -> None:
        self.rel = rel
        self.line = line
        self.message = message

    def render(self) -> str:
        """Render the finding as a `  path:line  message` report line."""
        return f"  {self.rel}:{self.line}  {self.message}"


def _iso_date_ok(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _markdown_files() -> list[Path]:
    out: list[Path] = []
    for p in sorted(REPO_ROOT.rglob("*.md")):
        rel = p.relative_to(REPO_ROOT)
        if any(part.startswith(".") for part in rel.parts):
            continue
        out.append(p)
    return out


def _code_mask(lines: list[str]) -> list[bool]:
    """True for lines inside a ``` fenced block (the fence lines included)."""
    mask = [False] * len(lines)
    in_fence = False
    for i, ln in enumerate(lines):
        if FENCE_RE.match(ln):
            mask[i] = True
            in_fence = not in_fence
            continue
        mask[i] = in_fence
    return mask


def _todo_section_ranges(lines: list[str], mask: list[bool]) -> list[tuple[int, int]]:
    """Half-open [start, end) line-index ranges of each `## TODO` body."""
    ranges: list[tuple[int, int]] = []
    starts: list[int] = []
    for i, ln in enumerate(lines):
        if mask[i]:
            continue
        m = H2_RE.match(ln)
        if m and m.group(1).strip().lower() == "todo":
            starts.append(i)
    for s in starts:
        end = len(lines)
        for j in range(s + 1, len(lines)):
            if not mask[j] and H2_RE.match(lines[j]):
                end = j
                break
        ranges.append((s + 1, end))
    return ranges


def check_file(path: Path) -> list[Finding]:
    """Validate every TODO item in `path` against the format schema."""
    rel = path.relative_to(REPO_ROOT).as_posix()
    lines = path.read_text(encoding="utf-8").splitlines()
    mask = _code_mask(lines)
    findings: list[Finding] = []
    seen_ids: dict[str, int] = {}

    for start, end in _todo_section_ranges(lines, mask):
        # Item heading lines within this section.
        item_heads: list[int] = []
        for i in range(start, end):
            if mask[i]:
                continue
            h3 = H3_RE.match(lines[i])
            if h3 and ITEM_RE.match(h3.group(1)):
                item_heads.append(i)
        for idx, head in enumerate(item_heads):
            item = ITEM_RE.match(H3_RE.match(lines[head]).group(1))
            item_id = item.group("id")
            lineno = head + 1
            if item_id in seen_ids:
                findings.append(
                    Finding(
                        rel,
                        lineno,
                        f"duplicate TODO id {item_id!r} "
                        f"(also at line {seen_ids[item_id]})",
                    )
                )
            else:
                seen_ids[item_id] = lineno
            body_end = item_heads[idx + 1] if idx + 1 < len(item_heads) else end
            body = [lines[j].strip() for j in range(head + 1, body_end) if not mask[j]]
            if not any(PRIO_RE.match(b) for b in body):
                findings.append(
                    Finding(rel, lineno, f"{item_id}: missing `Prio:` field (P0..P3)")
                )
            if not any(DEPENDS_RE.match(b) for b in body):
                findings.append(
                    Finding(
                        rel,
                        lineno,
                        f"{item_id}: missing `Depends:` field (ids / none / LAST)",
                    )
                )
            updated = next(
                (UPDATED_RE.match(b) for b in body if UPDATED_RE.match(b)), None
            )
            if updated is None:
                if any(UPDATED_KEY_RE.match(b) for b in body):
                    findings.append(
                        Finding(
                            rel,
                            lineno,
                            f"{item_id}: `Updated:` is not an ISO date (YYYY-MM-DD)",
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            rel,
                            lineno,
                            f"{item_id}: missing `Updated:` field (YYYY-MM-DD)",
                        )
                    )
            elif not _iso_date_ok(updated.group(1)):
                findings.append(
                    Finding(
                        rel,
                        lineno,
                        f"{item_id}: `Updated:` {updated.group(1)} is not a valid date",
                    )
                )
    return findings


def main() -> int:
    """Validate the root TODO index and every markdown TODO; return an exit code."""
    todo = REPO_ROOT / "TODO.md"
    if not todo.is_file():
        print("todo-format: no root TODO.md — nothing to validate")
        return 0

    findings: list[Finding] = []

    # The root TODO.md must carry a `## TODO` index heading.
    root_lines = todo.read_text(encoding="utf-8").splitlines()
    root_mask = _code_mask(root_lines)
    has_index = any(
        not root_mask[i]
        and H2_RE.match(ln)
        and H2_RE.match(ln).group(1).strip().lower() == "todo"
        for i, ln in enumerate(root_lines)
    )
    if not has_index:
        findings.append(
            Finding(
                "TODO.md",
                1,
                "missing `## TODO` index heading (TODO_FORMAT.md source schema)",
            )
        )

    for path in _markdown_files():
        findings.extend(check_file(path))

    if not findings:
        print("todo-format: OK")
        return 0
    print(f"todo-format: {len(findings)} issue(s)")
    for f in findings:
        print(f.render())
    return 1


if __name__ == "__main__":
    sys.exit(main())
