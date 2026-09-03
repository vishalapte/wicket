# sysadmin-hardware/lib/_telemetry.py — OPTIONAL runtime probe. Copy to your
# package's source root as `<pkg>/_telemetry.py` (e.g. src/acme/_telemetry.py)
# to record a resource-usage line per CLI invocation. It is the empirical input
# the racecar-sysadmin-hardware lens consumes; see sysadmin-hardware/TELEMETRY.md
# for the record schema, the enable switch, and the one-line adoption.
#
# This is runtime code the CLI imports, so it must live in the package tree
# (like the optional arch-python/lib/_cli.py renderer), not in ~/.claude
# where a check script lives. Modifying it is a standards-change conversation,
# not a per-project decision. Copy it verbatim.
"""Cheap, stdlib-only resource telemetry for a racecar `python -m <pkg>` CLI.

racecar CLIs dispatch through one `main()` per `__main__.py` (arch-python/
CLI.md). Wrapping that single call records the whole command uniformly, without
any subcommand opting in by hand:

    if __name__ == "__main__":
        from <pkg>._telemetry import run
        run(main)

`run(main)` executes `main()` inside `record()`, which times the process,
reads `resource.getrusage`, and appends one JSON object to the telemetry log.
`record()` is also usable directly as a context manager when the entrypoint
does work around `main()`.

On by default, opt-out: telemetry records unless `RACECAR_USAGE_TELEMETRY` is set falsy,
or `[tool.racecar.telemetry].usage = false` in pyproject.toml (env wins; see TELEMETRY.md
"Enable switch"). Safe either way — the probe never changes the command's behavior or
output and never raises into the command: off is a no-op, on only appends a local,
gitignored record; any telemetry failure is swallowed (surfaced on stderr only when
`RACECAR_TELEMETRY_DEBUG` is truthy).

Storage: append-only JSONL at `$RACECAR_TELEMETRY_DIR/usage.jsonl`, default
`./.telemetry/usage.jsonl` (relative to the process CWD, which for a
`python -m <pkg>` invocation is the repo root). One object per line; see
TELEMETRY.md for the schema. POSIX only (needs `resource`); a graceful no-op
where that module is absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Iterator, cast

try:
    import resource
except ImportError:  # non-POSIX (e.g. Windows): probe degrades to a no-op.
    resource = None  # type: ignore[assignment]

# Schema 2 adds the `work` object (mark() work-scale counters) and the `provenance`
# object (run-time git SHA/dirty, python, host, env fingerprint) — both backward-only
# signals: capturable at run time, lost afterward, useful in accumulation.
SCHEMA_VERSION = 2

_ENABLE_ENV = "RACECAR_USAGE_TELEMETRY"
_DIR_ENV = "RACECAR_TELEMETRY_DIR"
_DEBUG_ENV = "RACECAR_TELEMETRY_DEBUG"
_DEFAULT_DIR = ".telemetry"
_JSONL_NAME = "usage.jsonl"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
# Flags whose value is a worker/concurrency count, if the command exposes one.
_WORKER_FLAGS = ("--workers", "--jobs", "-j")


_CONFIG_SENTINEL: Any = object()
_config_cache: Any = _CONFIG_SENTINEL
_settings_cache: Any = _CONFIG_SENTINEL


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file to a dict, or `{}` on any failure (missing tomllib, bad TOML)."""
    try:
        import tomllib  # pylint: disable=import-outside-toplevel  # stdlib 3.11+
    except ImportError:
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _warn(exc)
        return {}


def _config() -> dict[str, Any]:
    """`[tool.racecar.telemetry]` from the nearest pyproject.toml (memoized), or `{}`.

    The shared per-repo config home for the switches and the log dir. Read once per process,
    walking up from the CWD (the repo root for a `python -m <pkg>` run). Any failure degrades
    to `{}`, i.e. the on-by-default, `.telemetry` defaults. Config must never break a command.
    """
    global _config_cache  # pylint: disable=global-statement
    if _config_cache is not _CONFIG_SENTINEL:
        return cast(dict[str, Any], _config_cache)
    _config_cache = {}
    for base in (Path.cwd(), *Path.cwd().parents):
        pyproject = base / "pyproject.toml"
        if pyproject.is_file():
            section = (
                _read_toml(pyproject)
                .get("tool", {})
                .get("racecar", {})
                .get("telemetry", {})
            )
            if isinstance(section, dict):
                _config_cache = section
            break
    return cast(dict[str, Any], _config_cache)


def _settings() -> dict[str, Any]:
    """`[telemetry]` from the nearest `.telemetry/settings.toml` (memoized), or `{}`.

    The repo-local, gitignored, per-developer override the `/racecar-telemetry-*` toggles
    write. Sits between the env var and pyproject in switch resolution, so a developer opts a
    checkout in or out without touching the shared pyproject default. Any failure degrades
    to `{}`; never escapes the repo (stops at the `.git` root).
    """
    global _settings_cache  # pylint: disable=global-statement
    if _settings_cache is not _CONFIG_SENTINEL:
        return cast(dict[str, Any], _settings_cache)
    _settings_cache = {}
    for base in (Path.cwd(), *Path.cwd().parents):
        settings = base / ".telemetry" / "settings.toml"
        if settings.is_file():
            section = _read_toml(settings).get("telemetry", {})
            if isinstance(section, dict):
                _settings_cache = section
            break
        if (base / ".git").exists():
            break
    return cast(dict[str, Any], _settings_cache)


def _switch(env_name: str, cfg_key: str) -> bool:
    """Resolve a switch: env > `.telemetry/settings.toml` > pyproject > on by default.

    The env var wins when set (truthy = on, any other value = off); else the per-developer
    `.telemetry/settings.toml`; else `[tool.racecar.telemetry].<cfg_key>`; else on. Telemetry
    records by default; a repo or developer opts out deliberately. Neither choice breaks
    anything: off is a no-op, on only appends a local, gitignored record.
    """
    raw = os.environ.get(env_name, "").strip().lower()
    if raw:
        return raw in _TRUTHY
    for source in (_settings(), _config()):
        if cfg_key in source:
            return bool(source[cfg_key])
    return True


def enabled() -> bool:
    """Whether usage telemetry records — on by default; opt out via env or pyproject."""
    return _switch(_ENABLE_ENV, "usage")


def _debug() -> bool:
    return os.environ.get(_DEBUG_ENV, "").strip().lower() in _TRUTHY


def log_path() -> Path:
    """`<dir>/usage.jsonl`: env `RACECAR_TELEMETRY_DIR` > pyproject `dir` > `.telemetry`."""
    root = (
        os.environ.get(_DIR_ENV, "").strip()
        or str(_config().get("dir", "")).strip()
        or _DEFAULT_DIR
    )
    return Path(root) / _JSONL_NAME


def _ensure_gitignored(directory: Path) -> None:
    """Make the telemetry dir self-ignoring, so its contents are never committed.

    Drops a `<dir>/.gitignore` containing `*` (the pytest-`.pytest_cache` pattern), which git
    honors tracked or not — so the log stays out of every commit regardless of the repo's root
    `.gitignore`. Idempotent, best-effort: instrumentation must never surprise, and a failure
    here must never break the command.
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
    except OSError as exc:
        _warn(exc)


_ENV_SENTINEL: Any = object()
_env_cache: Any = _ENV_SENTINEL


def _env_fingerprint() -> str | None:
    """A 12-char hash of the installed distribution set (`name==version`, sorted).

    Computed once per process (memoized) and only when a record is emitted. It names no
    dependency; it changes iff the resolved environment changes — the backward-only
    signal that correlates a resource shift to a dependency upgrade, without bloating
    every record with the full list. None if the set cannot be read.
    """
    global _env_cache  # pylint: disable=global-statement
    if _env_cache is not _ENV_SENTINEL:
        return cast("str | None", _env_cache)
    try:
        dists = sorted(
            f"{dist.metadata['Name']}=={dist.version}"
            for dist in metadata.distributions()
            if dist.metadata and dist.metadata["Name"]
        )
        _env_cache = hashlib.sha256("\n".join(dists).encode()).hexdigest()[:12]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _warn(exc)
        _env_cache = None
    return cast("str | None", _env_cache)


def _git(args: list[str]) -> str | None:
    """Stripped stdout of `git <args>` in the process CWD, or None on any failure."""
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _git_provenance() -> tuple[str | None, bool | None]:
    """`(short SHA, dirty)` of HEAD in the CWD, or `(None, None)` off a git tree.

    Called AFTER the resource snapshots (see `_record`) so the git subprocesses are
    never counted in the command's own CPU/children usage — provenance costs the log,
    not the measurement.
    """
    sha = _git(["rev-parse", "--short", "HEAD"])
    if sha is None:
        return None, None
    status = _git(["status", "--porcelain"])
    return sha, (bool(status) if status is not None else None)


def _main_package() -> str | None:
    """The dotted package of the running `python -m <pkg>` entrypoint.

    For `python -m acme.engine`, the `__main__` module carries
    `__package__ == "acme.engine"`; fall back to its `__spec__.parent`. Returns
    None when the process was not launched as a package module.
    """
    main_mod = sys.modules.get("__main__")
    pkg = getattr(main_mod, "__package__", None)
    if pkg:
        return cast(str, pkg)
    spec = getattr(main_mod, "__spec__", None)
    parent = getattr(spec, "parent", None)
    return parent or None


_REDACTED = "<redacted>"
# Flag NAMES whose value is a secret to mask (matched case-insensitively, as a substring
# so `--api-key` / `--db-password` / `--auth-token` all hit).
_SECRET_FLAG_RE = re.compile(
    r"(?i)(pass(?:word|wd)?|secret|token|api[-_]?key|access[-_]?key|auth|credential"
    r"|private[-_]?key)"
)
# Standalone token SHAPES that are secrets wherever they appear in argv.
_SECRET_VALUE_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"  # JWT
    r"|(?:sk|rk|ghp|gho|ghu|ghs|ghr|xox[baprs])[-_][A-Za-z0-9_-]{10,}"  # prefixed API keys
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|A(?:KIA|SIA)[0-9A-Z]{16}"  # AWS access key id
    r"|-----BEGIN[ A-Z]+PRIVATE KEY-----"  # PEM header
)
# A URL with inline credentials — mask the password, keep scheme/user/host for grouping.
_URL_CRED_RE = re.compile(r"(?P<pre>://[^/\s:@]+):[^/\s:@]+@")


def _redact_argv(argv: Sequence[str]) -> list[str]:
    """Mask secret-shaped tokens in argv before anything downstream reads it.

    Telemetry records by default and the log is local + gitignored, but it must never become a
    credential store — a `--token ghp_…` run would otherwise write the secret verbatim,
    and worse leak it into `subcommand`/`command` (the first positional after a
    value-taking flag). Masking here, once, keeps every derived field clean. Conservative
    by design: it masks the value after a secret-named flag (`--token X`, `--pw=X`), any
    standalone token whose shape is a known secret (JWT, `ghp_`/`sk-`/`AKIA…`, PEM), and
    the password inside a URL; paths and ordinary arguments pass through unchanged.
    """
    out: list[str] = []
    mask_next = False
    for token in argv:
        if mask_next:
            out.append(_REDACTED)
            mask_next = False
        elif (
            token.startswith("-")
            and "=" in token
            and _SECRET_FLAG_RE.search(token.partition("=")[0])
        ):
            out.append(f"{token.partition('=')[0]}={_REDACTED}")
        elif token.startswith("-") and _SECRET_FLAG_RE.search(token):
            out.append(token)
            mask_next = True
        elif _SECRET_VALUE_RE.search(token):
            out.append(_REDACTED)
        else:
            out.append(_URL_CRED_RE.sub(rf"\g<pre>:{_REDACTED}@", token))
    return out


def _subcommand(argv: Sequence[str]) -> str | None:
    """First positional token in argv (the argparse subcommand), if any."""
    for token in argv:
        if not token.startswith("-"):
            return token
    return None


def _workers(argv: Sequence[str]) -> int | None:
    """Worker/concurrency count parsed from argv, if the command exposes one.

    Handles both `--workers N` and `--workers=N` (and the `-j` / `--jobs`
    spellings). Returns None when no such flag is present or its value is not an
    integer.
    """
    for i, token in enumerate(argv):
        for flag in _WORKER_FLAGS:
            if token == flag and i + 1 < len(argv):
                return _as_int(argv[i + 1])
            if token.startswith(flag + "="):
                return _as_int(token[len(flag) + 1 :])
    return None


def _as_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _peak_rss_bytes(usage_self: Any, usage_children: Any) -> int:
    """Peak resident-set size in bytes across the process and its children.

    `ru_maxrss` is a high-water mark, not a delta: bytes on macOS, kibibytes on
    Linux. Take the larger of the two high-water marks (the process itself, and
    the largest single child) so a thread-pool workload reports the parent peak
    and a process-pool workload reports its heaviest child.
    """
    scale = 1 if sys.platform == "darwin" else 1024
    return int(max(usage_self.ru_maxrss, usage_children.ru_maxrss) * scale)


def _exit_code(code: object) -> int:
    """Normalize a `SystemExit` code to an integer status (None -> 0, str -> 1)."""
    if code is None:
        return 0
    if isinstance(code, bool):
        return int(code)
    if isinstance(code, int):
        return code
    return 1


class _Probe:
    """One in-flight measurement: start marks captured, finish emits the record."""

    def __init__(self, argv: Sequence[str]) -> None:
        # Redact once, up front, so every derived field (argv, subcommand, command,
        # workers) reads the masked vector and no secret can reach the log.
        self.argv = _redact_argv(argv)
        # Work-scale counters attached by mark() during the run (rows, bytes, files…).
        self.work: dict[str, float] = {}
        self.package = _main_package()
        self.wall_start = time.monotonic()
        self.ts_start = datetime.now(timezone.utc)
        self.rusage_self_start = resource.getrusage(resource.RUSAGE_SELF)
        self.rusage_children_start = resource.getrusage(resource.RUSAGE_CHILDREN)

    def _record(self, status: int) -> dict[str, Any]:
        end_self = resource.getrusage(resource.RUSAGE_SELF)
        end_children = resource.getrusage(resource.RUSAGE_CHILDREN)
        ts_end = datetime.now(timezone.utc)
        wall = time.monotonic() - self.wall_start

        cpu_user = (end_self.ru_utime - self.rusage_self_start.ru_utime) + (
            end_children.ru_utime - self.rusage_children_start.ru_utime
        )
        cpu_sys = (end_self.ru_stime - self.rusage_self_start.ru_stime) + (
            end_children.ru_stime - self.rusage_children_start.ru_stime
        )
        io_read = (end_self.ru_inblock - self.rusage_self_start.ru_inblock) + (
            end_children.ru_inblock - self.rusage_children_start.ru_inblock
        )
        io_write = (end_self.ru_oublock - self.rusage_self_start.ru_oublock) + (
            end_children.ru_oublock - self.rusage_children_start.ru_oublock
        )

        subcommand = _subcommand(self.argv)
        command = f"python -m {self.package}" if self.package else "python -m ?"
        if subcommand:
            command = f"{command} {subcommand}"

        cpu_total = cpu_user + cpu_sys
        # Mean parallelism actually exercised: CPU-seconds burned per wall-second.
        # ~1 is serial; ~N means N cores kept busy on average. The single point
        # value that says how many vCPUs a run truly used, independent of the
        # worker count it was launched with.
        cores_used = round(cpu_total / wall, 2) if wall > 0 else 0.0

        # Provenance is gathered HERE — after every rusage/timing snapshot above — so
        # the git subprocesses and the dist scan never count against the command's own
        # numbers. It attributes a run to the exact code+environment that produced it,
        # which the repo's current state cannot reconstruct after the fact.
        git_sha, git_dirty = _git_provenance()
        provenance = {
            "git_sha": git_sha,
            "git_dirty": git_dirty,
            "python": platform.python_version(),
            "host": platform.node() or None,
            "venv": sys.prefix,
            "env_fingerprint": _env_fingerprint(),
        }

        return {
            "schema": SCHEMA_VERSION,
            "ts_start": self.ts_start.isoformat().replace("+00:00", "Z"),
            "ts_end": ts_end.isoformat().replace("+00:00", "Z"),
            "command": command,
            "module": self.package,
            "subcommand": subcommand,
            "argv": self.argv,
            "wall_s": round(wall, 4),
            "cpu_user_s": round(cpu_user, 4),
            "cpu_sys_s": round(cpu_sys, 4),
            "cpu_total_s": round(cpu_total, 4),
            "cores_used": cores_used,
            "peak_rss_mb": round(
                _peak_rss_bytes(end_self, end_children) / 1_048_576, 2
            ),
            "io_read_blocks": max(io_read, 0),
            "io_write_blocks": max(io_write, 0),
            "workers": _workers(self.argv),
            "cpu_count": os.cpu_count(),
            "exit_status": status,
            "pid": os.getpid(),
            "platform": sys.platform,
            "work": dict(self.work),
            "provenance": provenance,
        }

    def finish(self, status: int) -> None:
        """Emit the resource record for this run to the append-only JSONL log."""
        payload = self._record(status)
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _ensure_gitignored(path.parent)  # the log must never be committable
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        # A single write() of a sub-PIPE_BUF line under O_APPEND is atomic on
        # POSIX, so concurrent `python -m` processes never interleave lines.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


# The in-flight probe, so mark() can reach the record being built. Set while a measured
# block runs (record()), None otherwise — which is why mark() is a safe no-op when
# telemetry is off or the command runs unwrapped.
_ACTIVE: _Probe | None = None


def mark(**counters: float) -> None:
    """Attach work-scale counters (rows, bytes, files…) to the current run's record.

    A no-op when telemetry is off or no run is being measured, so a command can call it
    unconditionally. Numeric values ACCUMULATE across calls — mark each batch — and land
    under the record's `work` object, turning every resource number into a rate (resource
    per unit of work), the metric that makes sizing predictive. Never raises: a bad mark
    must not break the command.
    """
    probe = _ACTIVE
    if probe is None:
        return
    try:
        for key, value in counters.items():
            probe.work[key] = probe.work.get(key, 0) + value
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _warn(exc)


@contextmanager
def record(argv: Sequence[str] | None = None) -> Iterator[None]:
    """Measure the wrapped block and append one telemetry record on exit.

    A no-op (zero added cost beyond an env lookup) unless `RACECAR_USAGE_TELEMETRY` is
    set and `resource` is importable. Records the exit status: 0 on clean
    return, the `SystemExit` code when the block exits via `sys.exit`, 1 on any
    other exception. The original exit or exception always propagates unchanged;
    telemetry never alters control flow, and a failure inside the probe is
    swallowed (shown on stderr only under `RACECAR_TELEMETRY_DEBUG`).
    """
    if not enabled() or resource is None:
        yield
        return

    global _ACTIVE  # pylint: disable=global-statement
    try:
        probe: _Probe | None = _Probe(sys.argv[1:] if argv is None else argv)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _warn(exc)
        probe = None

    _ACTIVE = probe  # expose the in-flight record to mark()
    try:
        yield
    except SystemExit as exc:
        _safe_finish(probe, _exit_code(exc.code))
        raise
    except BaseException:
        _safe_finish(probe, 1)
        raise
    finally:
        _ACTIVE = None
    # Reached only on a clean return; the except branches above re-raise.
    _safe_finish(probe, 0)


def run(main: Callable[[], Any], argv: Sequence[str] | None = None) -> None:
    """Run `main()` under `record()`, propagating its return as the process exit code.

    The one-line adoption at a CLI entrypoint: `run(main)` is `sys.exit(main())` with
    measurement, so replacing a compliant `raise SystemExit(main())` / `sys.exit(main())`
    guard with it is behavior-preserving. `main()` runs inside the measured block; its return
    (or an explicit `sys.exit`) becomes the exit code — recorded by `record()` and re-raised,
    never swallowed. (The earlier form discarded `main()`'s return, forcing exit 0 — wrong for
    every entrypoint that returns its code.)
    """
    with record(argv=argv):
        raise SystemExit(main())


def _safe_finish(probe: _Probe | None, status: int) -> None:
    if probe is None:
        return
    try:
        probe.finish(status)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _warn(exc)


def _warn(exc: BaseException) -> None:
    if _debug():
        sys.stderr.write(f"[racecar-telemetry] suppressed: {exc!r}\n")
