"""Mirror a template tree into an output tree, substituting placeholders.

Each template directory under arch-python/templates/<name>/ mirrors the target output
tree exactly: render_tree writes every file to out/<same relative path>, applying `subs`
(placeholder -> replacement) to its text. Empty files (package __init__.py) copy as empty;
*.sh files are made executable. A generator copies its static tree with render_tree, then
overlays the manifest-interpolated files (settings base, urlconf includes, per-vertical
adapters) on top -- so the template directory is a literal picture of what gets generated.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

from ._python import repo_python


def _substitute(text: str, subs: dict[str, str]) -> str:
    """`text` with every placeholder in `subs` replaced, in the order `subs` lists them."""
    for placeholder, value in subs.items():
        text = text.replace(placeholder, value)
    return text


def render_tree(
    template_dir: Path,
    out: Path,
    subs: dict[str, str] | None = None,
    *,
    clobber: bool = True,
    skip: frozenset[str] = frozenset(),
) -> list[Path]:
    """Copy template_dir into out 1:1, applying `subs` to each file's text and path.

    Returns the files written, in the order written. A symlinked template file is copied
    as the file it points at, so a template can carry a module whose one home is elsewhere.

    Constraints: substitution is a global str.replace, over each file's text and over its
    path relative to template_dir, so placeholder keys must be collision-proof sentinels
    (the __NAME__ / {name} convention) that cannot occur in real content, and a key that
    contains another must be listed first. Templates must be text (read as UTF-8); empty
    directories are not created (every package dir carries an __init__.py).

    `clobber=False` leaves a file that already exists as it is and leaves it out of the
    returned list, so a second render into the same tree writes nothing. `skip` names
    template files, by their path relative to template_dir, that are not rendered at all.
    """
    subs = subs or {}
    written: list[Path] = []
    for src in sorted(template_dir.rglob("*")):
        if not src.is_file():
            continue
        # Skip OS and build junk (.DS_Store, __pycache__), which read_text() cannot read.
        if src.name == ".DS_Store" or "__pycache__" in src.parts:
            continue
        if src.relative_to(template_dir).as_posix() in skip:
            continue
        dest = out / _substitute(src.relative_to(template_dir).as_posix(), subs)
        if not clobber and dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_substitute(src.read_text(), subs))
        if src.suffix == ".sh":
            dest.chmod(0o755)
        written.append(dest)
    return written


def format_python(dest: Path, paths: list[Path]) -> list[str]:
    """Run a repo's own formatters over Python rendered into it: isort, then black.

    A placeholder changes line lengths and import order, so no template text is
    formatter-clean for every name. Returns the formatters that are not installed and so did
    not run; the caller says so.
    """
    files = [str(path) for path in paths if path.suffix == ".py"]
    skipped: list[str] = []
    if not files:
        return skipped
    config = str(dest / "pyproject.toml")
    # The repo's own venv holds its isort and black, not the `python3` running this script.
    python = repo_python(dest)
    for tool, args in (
        ("isort", ["--settings-path", config]),
        ("black", ["--quiet", "--config", config]),
    ):
        if not _has(python, tool):
            skipped.append(tool)
            continue
        subprocess.run(
            [python, "-m", tool, *args, *files],
            cwd=dest,
            check=True,
            capture_output=True,
        )
    return skipped


def _has(python: str, tool: str) -> bool:
    """Whether `tool` imports under `python`."""
    if python == sys.executable:
        return importlib.util.find_spec(tool) is not None
    done = subprocess.run(
        [python, "-c", f"import {tool}"], capture_output=True, check=False
    )
    return done.returncode == 0
