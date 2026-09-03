#!/usr/bin/env python3
"""telemetry_toggle.py — flip a telemetry switch in the repo-local `.telemetry/settings.toml`.

The deterministic backend for `/racecar-telemetry-build` and `/racecar-telemetry-share`. It
writes one boolean into `[telemetry]` in `.telemetry/settings.toml` — the per-developer,
gitignored override that sits between the env var and pyproject in switch resolution (env >
settings.toml > pyproject > on). So a developer opts a checkout in or out without touching the
shared pyproject default or exporting an env var.

    telemetry_toggle.py <build|share|usage> on|off   # set it
    telemetry_toggle.py <build|share|usage>          # print current file value

`share` gates whether the anonymized aggregate may leave the machine; `build` gates local gate-
outcome recording; `usage` gates per-command resource recording. Prints the resulting state.
Pure stdlib; never touches anything but `.telemetry/settings.toml`.

Complexity: O(1)
"""

from __future__ import annotations

import sys
from pathlib import Path

from check_packaging_rules._root import find_repo_root

_KEYS = ("build", "share", "usage")
_SETTINGS_REL = Path(".telemetry") / "settings.toml"
_TRUTHY = {"on", "true", "1", "yes"}
_FALSY = {"off", "false", "0", "no"}


def _ensure_gitignored(directory: Path) -> None:
    """Make the telemetry dir self-ignoring (pytest-`.pytest_cache` pattern), best-effort.

    A `<dir>/.gitignore` of `*` keeps the per-developer settings (and every other telemetry
    file) out of commits regardless of the repo's root `.gitignore`. Idempotent.
    """
    marker = directory / ".gitignore"
    if marker.exists():
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "# Created automatically by racecar telemetry — local-only, never committed.\n*\n",
            encoding="utf-8",
        )
    except OSError:
        pass


class TelemetryToggleError(Exception):
    """The settings file exists but could not be read back as valid TOML."""


def _load(path: Path) -> dict[str, bool]:
    """Read the `[telemetry]` booleans from settings.toml, or `{}` if it does not exist.

    A file that EXISTS but fails to read or parse (corrupt, a hand-edit gone wrong, a
    transient permissions error) is not the same state as "no file yet" -- conflating
    the two is what let a set-one-key write silently drop every other previously-set
    key, with no diagnostic that the existing file was ever corrupt. Raises
    TelemetryToggleError so a caller updating the file refuses rather than clobbering
    the corruption with a truncated version of its own intended content.
    """
    if not path.is_file():
        return {}
    try:
        import tomllib  # pylint: disable=import-outside-toplevel  # stdlib 3.11+
    except ImportError:
        return {}
    try:
        section = tomllib.loads(path.read_text(encoding="utf-8")).get("telemetry", {})
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise TelemetryToggleError(f"{path}: could not be read as TOML: {err}") from err
    return {k: bool(v) for k, v in section.items() if isinstance(v, bool)}


def _write(path: Path, values: dict[str, bool]) -> None:
    """Rewrite settings.toml with the `[telemetry]` table (the only table it holds)."""
    lines = [
        "# racecar telemetry — per-developer, gitignored override.",
        "# Written by /racecar-telemetry-build|share. Resolution: env > this > pyproject > on.",
        "[telemetry]",
    ]
    lines += [f"{key} = {str(values[key]).lower()}" for key in sorted(values)]
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_gitignored(path.parent)  # settings is per-developer, never committed
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    """Parse `<key> [on|off]`, update or report the switch, print the new state."""
    if not argv or argv[0] not in _KEYS:
        print(
            f"usage: telemetry_toggle.py <{'|'.join(_KEYS)}> [on|off]", file=sys.stderr
        )
        return 2
    key = argv[0]
    path = find_repo_root(Path.cwd()) / _SETTINGS_REL
    try:
        values = _load(path)
    except TelemetryToggleError as err:
        print(f"telemetry_toggle: {err}", file=sys.stderr)
        return 2

    if len(argv) == 1:
        state = values[key] if key in values else "unset (default on)"
        print(f"telemetry: {key} = {state} in {_SETTINGS_REL}")
        return 0

    choice = argv[1].strip().lower()
    if choice not in _TRUTHY and choice not in _FALSY:
        print(f"telemetry_toggle: expected on|off, got {argv[1]!r}", file=sys.stderr)
        return 2
    values[key] = choice in _TRUTHY
    _write(path, values)
    print(
        f"telemetry: {key} = {'on' if values[key] else 'off'} (wrote {_SETTINGS_REL})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
