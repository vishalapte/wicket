#!/usr/bin/env python3
"""GATE: close the repo README against `config.toml`, in both directions.

`check_required_docs.py` grades the README's existence, its frontmatter and its `pnode`
key; this grades its settings against `config.toml`:

  forward   every setting `config.toml` declares is named in the README.
  backward  every setting-shaped claim in the README names something
            `config.toml` declares.
  values    a scalar `default` appears in the README verbatim.

It does not require a heading, does not generate the section, and does not read a
nested-dict default (no single literal to look for).

No `config.toml`, no check, no finding.

Output:
  - One line per finding: `check_config_closure: <severity>: <message>`.
  - Summary: `check_config_closure: OK` (exit 0) or `... N errors` (exit 1).

Usage:
    python3 <path-to>/check_config_closure.py [--root <path>]

Complexity: O(S + R), S = settings declared, R = lines in the README. Two
files read once each; no treewalk.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from lib.shared._config import Declaration, env_names, parse, search_env_names
from lib.shared._report import Findings, emit
from lib.shared._root import find_repo_root

#: What a setting-shaped claim looks like in prose, for the backward direction: an
#: UPPER_SNAKE token of two or more segments (an environment variable). The lookahead
#: refuses a match followed by `.` and a letter or digit, so `CLAUDE_RACECAR.md` is not
#: read; `FOO_BAR.Next` is therefore not read either.
_ENVVAR = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b(?!\.[A-Za-z0-9])")

#: Environment variables that are not this repo's settings (`PATH` and friends).
_NOT_OURS = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "HOME",
        "SHELL",
        "EDITOR",
        "LC_ALL",
        "LANG",
        "TERM",
        "TMPDIR",
        "GIT_EDITOR",
        "GIT_SEQUENCE_EDITOR",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "CI",
        "DEBIAN_FRONTEND",
        "SOURCE_DATE_EPOCH",
    }
)


def documented(readme: str, name: str, spec: dict[str, Any]) -> bool:
    """Is this setting named in the README, by dotted name or by env var? Either counts."""
    if name in readme:
        return True
    return any(env in readme for env in env_names(spec))


def literal_of(spec: dict[str, Any]) -> str | None:
    """The default as the string a README would carry, or None if it has none.

    A bool is rendered lowercase (`true`). A nested default returns None, which the
    caller reads as "decline".
    """
    value = spec.get("default")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)) and str(value).strip():
        return str(value)
    return None


def grade(root: Path, f: Findings) -> None:
    """Put `config.toml` and the README against each other, both ways."""
    config = root / "config.toml"
    readme = root / "README.md"
    if not config.is_file():
        f.info("no config.toml; nothing to close the README against")
        return
    if not readme.is_file():
        f.info("no README.md to grade")
        return
    try:
        declared: Declaration = parse(config.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as err:
        f.error(f"config.toml does not parse: {err}")
        return

    text = readme.read_text(encoding="utf-8", errors="replace")
    found = declared.settings
    if not found:
        f.info("config.toml declares no setting; nothing to close")
        return

    for name, spec in sorted(found.items()):
        if not documented(text, name, spec):
            envs = env_names(spec)
            spelling = f" (or {', '.join(envs)})" if envs else ""
            f.error(
                f"config.toml declares `{name}`{spelling} and README.md never names "
                "it. A setting nobody wrote down is one an operator cannot discover."
            )
            continue
        literal = literal_of(spec)
        if literal is not None and literal not in text:
            f.error(
                f"`{name}` defaults to `{literal}` and README.md does not carry that "
                "literal. A documented default the declaration contradicts sends a "
                "reader somewhere the code never looks."
            )

    ours = {env for spec in found.values() for env in env_names(spec)}
    ours |= set(search_env_names(declared))
    for claimed in sorted(set(_ENVVAR.findall(text)) - ours - _NOT_OURS):
        f.error(
            f"README.md names `{claimed}` and config.toml declares no setting for "
            "it. A setting deleted from the code and left in the prose sends a "
            "reader to a variable that does nothing."
        )


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, grade the pair, print findings, return an exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None, help="repo root")
    args = parser.parse_args(argv)

    root = args.root or find_repo_root(Path.cwd())
    if root is None:
        print("check_config_closure: info: not a repository; nothing to grade")
        print("check_config_closure: OK")
        return 0

    f = Findings()
    grade(root, f)
    return emit(f, "check_config_closure")


if __name__ == "__main__":
    sys.exit(main())
