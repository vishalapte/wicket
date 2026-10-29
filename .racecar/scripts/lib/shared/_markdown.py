"""The one reader of a markdown file: which lines are frontmatter, which are code, which are text.

Every racecar reader of a markdown file goes through `parse` (or `read`) and asks the document
what it wants: its text lines, headings, title, sections or links. None splits a file itself.

A line is in one region:

  `frontmatter`  the `---` block at the top, as `_frontmatter.split` bounds it
  `code`         a fenced block, from its opening marker through its closing one
  `text`         everything else

A fence opens on a line starting with three or more backticks or tildes, after any
indentation. It closes on a line whose marker is the same character and at least as long, as
CommonMark says: `~~~` does not close a backtick fence, and three backticks do not close four.
A block that never closes makes everything below it code; the document carries the open
marker (`unclosed`) so a reader that must not silently lose the rest of a file can say so.

Headings, the title, sections and links are read from text lines only. A reader that must see
every line -- a leak scan, where a leak in the frontmatter is still a leak -- reads `lines`
itself, and the choice is visible where it reads.

Line numbers count from 1 and are the file's own, frontmatter included, so a finding names the
line an editor opens.

Complexity: O(n) in lines
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import NamedTuple

from lib.shared import _frontmatter

FRONTMATTER, CODE, TEXT = "frontmatter", "code", "text"

_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


class Line(NamedTuple):
    """One line of the file: its number from 1, its text, and its region."""

    no: int
    text: str
    region: str


class Heading(NamedTuple):
    """A `#` heading on a text line: its line number, its level (1-6) and its title."""

    no: int
    level: int
    title: str


class Link(NamedTuple):
    """A `[text](target)` on a text line, outside an inline code span."""

    no: int
    text: str
    target: str


class Section(NamedTuple):
    """The lines under one heading, to the next heading of the same level or higher.

    `heading` is None for the preamble, the text before the first heading of the level.
    `start` is the first line number of the section (its heading's, when it has one) and `end`
    the line number one past its last.
    """

    heading: Heading | None
    start: int
    end: int


@dataclass(frozen=True)
class Document:
    """A markdown file, read once: every line with its region."""

    lines: tuple[Line, ...]
    frontmatter: str | None
    """The frontmatter block's text between its fences, or None when the file has none."""
    unclosed: str | None
    """The marker of a fence that never closed, or None."""

    def text(self) -> Iterator[Line]:
        """The text lines: not frontmatter, not code."""
        return (line for line in self.lines if line.region == TEXT)

    def body(self) -> Iterator[Line]:
        """Every line after the frontmatter, code included."""
        return (line for line in self.lines if line.region != FRONTMATTER)

    @cached_property
    def _headings(self) -> dict[int, Heading]:
        """Every heading on a text line, by its line number; read once per document."""
        found = {}
        for line in self.text():
            match = _HEADING.match(line.text)
            if match:
                found[line.no] = Heading(
                    line.no, len(match.group(1)), match.group(2) or ""
                )
        return found

    def headings(self, level: int | None = None) -> list[Heading]:
        """The headings on text lines, of `level` when one is given."""
        return [h for h in self._headings.values() if level is None or h.level == level]

    def heading_at(self, no: int) -> Heading | None:
        """The heading on line `no`, or None when that line is not a heading."""
        return self._headings.get(no)

    def title(self) -> str | None:
        """The first level-1 heading's title, or None."""
        first = self.headings(1)
        return first[0].title if first else None

    def sections(self, level: int = 2) -> list[Section]:
        """The file cut at each heading of `level`: the preamble first, then one per heading.

        A section runs to the next heading of the same level or higher, so a `##` section holds
        its `###` subsections and stops at the next `#` or `##`.
        """
        end = len(self.lines) + 1
        cuts = [h for h in self.headings() if h.level <= level]
        out: list[Section] = []
        first = cuts[0].no if cuts else end
        out.append(Section(None, 1, first))
        for index, heading in enumerate(cuts):
            if heading.level != level:
                continue
            stop = cuts[index + 1].no if index + 1 < len(cuts) else end
            out.append(Section(heading, heading.no, stop))
        return out

    def section(self, title: str, level: int = 2) -> Section | None:
        """The first section of `level` whose heading's title is `title`, or None."""
        return next(
            (
                s
                for s in self.sections(level)
                if s.heading is not None and s.heading.title == title
            ),
            None,
        )

    def within(self, section: Section) -> list[Line]:
        """The lines of `section` after its heading line, code and text alike."""
        first = section.start + 1 if section.heading is not None else section.start
        return [line for line in self.lines if first <= line.no < section.end]

    def links(self) -> list[Link]:
        """Every `[text](target)` on a text line, outside inline code spans."""
        found = []
        for line in self.text():
            # Found on the line with its code spans blanked, so a link quoted in backticks is
            # not one; read back from the line itself, so a link whose text is code
            # (`` [`A.md`](A.md) ``) keeps that text. Blanking keeps every column in place.
            for match in _LINK.finditer(without_code_spans(line.text)):
                text = line.text[match.start(1) : match.end(1)]
                target = line.text[match.start(2) : match.end(2)].strip()
                found.append(Link(line.no, text, target))
        return found


def without_code_spans(text: str) -> str:
    """`text` with every inline code span blanked to spaces, so columns stay where they were."""
    return _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), text)


def parse(text: str, *, frontmatter: bool = True) -> Document:
    """Read `text` once: each line's region, the frontmatter block, and an unclosed fence.

    `frontmatter=False` for a piece of a file -- a body already split from its block, one
    section, the text between two markers. Only a file's first line can open a frontmatter
    block, so a piece that happens to start with `---` is not one, and reading it as one
    would skip the piece's own first lines.
    """
    block, body = _frontmatter.split(text) if frontmatter else (None, text)
    raw = text.splitlines()
    head = len(text[: len(text) - len(body)].splitlines()) if block is not None else 0
    lines: list[Line] = []
    fence: str | None = None
    for index, line in enumerate(raw):
        no = index + 1
        if index < head:
            lines.append(Line(no, line, FRONTMATTER))
            continue
        opener = _FENCE.match(line)
        if fence is not None:
            marker = opener.group(2) if opener else ""
            if marker.startswith(fence[0]) and len(marker) >= len(fence):
                fence = None
            lines.append(Line(no, line, CODE))
        elif opener:
            fence = opener.group(2)
            lines.append(Line(no, line, CODE))
        else:
            lines.append(Line(no, line, TEXT))
    return Document(tuple(lines), block, fence)


def read(path: Path) -> Document:
    """Read the markdown file at `path`."""
    return parse(path.read_text(encoding="utf-8"))
