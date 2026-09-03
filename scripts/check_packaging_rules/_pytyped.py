"""PEP 561: a typed library must ship the marker that says so."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import _rel_for_audit
from ._findings import Finding

_MARKER = "py.typed"


def _package_dirs(
    root: Path, lib_data: dict[str, Any] | None, source_root: str | None
) -> list[Path]:
    """The importable package directories this project publishes.

    Read from `[tool.setuptools.packages.find].where`, which is optional: nothing in
    this audit requires it and setuptools auto-discovers a src-layout without it. When
    it is absent the answer is the SHAPE's packaged root, never a guess — `where` is
    always `SRC` (PACKAGING.md §"Consuming the shape"), and the only shapes that reach
    this check are `src`, `src+server` and `server`, whose roots are `src`, `src` and
    `server`.

    The repo root is deliberately not a fallback. It is not a packaged layout in any
    shape canon recognizes (PACKAGING.md "Scope" refuses pyproject-at-package-root
    layouts other than `src/`), so scanning it cannot produce a right answer — only
    wrong ones, by reporting every root directory that happens to be importable. A
    `tests/` package is the common case, and the finding then asks the owner to ship a
    PEP 561 marker from a package no consumer imports.

    A package is a directory with an `__init__.py`; namespace directories (`src`
    itself) are not.
    """
    setuptools = (lib_data or {}).get("tool", {}).get("setuptools", {})
    find = setuptools.get("packages", {}).get("find", {})
    roots = find.get("where") or ([source_root] if source_root else [])
    out: list[Path] = []
    for where in roots:
        base = root / where
        if not base.is_dir():
            continue
        out += [d for d in sorted(base.iterdir()) if (d / "__init__.py").is_file()]
    return out


def _ships_marker(lib_data: dict[str, Any] | None) -> bool:
    """True when the build backend is told to include `py.typed` in the wheel.

    Two forms satisfy it. The explicit one is `[tool.setuptools.package-data]` naming
    the marker under any key (`"*"` or the package name). The implicit one is
    `include-package-data = true`, where setuptools takes the file set from the SCM or
    `MANIFEST.in` — looser, but it genuinely ships the marker, and a checker that
    refused it would be prescribing a mechanism rather than the outcome.
    """
    setuptools = (lib_data or {}).get("tool", {}).get("setuptools", {})
    if setuptools.get("include-package-data") is True:
        return True
    for patterns in (setuptools.get("package-data") or {}).values():
        if isinstance(patterns, list) and any(
            isinstance(p, str) and p.strip() == _MARKER for p in patterns
        ):
            return True
    return False


def check_py_typed(
    root: Path,
    lib_data: dict[str, Any] | None,
    lib_label: str,
    source_root: str | None = None,
) -> list[Finding]:
    """A published library declares its own types with a `py.typed` marker (PEP 561).

    Without it a fully annotated library is not merely half-typed to its consumers, it
    is INVISIBLE: mypy reports `import-untyped` at the boundary and degrades every value
    crossing it to `Any`, silently. The marker is what makes annotation work travel past
    the repo that wrote it. This is the mirror of the type-stub rule in §6 — that one is
    about consuming a dependency whose types are missing; this one is about not being
    that dependency.

    Two halves, because either alone is inert: the marker must exist beside the package,
    and the build backend must be told to ship it, or it stays in the tree and never
    reaches the wheel.

    Contingent on `[project]`. A repo with no `[project]` table publishes nothing (a
    docs tree, a standards framework, a scripts collection), so there is no consumer to
    be invisible to and the rule does not apply.

    Both are Findings, not Blockers. Declaring types is a commitment to their stability,
    and whether to make it is the owner's call (../shared/OWNERSHIP.md); racecar names
    the cost of not making it rather than refusing to pass. The second finding is the
    sharper one — a marker that exists but does not ship is a stated intent the build
    silently drops.
    """
    project = (lib_data or {}).get("project")
    if not isinstance(project, dict):
        return []

    packages = _package_dirs(root, lib_data, source_root)
    if not packages:
        return []

    findings: list[Finding] = []
    missing = [p for p in packages if not (p / _MARKER).is_file()]
    for pkg in missing:
        findings.append(
            Finding(
                "Finding",
                _rel_for_audit(root, pkg / _MARKER),
                "missing-py-typed",
                "PEP 561: add an empty py.typed so this library's annotations are "
                "visible to anything that imports it as an installed package.",
            )
        )

    if len(missing) < len(packages) and not _ships_marker(lib_data):
        findings.append(
            Finding(
                "Finding",
                lib_label,
                "py-typed-not-shipped",
                "py.typed exists but nothing ships it; add [tool.setuptools.package-data] "
                '"*" = ["py.typed"] or include-package-data = true, or the marker never '
                "reaches the wheel.",
            )
        )
    return findings
