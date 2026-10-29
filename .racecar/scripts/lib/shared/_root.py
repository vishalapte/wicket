"""The `.git` walk-up every delivered script uses to find the repo it is grading, and the
one place that says where that repo keeps its package.

`src/racecar/lib/_root.py` and `lib/packaging/_root.py` are symlinks to this file. What only
racecar needs (where the racecar checkout is, the `--root` check) is in
`lib/racecar_only/_root_racecar_only.py`, which no adopter receives.

A delivered checker runs from an adopter's flat `scripts/` with no racecar installed, so this
file imports nothing from `racecar`.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor of `start` (default CWD) holding `.racecar/` or `.git`.

    `.racecar/` first in meaning: it is where racecar was delivered, so its parent is the
    repo racecar governs, and `package create` writes it before any `git init`. `.git`
    answers for a repo racecar has not been delivered into yet, which is where the first
    sync runs. Whichever marker is nearer wins, so a checkout nested in another resolves to
    itself.

    Resolved, so the root compares equal to the `--root` paths callers already resolve.
    Returns `start` when neither is found, so a checker run outside a repo still reports.
    """
    start = (start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".racecar").is_dir() or (candidate / ".git").exists():
            return candidate
    return start


def git_common_dir(path: Path) -> Path | None:
    """Return the repository `path` belongs to, or None when it is not in one.

    The common dir, not the git dir: a worktree's git dir is private to it
    (`.git/worktrees/<name>`) while its common dir is shared by every worktree.

    None on every negative: not a repo, no git on the machine, a path that does not
    exist. A caller comparing two of these must therefore treat None as "cannot say",
    never as a match, which :func:`same_repository` does.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # git is not installed
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    common = Path(proc.stdout.strip())
    return common.resolve() if common.is_absolute() else (path / common).resolve()


def same_repository(one: Path, other: Path) -> bool:
    """Whether two paths are checkouts of ONE repository -- a git worktree included.

    A path comparison cannot answer this: `.claude/worktrees/<branch>/` is nested inside the
    main checkout and is a separate checkout of the same repository.

    A path is always the same checkout as itself, git repository or not.

    False when either side cannot be resolved. An unanswerable question is not a match.
    """
    if one.resolve() == other.resolve():
        return True
    here = git_common_dir(one)
    return here is not None and here == git_common_dir(other)


def project_name(root: Path) -> str | None:
    """Return `[project].name`, or None when the repo declares none.

    Tolerant of an unparseable or absent pyproject: a malformed manifest is the packaging
    checker's finding.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None
    name = data.get("project", {}).get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def package_root(root: Path) -> Path:
    """Where `root` keeps its package: `src/` when it exists, else the repo root when the
    package `[project].name` names sits there (the `flat` shape, `flat_package`), else `src/`.

    A repo with neither is read as `src/`, where racecar scaffolds a new package.
    """
    src = root / "src"
    if src.is_dir():
        return src
    return root if flat_package(root) is not None else src


def flat_package(root: Path) -> Path | None:
    """The package at the repo root that `[project].name` names, when there is no `src/`.

    A root holds more than packages (`tests/`, tooling), so at the root only the name the
    repo declares can say which folder it ships; with no name, there is no `flat` package.
    """
    if (root / "src").is_dir():
        return None
    name = project_name(root)
    if name is None:
        return None
    package = root / module_name(name)
    return package if (package / "__init__.py").is_file() else None


def has_library(root: Path) -> bool:
    """Whether `root` carries a library: a `src/`, or the package `[project].name` names at
    the root (the `flat` shape). The PYTHON_LIBRARY axis of the shape, stated once.
    """
    return (root / "src").is_dir() or flat_package(root) is not None


def _git_visible(root: Path, *patterns: str) -> list[str] | None:
    """The files matching `patterns` that git does not ignore, or None outside a repository."""
    try:
        out = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                *patterns,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return None
    return out.splitlines()


def django_roots(root: Path) -> list[Path]:
    """Every folder in `root` that holds a Django project, the same rule as racecar.mk's RC_DJANGO.

    Each top-level, non-hidden folder holding a `manage.py` that git does not ignore, in name
    order; else the repo root, when a root `manage.py` makes the `django` shape (no library);
    else none. No folder name is reserved. Outside a git repository every file counts.
    `racecar.mk` decides the same thing in Make (`_RC_DJANGO_MNG`, `_ROOT_MNG`), and a
    coherence test holds the two in step.
    """
    names = _git_visible(root, ":(glob)*/manage.py", "manage.py")
    if names is None:
        names = [p.relative_to(root).as_posix() for p in root.glob("*/manage.py")]
        names += ["manage.py"] if (root / "manage.py").is_file() else []
    folders = sorted(
        {n.split("/", 1)[0] for n in names if "/" in n and not n.startswith(".")}
    )
    if folders:
        return [root / folder for folder in folders]
    if "manage.py" in names and not has_library(root):
        return [root]
    return []


def packages(root: Path) -> list[Path]:
    """Every directory under the package root that holds an `__init__.py`, sorted by name.

    Under a `flat` root, only the named package: the rest of a root is not the package.
    """
    flat = flat_package(root)
    if flat is not None:
        return [flat]
    home = package_root(root)
    if not home.is_dir():
        return []
    return [p for p in sorted(home.iterdir()) if (p / "__init__.py").is_file()]


def module_name(name: str) -> str:
    """A distribution name as the import name it builds: `-` and `.` become `_`."""
    return re.sub(r"[-.]+", "_", name)


def package_dir(root: Path) -> Path | None:
    """The repo's package: the one entry of `packages(root)`.

    With several, the one `[project].name` names (as its import name, `module_name`). The
    repo states which of them it ships, so racecar reads that rather than picking one.
    None when there is none, or several and the name settles nothing; a command then has
    no package to act on.
    """
    found = packages(root)
    if len(found) == 1:
        return found[0]
    name = project_name(root)
    if name is None:
        return None
    named = [p for p in found if p.name == module_name(name)]
    return named[0] if named else None


def not_present(root: Path) -> str:
    """Why a check does not apply to `root`, in the owner's three cases, worded once.

    A package and no Django project: a Django check says "No django server to check". A
    Django project and no package: a package check says "No package to check". Neither:
    "Nothing to check". Every checker that skips for want of its subject says one of these,
    so the same repo is described the same way everywhere.
    """
    package, server = package_dir(root) is not None, bool(django_roots(root))
    if package and not server:
        return "No django server to check"
    if server and not package:
        return "No package to check"
    return "Nothing to check"
