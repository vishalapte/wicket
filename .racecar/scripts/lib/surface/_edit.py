"""Editing a Python file in place: find an anchor by parsing, splice text, and say what happened.

Every function here reads the file, decides from its parsed structure whether the piece it
adds is already there, and writes only when it is not. Nothing is recognised by a mark left
in the code, so running one twice changes nothing the second time. Each returns one line:
`added`, `present`, or `unplaced` when the anchor it needs is not in the file.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _offset(data: bytes, line: int, col: int) -> int:
    """The byte offset of an ast (lineno, col_offset) pair, both as ast reports them."""
    lines = data.splitlines(keepends=True)
    return sum(len(chunk) for chunk in lines[: line - 1]) + col


def _literal(tree: ast.Module, owner: str) -> ast.List | ast.Dict | None:
    """The list a function named `owner` returns, or the dict a module name is bound to."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == owner:
            for stmt in node.body:
                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.List):
                    return stmt.value
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == owner
            and isinstance(node.value, ast.Dict)
        ):
            return node.value
    return None


def _keys(literal: ast.List | ast.Dict) -> set[str]:
    """What a verb or noun literal already lists: dict keys, or each row's first string."""
    if isinstance(literal, ast.Dict):
        items = literal.keys
    else:
        items = [e.elts[0] if isinstance(e, ast.Tuple) else e for e in literal.elts]
    return {
        i.value
        for i in items
        if isinstance(i, ast.Constant) and isinstance(i.value, str)
    }


def add_entry(path: Path, owner: str, key: str, entry: str) -> str:
    """Add `entry` to the literal `owner` holds unless it lists `key`; what happened.

    The entry goes in before the closing bracket, so the rows already there and any
    comment between them are left exactly as they were.
    """
    data = path.read_bytes()
    literal = _literal(ast.parse(data), owner)
    if literal is None:
        return f"unplaced  {path}: `{owner}` is not a literal list or dict to add {key!r} to"
    if key in _keys(literal):
        return f"present   {path}: {owner} lists {key!r}"
    assert literal.end_lineno is not None and literal.end_col_offset is not None
    close = _offset(data, literal.end_lineno, literal.end_col_offset) - 1
    before = data[:close].rstrip()
    items = literal.keys if isinstance(literal, ast.Dict) else literal.elts
    sep = b"" if not items or before.endswith(b",") else b","
    path.write_bytes(before + sep + b" " + entry.encode() + b"," + data[close:])
    return f"added     {path}: {owner} lists {key!r}"


def _defined(tree: ast.Module) -> set[str]:
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
    }


def _above_comments(lines: list[str], index: int) -> int:
    """`index` moved up past the comment lines directly above it, which belong to it."""
    while index > 0 and lines[index - 1].lstrip().startswith("#"):
        index -= 1
    return index


def add_block(path: Path, name: str, block: str, before: str | None) -> str:
    """Insert a top-level def or class unless `name` is defined; what happened.

    It goes above the statement defining or binding `before` (and above the comments that
    sit on that statement), or at the end of the file when `before` is None or absent.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    if name in _defined(tree):
        return f"present   {path}: {name}"
    lines = text.splitlines(keepends=True)
    anchor = next(
        (
            node.lineno
            for node in tree.body
            if before is not None
            and (
                getattr(node, "name", None) == before
                or before in {t.id for t in _targets(node)}
            )
        ),
        None,
    )
    if anchor is None:
        text = text.rstrip("\n") + "\n\n\n" + block.rstrip("\n") + "\n"
    else:
        at = _above_comments(lines, anchor - 1)
        lines.insert(at, block.rstrip("\n") + "\n\n\n")
        text = "".join(lines)
    path.write_text(text, encoding="utf-8")
    return f"added     {path}: {name}"


def _targets(node: ast.stmt) -> list[ast.Name]:
    if isinstance(node, ast.Assign):
        return [t for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target]
    return []


def add_parser_block(path: Path, verb: str, block: str) -> str:
    """Insert a verb's parser lines into `parser()`, above its `add_flag` loop."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "parser"),
        None,
    )
    if fn is None:
        return f"unplaced  {path}: no parser() to add {verb!r} to"
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == verb
        ):
            return f"present   {path}: parser() adds {verb!r}"
    anchor = next((s for s in fn.body if isinstance(s, (ast.For, ast.Return))), None)
    if anchor is None:
        return f"unplaced  {path}: parser() has no add_flag loop or return to add above"
    lines = text.splitlines(keepends=True)
    indented = "".join(
        f"    {line}\n" if line else "\n" for line in block.rstrip("\n").split("\n")
    )
    lines.insert(anchor.lineno - 1, indented + "\n")
    path.write_text("".join(lines), encoding="utf-8")
    return f"added     {path}: parser() adds {verb!r}"


def add_import(path: Path, module: str, level: int, name: str) -> str:
    """Make `from <level dots><module> import name` true in `path`; what happened."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    found = next(
        (
            n
            for n in tree.body
            if isinstance(n, ast.ImportFrom) and n.module == module and n.level == level
        ),
        None,
    )
    spelled = f"from {'.' * level}{module} import "
    if found is not None:
        names = [a.name for a in found.names]
        if name in names:
            return f"present   {path}: imports {name}"
        lines = text.splitlines(keepends=True)
        assert found.end_lineno is not None
        lines[found.lineno - 1 : found.end_lineno] = [
            spelled + ", ".join(sorted({*names, name})) + "\n"
        ]
        path.write_text("".join(lines), encoding="utf-8")
        return f"added     {path}: imports {name}"
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    lines = text.splitlines(keepends=True)
    at = imports[-1].end_lineno if imports else 0
    assert at is not None
    lines.insert(at, f"\n{spelled}{name}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return f"added     {path}: imports {name}"


def _tree(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        return None


def _parser_verbs(fn: ast.FunctionDef) -> dict[str, ast.Call]:
    """Each verb `parser()` adds, by the literal name its `add_parser` call gives it."""
    return {
        node.args[0].value: node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def _funcs(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    return next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name),
        None,
    )


def _calls(fn: ast.FunctionDef, attr: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]
