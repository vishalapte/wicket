#!/usr/bin/env python3
"""record_gate.py — DEVELOPER telemetry: append one gate-outcome record per run.

This is developer telemetry, deliberately kept separate from the *usage* telemetry
that `sysadmin-hardware/lib/_telemetry.py` collects. Usage telemetry is ambient — it
measures what a command costs when a user runs it (resources), behind
`RACECAR_USAGE_TELEMETRY`, stored in `.telemetry/usage.jsonl`. This measures the
maintainer's own signal — whether the gate is trending green — recorded when *you* run
the gate, behind its own switch `RACECAR_BUILD_TELEMETRY`, stored in
`.telemetry/build.jsonl`. Both are on by default (opt-out) and configured under
`[tool.racecar.telemetry]`; the two never share a switch or a file.

A transparent wrapper: ``record_gate.py <label> [--] <command...>`` runs the command,
streams its output through, records the outcome keyed to the current git commit, and
exits with the command's own exit code — so it can stand in for the raw command
anywhere (``record_gate.py check make check``).

Why it exists. The deterministic checkers answer "what is true now"; none answer "which
way is it moving," because trajectory requires a record of the past. Accumulated over
time this ledger is exactly that — the DRIFT trajectory ([`shared/DRIFT.md`](../shared/DRIFT.md)):
which checkers fire, how finding counts move commit-to-commit, whether the gate trends
green. It is the backward-only signal the static checks structurally cannot show.

On by default, opt-out: it records unless the ``build`` switch is off — resolved
``RACECAR_BUILD_TELEMETRY`` (env) > ``.telemetry/settings.toml`` (the repo-local, gitignored
per-developer override the ``/racecar-telemetry-build`` toggle writes) > ``[tool.racecar
.telemetry].build`` in pyproject.toml > on. Disabled, it is a pure passthrough. One JSON
object per run, appended to ``$RACECAR_TELEMETRY_DIR/build.jsonl`` (default
``.telemetry/build.jsonl``, gitignored):

    {schema, id, ts, git_sha, git_dirty, branch, racecar_version, label, command, ok,
     exit_code, wall_s, total_findings, checkers: {<name>: {ok, findings}}, pushed}

``racecar_version`` is the canon stamp (``scripts/.racecar-version``) in force when the gate
ran, so a later harvest can attribute a findings shift to the exact racecar version.

``pushed`` is ``false`` at write time and ``id`` is a stable per-record key. The transport
(a shared telemetry sink; the sink is a deferred decision) is not built yet — collection runs
now, sending later. When it lands, the push sends only ``pushed == false`` records
(anonymized at source via the harvest's ``anonymize()``), then flips them to ``true`` keyed on
``id`` — so the local log always shows, git-style, which records have gone to the fleet and
which have not.

``ok`` / ``exit_code`` are authoritative. ``checkers`` / ``total_findings`` are
best-effort, parsed from racecar's checker summary convention (``<name>: OK`` /
``<name>: N errors``); a gate whose output does not follow it still records its exit.

Usage:
    RACECAR_BUILD_TELEMETRY=1 python scripts/record_gate.py check -- make check
    RACECAR_BUILD_TELEMETRY=1 python scripts/record_gate.py arch  -- make arch

Complexity: O(n) per call (n = wrapped command's output lines, scanned once in
parse_checkers); O(1) against accumulated ledger history, since writes are append-only
and never re-read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

SCHEMA = 2
_TRUTHY = frozenset({"1", "true", "yes", "on"})
# A racecar checker summary line: `<name>: OK` (pass) or `<name>: N errors` / a message.
_SUMMARY_RE = re.compile(r"^(?P<name>[a-z][a-z0-9_]*): (?P<rest>\S.*)$")
_ERRORS_RE = re.compile(r"\b(\d+)\s+errors?\b")


class CheckerEntry(TypedDict):
    """One checker's verdict in a gate record: did it pass, and how many findings.

    A fixed two-key shape, so it is declared rather than left as `dict[str, object]` --
    which would make `sum(entry["findings"])` an `int(object)` call at the one place the
    ledger's headline number is computed.
    """

    ok: bool
    findings: int


def _load_toml(path: Path) -> dict[str, object]:
    """Parse a TOML file to a dict, or `{}` on any failure (missing tomllib, bad TOML)."""
    try:
        import tomllib  # pylint: disable=import-outside-toplevel  # stdlib 3.11+
    except ImportError:
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:  # pylint: disable=broad-exception-caught
        return {}


def _subtable(data: dict[str, object], *keys: str) -> dict[str, object]:
    """Walk nested TOML tables by key; `{}` at the first key that is missing or not a table.

    TOML parses to `dict[str, object]`, so a chained `.get("a", {}).get("b", {})` is asking
    `object` for `.get`. One home for the walk, and it keeps the degrade-to-`{}` contract
    both callers rely on: config must never break the gate.
    """
    cursor = data
    for key in keys:
        nxt = cursor.get(key)
        if not isinstance(nxt, dict):
            return {}
        cursor = nxt
    return cursor


_config_cache: dict[str, object] | None = None  # pylint: disable=invalid-name
_settings_cache: dict[str, object] | None = None  # pylint: disable=invalid-name


def _config() -> dict[str, object]:
    """`[tool.racecar.telemetry]` from the nearest pyproject.toml (memoized), or `{}`.

    The shared per-repo config home for the switches and the log dir; read once, walking up
    from the CWD. Any failure degrades to `{}` — the on-by-default, `.telemetry` defaults.
    Config must never break the gate.
    """
    global _config_cache  # pylint: disable=global-statement
    if _config_cache is not None:
        return _config_cache
    resolved: dict[str, object] = {}
    for base in (Path.cwd(), *Path.cwd().parents):
        pyproject = base / "pyproject.toml"
        if pyproject.is_file():
            resolved = _subtable(_load_toml(pyproject), "tool", "racecar", "telemetry")
            break
    _config_cache = resolved
    return resolved


def _settings() -> dict[str, object]:
    """`[telemetry]` from the nearest `.telemetry/settings.toml` (memoized), or `{}`.

    The repo-local, gitignored, per-developer override that the `/racecar-telemetry-build`
    and `/racecar-telemetry-share` toggles write. Sits between the env var and pyproject in
    the switch resolution, so a developer opts a checkout in or out without touching the
    shared pyproject default. Any failure degrades to `{}`.
    """
    global _settings_cache  # pylint: disable=global-statement
    if _settings_cache is not None:
        return _settings_cache
    resolved: dict[str, object] = {}
    for base in (Path.cwd(), *Path.cwd().parents):
        settings = base / ".telemetry" / "settings.toml"
        if settings.is_file():
            resolved = _subtable(_load_toml(settings), "telemetry")
            break
        if (base / ".git").exists():
            break  # don't escape the repo looking for a per-repo file
    _settings_cache = resolved
    return resolved


def _switch(env_name: str, cfg_key: str) -> bool:
    """Resolve a switch: env > `.telemetry/settings.toml` > pyproject > on by default."""
    raw = os.environ.get(env_name, "").strip().lower()
    if raw:
        return raw in _TRUTHY
    for source in (_settings(), _config()):
        if cfg_key in source:
            return bool(source[cfg_key])
    return True


def _enabled() -> bool:
    """Whether the gate ledger records — on by default; opt out via env or pyproject."""
    return _switch("RACECAR_BUILD_TELEMETRY", "build")


def _log_path() -> Path:
    root = (
        os.environ.get("RACECAR_TELEMETRY_DIR", "").strip()
        or str(_config().get("dir", "")).strip()
        or ".telemetry"
    )
    return Path(root) / "build.jsonl"


def ensure_gitignored(directory: Path) -> None:
    """Make the telemetry dir self-ignoring, so its contents are never committed.

    Drops a `<dir>/.gitignore` containing `*` (the pytest-`.pytest_cache` pattern), which git
    honors whether or not it is tracked — so the local ledger stays out of every commit
    regardless of the repo's root `.gitignore`. Idempotent and best-effort: a telemetry file
    must never be committable, but failing to write the marker must never fail the gate.
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


def _git(args: list[str]) -> str | None:
    """Stripped stdout of `git <args>`, or None on any failure."""
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if not out.returncode else None


def _git_context() -> dict[str, object]:
    sha = _git(["rev-parse", "--short", "HEAD"])
    if sha is None:
        return {"git_sha": None, "git_dirty": None, "branch": None}
    status = _git(["status", "--porcelain"])
    return {
        "git_sha": sha,
        "git_dirty": bool(status) if status is not None else None,
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]),
    }


def _racecar_version() -> str | None:
    """The canon stamp (`scripts/.racecar-version`) in force, or None if unstamped.

    `sync_scripts.py` writes this into every governed repo (racecar's short SHA); recording
    it per gate lets a later harvest attribute a findings shift to the exact racecar version.
    """
    root = _git(["rev-parse", "--show-toplevel"])
    if not root:
        return None
    try:
        return (Path(root) / "scripts" / ".racecar-version").read_text(
            encoding="utf-8"
        ).strip() or None
    except OSError:
        return None


def run_streamed(command: list[str]) -> tuple[int, list[str], float]:
    """Run `command`, echo its merged output live, and return (exit, lines, wall_s)."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as exc:
        print(f"record_gate: cannot run {command!r}: {exc}", file=sys.stderr)
        return 127, [], time.monotonic() - start
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        # CPython block-buffers stdout whenever it is not a TTY -- exactly this tool's
        # deployment context (CI logs, `tee`, another wrapper). Without the explicit
        # flush, every line the child prints arrives only after it exits, not as it
        # produces them, which is the opposite of what "streamed" documents.
        sys.stdout.write(line)
        sys.stdout.flush()
        lines.append(line)
    proc.wait()
    # POSIX reports a signaled child as a NEGATIVE returncode (e.g. -9 for SIGKILL).
    # Passed straight to sys.exit(), that computes exit_code % 256 = 247 -- not the
    # standard 137 (128+9) a shell sees running the same command directly. Translate
    # here, once, so the ledger and the wrapper's own exit code both match what running
    # `command` without this wrapper would have shown.
    code = proc.returncode
    if code < 0:
        code = 128 - code
    return code, lines, round(time.monotonic() - start, 3)


def parse_checkers(lines: list[str]) -> tuple[dict[str, CheckerEntry], int]:
    """Best-effort per-checker outcomes from summary lines, and the total finding count.

    A `<name>: OK …` line is a pass (0 findings); a `<name>: N errors` line contributes
    N; any other non-OK summary counts as at least one finding. Later lines win, so a
    checker's final summary is what lands (checkers print progress then a verdict).
    """
    checkers: dict[str, CheckerEntry] = {}
    for raw in lines:
        match = _SUMMARY_RE.match(raw.strip())
        if not match:
            continue
        name, rest = match.group("name"), match.group("rest")
        if rest.startswith("OK"):
            checkers[name] = {"ok": True, "findings": 0}
            continue
        counted = _ERRORS_RE.search(rest)
        # Only treat a line as a verdict when it counts errors or is a known status;
        # this avoids miscounting an informational `name: note` line as a failure.
        if counted:
            checkers[name] = {"ok": False, "findings": int(counted.group(1))}
    total = sum(entry["findings"] for entry in checkers.values())
    return checkers, total


def record(label: str, command: list[str]) -> int:
    """Run the gate, write one ledger record (when enabled), return the gate's exit."""
    exit_code, lines, wall_s = run_streamed(command)
    if not _enabled():
        return exit_code
    checkers, total = parse_checkers(lines)
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "id": os.urandom(8).hex(),
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **_git_context(),
        "racecar_version": _racecar_version(),
        "label": label,
        "command": command,
        "ok": not exit_code,
        "exit_code": exit_code,
        "wall_s": wall_s,
        "total_findings": total,
        "checkers": checkers,
        "pushed": False,
    }
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        ensure_gitignored(path.parent)  # the ledger must never be committable
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError as exc:  # a ledger failure must never fail the gate
        print(f"record_gate: could not write ledger: {exc}", file=sys.stderr)
    return exit_code


def main(argv: list[str]) -> int:
    """Parse `<label> [--] <command...>`, run it recorded, return its exit code."""
    if not argv:
        print("usage: record_gate.py <label> [--] <command...>", file=sys.stderr)
        return 2
    label, command = argv[0], argv[1:]
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("record_gate: no command given", file=sys.stderr)
        return 2
    return record(label, command)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
