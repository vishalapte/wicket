#!/usr/bin/env python3
"""Commit a thin, no-prefix launcher for a repo's ``python -m <pkg>…`` CLI.

Every racecar-shaped repo exposes its CLI as ``python -m <pkg>.<noun>[.<subnoun>…]
<verb> [args…]`` (``arch-python/CLI.md``), which requires the caller to be inside the
repo with its venv on ``PATH`` (or spell ``.venv/bin/python -m …`` by hand) every
time. This writes one small, self-locating executable to ``$REPO/bin/<pkg>`` — a
committable file, not a machine-local one::

    delphi apt.report build          # was: (cd ~/dev/…/delphi && .venv/bin/python -m
                                      #       delphi.apt.report build)
    meridian dashboard residency     # was: PYTHONPATH=src .venv/bin/python -m
                                      #       meridian.dashboard residency

...once ``delphi``/``meridian`` is reachable on ``PATH`` — a symlink into the repo
(``--link``, default target ``/usr/local/bin``) being the intended route, since it
keeps working when the repo's own copy changes; a plain ``cp`` elsewhere only runs
correctly if ``<pkg>`` happens to already be importable in whatever interpreter ends
up running it.

**Why ``bin/<pkg>`` and not a bare ``<pkg>`` at the repo root.** `check_docs.py`'s
`_top_level` treats a repo's own root-level entry names as the signal that a cited
path like ``<pkg>/lib/foo.py`` names THIS repo (as opposed to a worked example);
racecar's own docstrings write exactly that shorthand for ``src/<pkg>/lib/foo.py``,
and until a real top-level entry existed named after the package, that whole
citation style was silently exempt from validation. A committed file literally
called ``<pkg>`` at the repo root makes ``<pkg>`` such an entry, which flips those
citations into "checked" — against the wrong root, since the real target is under
``src/``. ``bin/`` carries no such name collision and never will.

**What it is not.** Not a new CLI, not a shell alias, not a second surface. The
committed file bakes in exactly one fact — the package's own name — and otherwise
locates its repo from its own path at RUN time (``Path(__file__).resolve().parent.parent``,
which follows a symlink back to this repo even when ``PATH`` reaches it through one),
finds that repo's venv the same three-candidate way ``racecar.mk``'s own ``VENV``
auto-detect does, and hands everything else to ``runpy.run_module`` (the documented
equivalent of ``python -m``). No machine-specific path is ever written to disk, which
is what makes the file safe to commit: it works unchanged on every clone, wherever
that clone lives, on whoever's machine — and needs no racecar checkout to run, only
to (re)generate when the CLI tree changes. The package's own ``__main__.py`` +
argparse tree is the one source of truth for every verb and flag; this generates no
structure of its own, exactly as ``gen_cli_docs.py`` projects that same tree into
pages rather than inventing one.

**Why re-exec into the venv rather than importing its packages.** A venv's own
``bin/python`` is itself a symlink to a base interpreter, and CPython only honours a
venv's ``pyvenv.cfg`` — and so its site-packages — for a process actually EXECED as
that symlink path, not one that merely adds its site-packages directory to
``sys.path`` by hand (the version-specific ``lib/pythonX.Y/site-packages`` name would
have to be hard-coded, and would still miss anything a venv config wires beyond the
path). Re-execing once (guarded so a re-exec cannot loop) is what makes this launcher
correct regardless of which ``python3`` happened to be first on ``PATH``.

Usage::

    python scripts/gen_cli_launcher.py             # print what --write would commit
    python scripts/gen_cli_launcher.py --write      # commit $REPO/bin/<pkg>
    python scripts/gen_cli_launcher.py --check      # non-zero if $REPO/bin/<pkg> is stale/missing
    python scripts/gen_cli_launcher.py --link       # --write, then symlink it onto --link-dir
    python scripts/gen_cli_launcher.py --link-dir DIR   # override the symlink target (default:
                                                         # /usr/local/bin)
    python scripts/gen_cli_launcher.py src/<pkg>    # point at a package (default: auto-discover)

Complexity: O(1) — one package-name lookup, one file written, at most one symlink.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from check_packaging_rules._root import find_repo_root

REPO_ROOT = find_repo_root()
if not (REPO_ROOT / ".git").exists():
    REPO_ROOT = Path(__file__).resolve().parents[1]

SRC = REPO_ROOT / "src" if (REPO_ROOT / "src").is_dir() else REPO_ROOT
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from check_cli_commands import (  # noqa: E402 # pylint: disable=wrong-import-position
    _default_root,
    _resolve_root,
)

DEFAULT_LINK_DIR = Path("/usr/local/bin")

# The committed launcher carries NO absolute path -- everything below is a fixed
# template, `pkg` the only substitution, so the rendered bytes are identical
# regardless of which machine or checkout location generated them.
_LAUNCHER_TEMPLATE = '''\
#!/usr/bin/env python3
"""GENERATED by racecar `gen_cli_launcher.py` -- DO NOT EDIT.
Regenerate:  {regenerate}

Runs `{pkg}`'s CLI with no `python -m` prefix, no venv to source or activate, and no
racecar on the machine that runs it. Self-locating: `REPO_ROOT` below is the parent
of this file's own real location (it lives at `$REPO/bin/{pkg}`), which is why a
symlink to it (e.g. into /usr/local/bin) keeps working while a plain copy does not --
a copy has no path back to this repo, and only runs correctly where `{pkg}` is
already importable some other way (a `pip install`).

When the repo IS found, this re-execs into its own venv (`.venv` / `venv` / `../venv`,
the same order `racecar.mk`'s `VENV` auto-detect checks) exactly once, because a
venv's interpreter is itself a symlink and Python only honours a venv when invoked AS
that symlink -- adding its site-packages by hand cannot substitute for the swap.

The first CLI argument is tried as a submodule of `{pkg}`; when that resolves, the
module tree takes over from there exactly as `python -m {pkg}.<that argument>` would,
and every remaining argument is its own to parse. When it does not resolve (no
argument, or the package has no such submodule), the whole command line is left for
`{pkg}`'s own root CLI.
"""
from __future__ import annotations

import importlib.util
import os
import runpy
import sys
from pathlib import Path

PKG = "{pkg}"
_REEXEC_MARK = f"_{{PKG.upper()}}_LAUNCHER_REEXECED"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _venv_python() -> Path | None:
    """The interpreter this repo's own `racecar.mk` would pick, or None.

    Resolves the venv DIRECTORY (so `../venv` and a symlinked ancestor normalize)
    but leaves the final `bin/python` un-followed -- see the module docstring.
    """
    for candidate in (".venv", "venv", "../venv"):
        venv_dir = (REPO_ROOT / candidate).resolve()
        python = venv_dir / "bin" / "python"
        if python.is_file() and os.access(python, os.X_OK):
            return python
    return None


def _target(argv: list[str]) -> tuple[str, list[str]]:
    """(module, remaining argv) -- consume argv[0] into the module path only if
    `PKG.argv[0]` actually exists; otherwise run PKG itself on the untouched argv."""
    if argv:
        candidate = f"{{PKG}}.{{argv[0]}}"
        try:
            exists = importlib.util.find_spec(candidate) is not None
        except (ImportError, ValueError):
            exists = False
        if exists:
            return candidate, argv[1:]
    return PKG, argv


if not os.environ.get(_REEXEC_MARK):
    _venv = _venv_python()
    if _venv is not None:
        os.environ[_REEXEC_MARK] = "1"
        os.execv(str(_venv), [str(_venv), __file__, *sys.argv[1:]])

_src = REPO_ROOT / "src"
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

_module, _rest = _target(sys.argv[1:])
sys.argv = [_module, *_rest]
runpy.run_module(_module, run_name="__main__", alter_sys=True)
'''

REGENERATE = "python scripts/gen_cli_launcher.py --write"


def render(pkg: str) -> str:
    """The launcher's full text -- `pkg` the only thing baked in."""
    return _LAUNCHER_TEMPLATE.format(regenerate=REGENERATE, pkg=pkg)


def build(root: str | None) -> tuple[str, Path]:
    """`(launcher text, commit path)` for `root` (default: auto-discover, like
    `check_cli_commands`/`gen_cli_docs`). The commit path is always `$REPO/bin/<pkg>`
    -- a committed file, not a machine-local install; `bin/` rather than the repo
    root itself so a file named for the package never becomes a root-level entry
    (see the module docstring's `_top_level` note)."""
    pkg = _resolve_root(root or _default_root())
    return render(pkg), REPO_ROOT / "bin" / pkg


def _current(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def write(text: str, path: Path) -> bool:
    """Write `text` to `path` and mark it executable. Returns whether it changed."""
    changed = _current(path) != text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return changed


def link(path: Path, link_dir: Path) -> str:
    """Point `link_dir/<path.name>` at `path`, replacing a stale symlink of ours but
    refusing to touch anything else there. Returns what happened, for the caller to
    print: 'linked', 'already linked', or (raising) why it could not proceed."""
    target = link_dir / path.name
    if target.is_symlink():
        if target.resolve() == path.resolve():
            return "already linked"
        target.unlink()
    elif target.exists():
        raise SystemExit(
            f"gen_cli_launcher: {target} exists and is not a symlink -- "
            "refusing to overwrite; remove it manually and re-run --link."
        )
    link_dir.mkdir(parents=True, exist_ok=True)
    target.symlink_to(path)
    return "linked"


def _warn_if_not_on_path(link_dir: Path) -> None:
    entries = os.environ.get("PATH", "").split(os.pathsep)
    resolved = {Path(entry).resolve() for entry in entries if entry}
    if link_dir.resolve() not in resolved:
        print(
            f"gen_cli_launcher: {link_dir} is not on PATH -- add "
            f'`export PATH="{link_dir}:$PATH"` to your shell profile.',
            file=sys.stderr,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/gen_cli_launcher.py",
        description="Commit a no-prefix launcher for this repo's `python -m <pkg>…` CLI.",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="package or src path to launch (default: a src/ package, else the cwd)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="commit $REPO/bin/<pkg> and mark it executable",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if $REPO/bin/<pkg> is missing or stale (writes nothing)",
    )
    parser.add_argument(
        "--link",
        action="store_true",
        help="--write, then symlink $REPO/bin/<pkg> onto --link-dir",
    )
    parser.add_argument(
        "--link-dir",
        type=Path,
        default=DEFAULT_LINK_DIR,
        help=f"where --link points its symlink (default: {DEFAULT_LINK_DIR})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Print, commit, check, or link the launcher for this repo's CLI."""
    args = _parser().parse_args(argv)
    text, path = build(args.root)

    if args.check:
        current = _current(path)
        if current != text:
            state = "missing" if current is None else "stale"
            print(f"{state}: {path}\nRegenerate:  {REGENERATE}", file=sys.stderr)
            return 1
        print(f"{path}: current")
        return 0

    if args.write or args.link:
        changed = write(text, path)
        print(("wrote   " if changed else "current ") + str(path))
        if args.link:
            print(f"{link(path, args.link_dir)}  {args.link_dir / path.name}")
            _warn_if_not_on_path(args.link_dir)
        return 0

    print(f"would commit: {path}\n")
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
