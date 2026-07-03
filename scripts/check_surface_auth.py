#!/usr/bin/env python3
"""check_surface_auth.py — enforce the auth rail (AUTH.md) on a generated surface.

A racecar surface is closed by default: it must carry the auth gate, and every exposed command must
declare a scope. This check fails when either is missing. It bites before the rail is implemented (a
surface with no auth fails it), which is the canon-first point: "closed by default" is a mechanical
fact, not a hope. Run from the project root; a project with no server is a no-op.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _server_root(repo: Path) -> Path | None:
    """The server dir if this project has a generated surface, else None."""
    cand = repo / "server"
    return cand if (cand / "docs" / "api" / "manifest.json").exists() else None


def findings(repo: Path) -> list[str]:
    """Return one message per auth-rail violation in the project's server surface."""
    server = _server_root(repo)
    if server is None:
        return []  # no surface to check
    out: list[str] = []
    # 1. Closed by default: the auth gate module must exist (the surface is not anonymous).
    if not (server / "project" / "auth.py").exists():
        out.append(
            "server ships without the auth gate (project/auth.py missing): the surface is "
            "anonymous, not closed by default (AUTH.md)"
        )
    # 2. Default-deny: every exposed command must declare a non-empty scope.
    manifest = json.loads((server / "docs" / "api" / "manifest.json").read_text(encoding="utf-8"))
    for vertical in manifest.get("verticals", []):
        for command in vertical.get("commands", []):
            if not command.get("scope"):
                out.append(
                    f"command {vertical['vertical']}.{command['subcommand']} has no scope: "
                    f"default-deny requires every command to declare one (AUTH.md)"
                )
    return out


def main() -> int:
    """CLI: 0 when the surface is closed-by-default with every command scoped, 1 otherwise."""
    problems = findings(Path.cwd())
    if not problems:
        print("check_surface_auth: OK")
        return 0
    for problem in problems:
        print(f"check_surface_auth: {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
