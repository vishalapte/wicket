#!/usr/bin/env python3
"""Install a repo's CLI as a native command: `~/.local/bin/<pkg>`, run from any directory.

    acme widget create --name example             # is: python -m acme.widget create ...
    racecar graph topology check                  # is: python -m racecar.graph.topology check

The installed file is the delivered shell launcher (`cli_launcher.sh`) with two lines filled
in, this repo's path and its package name. It is a copy, not a symlink, so the path has to be
written into it; the path lives only in the installed copy, never in the repo. Rerun after the
checkout moves.

The launcher keeps no table of nouns or verbs. At run time a leading word is a noun when
`<pkg>/<noun>/__main__.py` exists, and the rest goes to that module's own argparse. It sets
`RC_CLI`, which makes the CLI's `lib/cli.py` print `<pkg> <noun> <verb>` instead of
`python -m <pkg>.<noun> <verb>` in its usage, help, listings and errors.

Usage::

    cd <repo>
    python3 .racecar/scripts/install_cli.py                     # this repo's own package
    python3 .racecar/scripts/install_cli.py <pkg>               # write ~/.local/bin/<pkg>
    python3 .racecar/scripts/install_cli.py <pkg> --dir DIR     # write DIR/<pkg>
    python3 .racecar/scripts/install_cli.py <pkg> --dry-run     # say what it would write

With no `<pkg>`, the package is `[project].name` from pyproject.toml, `-` read as `_`; a repo
whose package has no `__main__.py` has no CLI, and the run says so and exits 0. A `<pkg>` named
outright with no `__main__.py` is refused.

A repo's `./install`, the symlink to the delivered `.racecar/install`, runs it with no `<pkg>`,
after `install_settings.py`. It replaces only what is ours: a launcher it wrote
before, or a symlink into this repo (the old `bin/<pkg>` launcher). Anything else at the
target is refused. Rerunning it writes nothing when the copy is current.

It imports nothing but the standard library and runs on any `python3` from 3.9, because a
repo's `./install` runs it before the repo has a venv.

Complexity: O(1) -- one file read, one file written.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

_HERE = Path(__file__).parent


#: The repo, from where racecar puts this file: `<repo>/.racecar/scripts/` in an adopter (and
#: racecar's own links there), `<repo>/scripts/` in racecar. A fixed layout, not a search, so
#: it needs no `lib.shared._root`, which a pre-`tomllib` `python3` cannot import.
REPO_ROOT = (
    _HERE.parent.parent if _HERE.parent.name == ".racecar" else _HERE.parent
).resolve()
DEFAULT_DIR = Path.home() / ".local" / "bin"
#: The second line of every launcher this writes: how a rerun knows a file is ours.
MARKER = "racecar-cli-launcher:"


def template() -> Path:
    """The delivered launcher: beside this script in an adopter, under `shell/` in racecar."""
    for candidate in (_HERE / "cli_launcher.sh", _HERE / "shell" / "cli_launcher.sh"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"install_cli: no cli_launcher.sh beside {_HERE}; resync racecar")


def render(pkg: str, repo: Path) -> str:
    """The launcher with `repo` and `pkg` filled in."""
    if "'" in str(repo):
        raise SystemExit(
            f"install_cli: {repo} holds a single quote; it cannot be installed"
        )
    text = template().read_text(encoding="utf-8")
    return text.replace("@REPO@", str(repo), 1).replace("@PKG@", pkg, 1)


def repo_name(root: Path = REPO_ROOT) -> str:
    """`[project].name` from `root`'s pyproject.toml; ValueError when it cannot be read.

    Read with a regular expression, not tomllib, which a 3.9 `python3` does not have.
    """
    path = root / "pyproject.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    section = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    found = section and re.search(r'^name\s*=\s*"([^"]+)"', section.group(1), re.M)
    if not found:
        raise ValueError(f"{path} has no [project] name")
    return found.group(1)


def identifier(name: str) -> str:
    """A project name as Python spells it: `my-tool` -> `my_tool`."""
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def main_module(name: str) -> Path:
    """Where `<name>/__main__.py` would be, under the repo's package home."""
    home = REPO_ROOT / "src" if (REPO_ROOT / "src").is_dir() else REPO_ROOT
    return home / name / "__main__.py"


def package(name: str) -> str:
    """`name`, once it is a package with a `__main__.py` under the repo's package home."""
    if not name.isidentifier() or not main_module(name).is_file():
        raise SystemExit(f"install_cli: no {main_module(name)}; name the package")
    return name


def ours(target: Path) -> bool:
    """Whether `target` may be replaced: a launcher of ours, or a symlink into this repo."""
    if target.is_symlink():
        return target.resolve().is_relative_to(REPO_ROOT)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()[:2]
    return len(lines) == 2 and MARKER in lines[1]


def install(text: str, target: Path, *, dry_run: bool) -> str:
    """Write `text` to `target`, mode 755. Returns what happened, for the caller to print."""
    present = target.exists() or target.is_symlink()
    if present and not ours(target):
        raise SystemExit(
            f"install_cli: {target} exists and is not a launcher of {REPO_ROOT}; "
            "refusing to overwrite. Remove it and rerun."
        )
    current = (
        present
        and not target.is_symlink()
        and target.read_text(encoding="utf-8") == text
    )
    if current:
        return "current"
    if dry_run:
        return "would write"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()
    target.write_text(text, encoding="utf-8")
    target.chmod(
        stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )
    return "wrote"


def _warn_if_not_on_path(directory: Path) -> None:
    entries = os.environ.get("PATH", "").split(os.pathsep)
    if directory.resolve() not in {Path(entry).resolve() for entry in entries if entry}:
        print(
            f"install_cli: {directory} is not on PATH; add "
            f'`export PATH="{directory}:$PATH"` to your shell profile.',
            file=sys.stderr,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 .racecar/scripts/install_cli.py",
        description="Install this repo's CLI as a native command on PATH.",
    )
    parser.add_argument(
        "package",
        nargs="?",
        help="the package to launch, as imported (the command gets the same name);"
        " default: this repo's [project].name",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_DIR,
        help="where to write it (default: ~/.local/bin)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="say what would be written, and write nothing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Install the launcher as DIR/<package>."""
    args = _parser().parse_args(argv)
    if args.package is None:
        try:
            pkg = identifier(repo_name())
        except ValueError as exc:
            raise SystemExit(f"install_cli: {exc}") from exc
        if not main_module(pkg).is_file():
            print(
                f"install_cli: no CLI ({main_module(pkg)} is absent); nothing to install"
            )
            return 0
    else:
        pkg = package(args.package)
    text = render(pkg, REPO_ROOT)
    directory = args.dir.expanduser()
    target = directory / pkg
    print(f"{install(text, target, dry_run=args.dry_run)}  {target}")
    _warn_if_not_on_path(directory)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
