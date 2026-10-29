"""Walk the `@path` import chain an instruction file declares.

Claude Code expands a line beginning `@` in an instruction file by inlining the file it
names, recursively. That is how racecar's baseline reaches a session: `CLAUDE.md` names
`AGENTS.md`, which names the `shared/` files, the generated axiom list and the change SOP.
This computes the transitive closure of those lines.

Stdlib only.

Complexity: O(n) in files reached, each read once.
"""

from __future__ import annotations

from pathlib import Path

from lib.shared import _markdown


def imports(path: Path, root: Path) -> list[Path]:
    """The files `path`'s own `@path` lines name, resolved against `root`, in file order.

    Read through the markdown reader, so a `@` line in the frontmatter or inside a code
    block is not an import: the instruction loader does not expand one there either.
    """
    return [
        (root / line.text[1:].strip()).resolve()
        for line in _markdown.read(path).text()
        if line.text.startswith("@")
    ]


def closure(entry: Path, root: Path) -> tuple[set[Path], list[Path]]:
    """Every file reachable from `entry` by `@path` imports, and the ones that do not exist.

    The entry file itself counts as reached: it is loaded by being the entry. Paths are
    resolved, so two spellings of one file are one member. A cycle terminates on `seen`
    rather than recursing, which the loader also survives.
    """
    seen: set[Path] = set()
    broken: list[Path] = []
    stack = [entry.resolve()]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if not current.is_file():
            broken.append(current)
            continue
        stack += [target for target in imports(current, root) if target not in seen]
    return seen, broken


def import_edges(reached: set[Path], root: Path) -> tuple[tuple[str, str], ...]:
    """Every `@path` import as a `(importer, imported)` pair, relative to `root`.

    An edge whose target is missing is included.

    Sorted, so a rendering is stable across filesystems.
    """

    def rel(path: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()

    out: list[tuple[str, str]] = []
    for path in reached:
        if not path.is_file():
            continue
        out += [(rel(path), rel(target)) for target in imports(path, root)]
    return tuple(sorted(out))
