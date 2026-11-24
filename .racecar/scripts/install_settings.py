#!/usr/bin/env python3
"""Install a repo's own settings into the local Claude Code config.

A repo's `./install`, the symlink to the delivered `.racecar/install`, runs this and then
`install_cli.py`. The repo is the one this file is delivered into; its name is
`[project].name` in pyproject.toml, and its prefix is that name in capitals, `-` read as `_`,
plus `_` (acme -> ACME_). Idempotent: a second run writes nothing.

Each input below is a file the repo may hold. An absent file is skipped; a malformed one
refuses the run. Writes, into $CLAUDE_CONFIG_DIR (default ~/.claude):

1. settings.json "env", from defaults.json: every key.
   - Every key must carry the repo's prefix. One that does not refuses the run.
   - A null value is not written, and a key of that name already set is kept.
   - `<KEY>` inside a value is the value of KEY, another key of the same file.
   - `~` is expanded, because Claude Code does not expand it in "env".
2. settings.json "hooks", from hooks.json: each hook is a script in this checkout. A hook
   whose command is a path in this checkout is the repo's, and one hooks.json no longer
   declares is removed. Every other hook is kept.
3. <NAME>.md, copied from CLAUDE_<NAME>.md (doc-graph frontmatter removed), and one
   `@<NAME>.md` line in CLAUDE.md that imports it. The rest of CLAUDE.md is never touched.

settings.json is printed one `path = value` line per setting, and only the settings that
change, so a value that is not changing is never printed:
- A setting added (its key absent today) is written without asking: `+path = value`.
- A setting changed (`-path = old`, `+path = new`) or removed (`-path = old`) is written only
  when the person at the terminal answers y or yes to "Replace <path>? [y/N]". With no
  terminal, none is written, and the run says to run ./install in a terminal.
- The additions are written either way, in the one write of settings.json.
- `--dry-run` prints the same lines, asks nothing and writes nothing.

The run refuses, exit 2, before writing anything when pyproject.toml has no [project] name,
defaults.json is not a JSON object of string or null values, a key lacks the prefix, a
placeholder names no string key of the file, hooks.json is malformed or carries the retired
`"retired"` list, CLAUDE_<NAME>.md opens frontmatter it never closes, or settings.json is not
JSON.

hooks.json:
    {"SessionStart": [{"matchers": ["startup", "resume"], "command": "hooks/x.sh"}],
     "SessionEnd": [{"matchers": [""], "command": "hooks/y.sh", "timeout": 420}]}

It imports only the standard library and runs on any `python3` from 3.9, because `./install`
runs before the repo has a venv.

Usage:
    python3 .racecar/scripts/install_settings.py
    python3 .racecar/scripts/install_settings.py --dry-run

Complexity: O(n) in the settings and hooks it compares.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from install_cli import REPO_ROOT, identifier, repo_name

DEFAULTS = REPO_ROOT / "defaults.json"
HOOKS = REPO_ROOT / "hooks.json"
PLACEHOLDER = re.compile(r"<([^<>]*)>")

#: (event, matcher, hook) for each hook hooks.json declares.
Hooks = list[tuple[str, str, dict[str, object]]]


class InstallError(Exception):
    """This checkout's files cannot be turned into what to write."""


def load_defaults(prefix: str) -> dict[str, str]:
    """The env to write: non-null values, placeholders filled, `~` expanded; {} without a file."""
    if not DEFAULTS.exists():
        return {}
    try:
        data = json.loads(DEFAULTS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"cannot read {DEFAULTS}: {exc}") from exc
    if not isinstance(data, dict) or not all(
        value is None or isinstance(value, str) for value in data.values()
    ):
        raise InstallError(f"{DEFAULTS} must be a JSON object of string or null values")
    foreign = sorted(name for name in data if not name.startswith(prefix))
    if foreign:
        raise InstallError(
            f"{DEFAULTS}: not this repo's ({prefix}*): {', '.join(foreign)}"
        )
    out: dict[str, str] = {}
    for name, value in data.items():
        if value is None:
            continue
        for key in PLACEHOLDER.findall(value):
            target = data.get(key)
            if not isinstance(target, str) or PLACEHOLDER.search(target):
                raise InstallError(
                    f"{name}: <{key}> names no plain string key of {DEFAULTS}"
                )
            value = value.replace(f"<{key}>", target)
        out[name] = os.path.expanduser(value)
    return out


def shell_path(path: Path) -> str:
    """`$HOME/...` when under the home directory, so settings.json carries no user name."""
    try:
        return "$HOME/" + str(path.relative_to(Path.home().resolve()))
    except ValueError:
        return str(path)


def load_hooks() -> Hooks | None:
    """(event, matcher, hook) for every hook hooks.json declares; None without a file."""
    if not HOOKS.exists():
        return None
    try:
        data = json.loads(HOOKS.read_text(encoding="utf-8"))
        if "retired" in data:
            raise InstallError(
                f'{HOOKS}: "retired" is no longer read; remove it. A hook of this'
                " checkout that hooks.json does not declare is removed without it."
            )
        declared = []
        for event, entries in data.items():
            for entry in entries:
                hook: dict[str, object] = {
                    "type": "command",
                    "command": shell_path(REPO_ROOT / entry["command"]),
                }
                if "timeout" in entry:
                    hook["timeout"] = int(entry["timeout"])
                for matcher in entry.get("matchers", [""]):
                    declared.append((str(event), str(matcher), hook))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise InstallError(f"{HOOKS} is malformed: {exc}") from exc
    return declared


def apply_hooks(settings: dict[str, Any], declared: Hooks) -> None:
    """Make this repo's hooks exactly `declared`; keep every hook that is not its own."""
    own = shell_path(REPO_ROOT) + "/"
    events = settings.setdefault("hooks", {})
    wanted = {(event, matcher, hook["command"]) for event, matcher, hook in declared}
    for event, groups in list(events.items()):
        for group in groups:
            group["hooks"] = [
                h
                for h in group.get("hooks", [])
                if not (
                    h.get("command", "").startswith(own)
                    and (event, group.get("matcher", ""), h.get("command"))
                    not in wanted
                )
            ]
        groups[:] = [g for g in groups if g.get("hooks")]
        if not groups:
            del events[event]
    for event, matcher, hook in declared:
        groups = events.setdefault(event, [])
        group = next((g for g in groups if g.get("matcher", "") == matcher), None)
        if group is None:
            group = {"matcher": matcher, "hooks": []}
            groups.append(group)
        inner = group.setdefault("hooks", [])
        current = next((h for h in inner if h.get("command") == hook["command"]), None)
        if current is None:
            inner.append(dict(hook))
        elif current != hook:
            current.clear()
            current.update(hook)
    if not events:
        del settings["hooks"]


def claude_md_text(name: str) -> str | None:
    """CLAUDE_<NAME>.md without its doc-graph frontmatter, which is the repo's own; None
    without a file."""
    template = REPO_ROOT / f"CLAUDE_{name}.md"
    if not template.exists():
        return None
    try:
        text = template.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallError(f"cannot read {template}: {exc}") from exc
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end < 0:
            raise InstallError(f"{template}: frontmatter is never closed")
        text = text[end + 5 :].lstrip("\n")
    return text


def load_settings(path: Path) -> tuple[str, dict[str, Any]]:
    """settings.json's text and its parsed value; ("", {}) without a file."""
    try:
        raw = path.read_text(encoding="utf-8") if path.exists() else ""
        current = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError) as exc:
        raise InstallError(f"cannot read {path}: {exc}") from exc
    if not isinstance(current, dict):
        raise InstallError(f"{path} must be a JSON object")
    return raw, current


def with_import(claude_md: str, line: str) -> str:
    """CLAUDE.md with `line` present exactly once."""
    if line in claude_md.splitlines():
        return claude_md
    return (claude_md.rstrip("\n") + "\n\n" if claude_md.strip() else "") + line + "\n"


def leaves(value: object, path: str = "") -> list[str]:
    """One `path = value` line per leaf of a JSON value, so a diff names only what changes."""
    if isinstance(value, dict) and value:
        return [
            line
            for key, item in value.items()
            for line in leaves(item, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(value, list) and value:
        return [
            line
            for index, item in enumerate(value)
            for line in leaves(item, f"{path}[{index}]")
        ]
    return [f"{path} = {json.dumps(value)}"] if path else []


def with_additions(before: object, after: object) -> object:
    """`before` plus every key `after` adds to a dict; every value `before` holds is kept."""
    if not (isinstance(before, dict) and isinstance(after, dict)):
        return before
    out = {key: with_additions(value, after.get(key)) for key, value in before.items()}
    out.update({key: value for key, value in after.items() if key not in before})
    return out


def changed_lines(before: object, after: object) -> list[str]:
    """`-path = old` and `+path = new` for each setting that differs, with no context."""
    lines = difflib.unified_diff(leaves(before), leaves(after), n=0, lineterm="")
    return [line for line in lines if not line.startswith(("---", "+++", "@@"))]


def confirmed(path: Path) -> bool:
    """True when the person at the terminal answers y or yes."""
    try:
        answer = input(f"Replace {path}? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def write_files(config: Path, name: str, text: str | None, dry_run: bool) -> None:
    """<NAME>.md holding `text`, and its CLAUDE.md import; nothing when `text` is None."""
    if text is None:
        return
    doc = config / f"{name}.md"
    claude_md = config / "CLAUDE.md"
    before_md = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    before_doc = doc.read_text(encoding="utf-8") if doc.exists() else ""
    # The imported file first: CLAUDE.md must never import a file not yet there.
    writes = (
        (doc, before_doc, text),
        (claude_md, before_md, with_import(before_md, f"@{name}.md")),
    )
    for path, before, after in writes:
        if before == after:
            print(f"install: {path} already up to date")
        elif dry_run:
            print(f"install: would write {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(after, encoding="utf-8")
            print(f"install: {'updated' if before else 'created'} {path}")


def write_settings(
    path: Path,
    loaded: tuple[str, dict[str, Any]],
    env: dict[str, str],
    declared: Hooks | None,
    dry_run: bool,
) -> None:
    """Add what is new without asking; replace or remove only on a y at a terminal."""
    raw, current = loaded
    settings = json.loads(raw) if raw.strip() else {}
    if env:
        settings.setdefault("env", {}).update(env)
    if declared is not None:
        apply_hooks(settings, declared)
    added = with_additions(current, settings)
    additions = changed_lines(current, added)
    changes = changed_lines(added, settings)
    target = added
    if not additions and not changes:
        print(f"install: {path} already up to date")
    if additions:
        print("\n".join(additions))
        count = f"{len(additions)} setting(s)"
        print(f"install: {'would add' if dry_run else 'adding'} {count} to {path}")
    if changes:
        print("\n".join(changes))
        if dry_run:
            print(f"install: would ask before replacing {path}")
        elif not (sys.stdin and sys.stdin.isatty()):
            print(
                f"install: {path}: changes not written;"
                " run ./install in a terminal to confirm"
            )
        elif confirmed(path):
            target = settings
        else:
            print(f"install: {path}: changes not written")
    if target != current and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(target, indent=2) + "\n", encoding="utf-8")
        print(f"install: {'updated' if raw else 'created'} {path}")


def main(argv: list[str]) -> int:
    """Write this repo's settings; 0 on success, 2 on refusal."""
    dry_run = argv == ["--dry-run"]
    if argv and not dry_run:
        print(
            f"install: unknown argument {argv[0]!r} (only --dry-run is accepted)",
            file=sys.stderr,
        )
        return 2
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    settings_path = config / "settings.json"
    # Every input is read and checked here, so a refusal comes before any write.
    try:
        name = identifier(repo_name()).upper()
        env = load_defaults(name + "_")
        declared = load_hooks()
        text = claude_md_text(name)
        loaded = load_settings(settings_path)
    except (InstallError, ValueError) as exc:
        print(f"install: {exc}", file=sys.stderr)
        return 2
    write_files(config, name, text, dry_run)
    write_settings(settings_path, loaded, env, declared, dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
