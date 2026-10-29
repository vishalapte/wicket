"""What each command prints before an edit and after it, compared line for line.

`api-fix/SKILL.md` makes this the condition for applying any edit to a surface.
It runs the command lines `_invocations` lists on both trees and reports every one whose
transcript -- stdout, stderr and exit code -- differs.

Before is a git ref, default `HEAD`, checked out in a scratch worktree and run with that
worktree's `src/` first on the import path, so it never imports the parent's package. After
is the working tree. Both run under the repo's own interpreter.

Only the nodes an edit can reach are compared: those whose static import closure, within
the package, holds a file that differs between the ref and the working tree, and those whose
lexicon nodes changed. A change to `lib/cli.py` therefore compares every node. A node whose
closure imports `racecar.lib._dispatch` also runs checker scripts it names only at run time,
which no import reaches, so any change under a script directory reaches it too: an edit to
`scripts/check_docs.py` compares `arch check`, the command that loads it.

The list is the old code's parser half and the lexicon's declared half. Generated from the
old code and run on the new, a flag or verb the edit removed shows up as a difference; a
declared line runs only where its spec row lets it (`_invocations`).

Each line runs isolated, in its own copy of the tree: every file git sees, copied into a
fresh repo, so what a `write` line writes lands in the copy and never in the tree being
compared, and no line sees what another wrote. `HOME` and `TMPDIR` point at a scratch
directory, bytecode is not written, and every variable that names a credential -- a token, a
key, a password, an agent socket -- is left out of the environment, so no line can reach a
tracker, a bucket or a host that needs one. A declared fixture is the directory inside the copy
the line runs from.

Output is normalised before it is compared: the tree roots, the scratch paths, dates, times,
durations and hex ids. A line whose transcripts differ runs again on both trees, and every
transcript line that changed between two runs of the same code is left out of the comparison.
The count of lines left out is reported with each noun.
"""

from __future__ import annotations

import ast
import difflib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.shared._python import repo_python
from lib.shared._root import package_root

from ._check_json import TIMEOUT
from ._error import SurfaceError
from ._form import _lexicon, audit_tree, package_of
from ._invocations import Invocation, declared_half, parser_half

_WORKERS = 4
#: An environment variable that may carry a credential, by name; matched broadly on purpose.
_CREDENTIAL = re.compile(
    r"TOKEN|SECRET|PASSW|CREDENTIAL|API_?KEY|ACCESS_KEY|PRIVATE_KEY|AUTH"
    r"|^AWS_|^GH_|^GITHUB_|^SSH_|^GNUPGHOME$|^GIT_ASKPASS$|^ANTHROPIC_|^OPENAI_",
    re.IGNORECASE,
)
_NORMALISE = (
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<date>"),
    (re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\b"), "<time>"),
    (re.compile(r"\b\d+(\.\d+)?s\b"), "<secs>"),
    (re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"), "<sha>"),
    (re.compile(r"/tmp/[^\s'\"),:]+"), "<tmp>"),
)


@dataclass(frozen=True)
class Transcript:
    """What one command line printed, normalised, and how it exited."""

    exit: int | None
    stdout: str
    stderr: str

    def lines(self) -> list[str]:
        """The transcript as the lines a diff reads."""
        return [
            f"exit {self.exit}",
            *(f"out| {line}" for line in self.stdout.splitlines()),
            *(f"err| {line}" for line in self.stderr.splitlines()),
        ]


def _git(root: Path, *args: str) -> str:
    """`git <args>` in `root`; a failure is a refusal naming what git said."""
    done = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise SurfaceError(f"git {' '.join(args)}: {done.stderr.strip() or 'failed'}")
    return done.stdout


def changed_files(root: Path, ref: str) -> set[str]:
    """Every path that differs between `ref` and the working tree, untracked files included."""
    tracked = _git(root, "diff", "--name-only", ref, "--").splitlines()
    untracked = _git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    return {p for p in [*tracked, *untracked] if p}


@contextmanager
def worktree(root: Path, ref: str) -> Iterator[Path]:
    """`ref` checked out in a scratch worktree, removed however the block ends."""
    scratch = Path(tempfile.mkdtemp(prefix="rc-transcript-"))
    before = scratch / "before"
    _git(root, "worktree", "add", "--detach", "--quiet", str(before), ref)
    try:
        yield before
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(before)],
            cwd=root,
            capture_output=True,
            check=False,
        )
        shutil.rmtree(scratch, ignore_errors=True)


def _module_file(src: Path, module: str) -> Path | None:
    """The file a dotted module name resolves to under `src`, or None."""
    base = src.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


#: The module that loads checker scripts by name at run time, so no import reaches them.
DISPATCH = "racecar.lib._dispatch"
#: Where the scripts it loads live: racecar's own tree, and an adopter's delivered copy.
SCRIPT_DIRS = ("scripts/", ".racecar/scripts/")


def _imports(path: Path, module: str, package: str) -> set[str]:
    """The package's own modules `path` imports, absolute and relative alike."""
    return {
        name
        for name in _named_imports(path, module)
        if name == package or name.startswith(package + ".")
    }


def _named_imports(path: Path, module: str) -> set[str]:
    """Every module `path` imports, relative imports resolved against `module`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    here = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = here.split(".")[: len(here.split(".")) - node.level + 1]
                base = ".".join([*parts, *([base] if base else [])])
            found.add(base)
            found |= {f"{base}.{alias.name}" for alias in node.names}
    return found


def loads_scripts(src: Path, files: set[str], root: Path | None = None) -> bool:
    """Whether any file in a closure imports the dispatcher that loads scripts by name.

    `files` are relative to `root`, the repo (default: `src`'s parent, the `src/` layout).
    """
    root = src.parent if root is None else root
    for rel in files:
        path = root / rel
        module = ".".join(path.relative_to(src).with_suffix("").parts)
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        if DISPATCH in _named_imports(path, module):
            return True
    return False


def closure(src: Path, module: str, package: str, root: Path | None = None) -> set[str]:
    """The repo-relative files `module` reaches through the package's own imports.

    Relative to `root`, the repo (default: `src`'s parent, the `src/` layout).
    """
    root = src.parent if root is None else root
    seen: set[str] = set()
    files: set[str] = set()
    todo = [module]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _module_file(src, name)
        if path is None:
            continue
        files.add(path.relative_to(root).as_posix())
        todo += sorted(_imports(path, name, package) - seen)
        parent = name.rpartition(".")[0]
        if parent:
            todo.append(parent)  # importing a submodule runs every package above it
    return files


def reached(
    root: Path, package: str, modules: list[str], changed: set[str]
) -> set[str]:
    """The node modules an edit can reach: an import closure, a loaded script, or a node of
    the repo's own lexicon changed."""
    src = package_root(root)
    own = _lexicon(root).lexicon_corpora(root).own.relative_to(root).as_posix()
    nodes = {p[len(own) + 1 :] for p in changed if p.startswith(f"{own}/")}
    scripts = any(p.startswith(SCRIPT_DIRS) for p in changed)
    out: set[str] = set()
    for module in modules:
        files = closure(src, f"{module}.__main__", package, root)
        if files & changed or (scripts and loads_scripts(src, files, root)):
            out.add(module)
            continue
        noun = module[len(package) + 1 :] if module != package else ""
        if noun:
            # A noun's nodes sit in its own directory; the root's sit at the top level.
            hit = any(p.startswith(f"{noun.replace('.', '/')}/") for p in nodes)
        else:
            hit = any("/" not in p for p in nodes)
        if hit:
            out.add(module)
    return out


def normalise(text: str, roots: list[Path]) -> str:
    """`text` with what differs between two honest runs replaced by a fixed token."""
    for place in sorted({str(r) for r in roots}, key=len, reverse=True):
        text = text.replace(place, "<root>")
    for pattern, token in _NORMALISE:
        text = pattern.sub(token, text)
    return text


def copy_tree(tree: Path, dest: Path) -> Path:
    """Every file git sees in `tree`, tracked or not, copied into a fresh repo at `dest`.

    Ignored files -- a virtualenv, caches, local state -- stay behind. The copy is a git
    repo of its own with no history, so a command that finds its root by `.git` finds the
    copy, and nothing it runs can reach the history of the tree it came from.
    """
    listed = _git(
        tree, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
    ).split("\0")
    for name in filter(None, listed):
        source = tree / name
        target = dest / name
        if source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(source), target)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(dest)], capture_output=True, check=False)
    return dest


def environment(home: Path, tmp: Path, src: Path) -> dict[str, str]:
    """The scratch environment one line runs in, with every credential left out."""
    kept = {k: v for k, v in os.environ.items() if not _CREDENTIAL.search(k)}
    return {
        **kept,
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "PYTHONPATH": str(src),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def run(line: Invocation, tree: Path, python: str, scratch: Path) -> Transcript:
    """Run one command line in its own copy of `tree`, isolated, and return what it printed."""
    home = scratch / "home"
    tmp = scratch / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    copy = copy_tree(tree, scratch / "tree")
    cwd = copy / line.fixture if line.fixture else copy
    try:
        done = subprocess.run(
            [python, "-m", line.module, *line.args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
            env=environment(home, tmp, package_root(copy)),
        )
    except subprocess.TimeoutExpired:
        return Transcript(None, "", f"did not finish in {TIMEOUT}s")
    roots = [tree, copy, cwd, scratch, home, tmp]
    return Transcript(
        done.returncode,
        normalise(done.stdout, roots),
        normalise(done.stderr, roots),
    )


def _diff(old: list[str], new: list[str]) -> list[str]:
    """The unified diff between two transcripts' lines, without its file headers."""
    return [
        text
        for text in difflib.unified_diff(old, new, lineterm="", n=1)
        if not text.startswith(("---", "+++"))
    ]


def _both(
    index: int, line: Invocation, trees: tuple[Path, Path], python: str, scratch: Path
) -> dict[str, Any]:
    """One line run on both trees, and the diff between what each printed.

    Where the two differ, both run again, and a transcript line that changed between two
    runs of the same code is left out of the diff: it varies by itself, not by the edit.
    """
    before, after = trees
    old = run(line, before, python, scratch / f"b{index}").lines()
    new = run(line, after, python, scratch / f"a{index}").lines()
    diff = _diff(old, new)
    unstable: set[str] = set()
    if diff:
        again_old = run(line, before, python, scratch / f"b{index}r").lines()
        again_new = run(line, after, python, scratch / f"a{index}r").lines()
        unstable = (set(old) ^ set(again_old)) | (set(new) ^ set(again_new))
        diff = _diff(
            [t for t in old if t not in unstable], [t for t in new if t not in unstable]
        )
    return {
        "noun": line.noun,
        "command": line.command(),
        "diff": diff,
        "unstable": len(unstable),
    }


def compare(
    root: Path, ref: str = "HEAD", noun: str | None = None
) -> list[dict[str, Any]]:
    """Every compared node: `{noun, lines, identical, unstable, differences: [{command, diff}]}`.

    `unstable` counts the transcript lines left out because two runs of the same code
    printed them differently.
    """
    package = package_of(root)
    changed = changed_files(root, ref)
    python = repo_python(root)
    after_tree = audit_tree(root)
    with worktree(root, ref) as before:
        before_tree = audit_tree(before)
        modules = sorted(
            {str(n.get("pkg")) for n in _walk(before_tree) if n.get("pkg")}
            | {str(n.get("pkg")) for n in _walk(after_tree) if n.get("pkg")}
        )
        nodes = reached(root, package, modules, changed)
        lines = [
            line
            for line in parser_half(before_tree, package)
            + declared_half(root, after_tree, package)
            if line.runs
            and line.module in nodes
            and (noun is None or line.noun == noun)
        ]
        with tempfile.TemporaryDirectory(prefix="rc-transcript-run-") as scratch:
            with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
                results = list(
                    pool.map(
                        lambda pair: _both(
                            pair[0], pair[1], (before, root), python, Path(scratch)
                        ),
                        enumerate(lines),
                    )
                )
    out: dict[str, dict[str, Any]] = {}
    for result in results:
        record = out.setdefault(
            result["noun"],
            {
                "noun": result["noun"],
                "lines": 0,
                "identical": 0,
                "unstable": 0,
                "differences": [],
            },
        )
        record["lines"] += 1
        record["unstable"] += result["unstable"]
        if result["diff"]:
            record["differences"].append(
                {"command": result["command"], "diff": result["diff"]}
            )
        else:
            record["identical"] += 1
    return [out[key] for key in sorted(out)]


def _walk(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Every node of an audit tree."""
    nodes = [tree]
    for child in tree.get("children") or []:
        nodes += _walk(child)
    return nodes


def findings(root: Path, ref: str, noun: str | None = None) -> list[dict[str, str]]:
    """`check --against`: one finding per command line whose transcript differs from `ref`'s."""
    return [
        {
            "kind": "transcript-differs",
            "finding": f"{node['noun']}: `{difference['command']}` prints differently "
            f"than at {ref} ({node['identical']} of {node['lines']} lines identical"
            f"{_left_out(node['unstable'])})\n"
            + "\n".join(f"  {text}" for text in difference["diff"]),
        }
        for node in compare(root, ref, noun)
        for difference in node["differences"]
    ]


def _left_out(count: int) -> str:
    """The note a finding carries when unstable transcript lines were left out."""
    return f"; {count} unstable line(s) left out" if count else ""


def unchanged_or_left(root: Path, noun: str | None = None) -> list[dict[str, str]]:
    """What `upgrade` still owes after a change: each command it made print differently.

    A node `upgrade` changed is not done while a command line prints differently than it
    did at `HEAD`. Where there is no `HEAD` to compare with, the item says the comparison
    could not run.
    """
    try:
        found = findings(root, "HEAD", noun)
    except SurfaceError as err:
        return [
            {
                "action": "judgment",
                "kind": "transcript-differs",
                "text": f"the before-and-after comparison could not run ({err}); compare "
                "what each changed command prints by hand",
            }
        ]
    return [
        {"action": "judgment", "kind": "transcript-differs", "text": item["finding"]}
        for item in found
    ]
