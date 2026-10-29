"""Makefile (and its included racecar.mk) validation against the §7 contract."""

from __future__ import annotations

import re
from pathlib import Path

from ..shared._constants import (
    FORBIDDEN_MAKEFILE_TOOLS,
    REQUIRED_MAKEFILE_TARGETS,
    RETIRED_MAKE_VARS,
)
from ._findings import Finding

_MAKEFILE_TARGET_RE = re.compile(r"^([a-zA-Z_][\w-]*)\s*:", re.MULTILINE)


#: How a repo gets racecar.mk: the verb `make racecar-sync` runs, usable without it.
SYNC = "`python -m racecar sync --dest .`"


def _declarations(text: str, target: str) -> list[re.Match[str]]:
    """Every declaration of a target, in file order.

    A target may be declared in both `racecar.mk` and the owned `Makefile`.
    """
    return list(
        re.finditer(
            rf"^{re.escape(target)}\s*:(?!=)([^\n]*)\n((?:[\t][^\n]*\n?)*)",
            text,
            re.MULTILINE,
        )
    )


def _target_prereqs(text: str, target: str) -> set[str]:
    """The union of a target's prerequisites across every declaration, as make reads it."""
    found: set[str] = set()
    for match in _declarations(text, target):
        found.update(match.group(1).split("##", 1)[0].split())
    return found


def _target_body(text: str, target: str) -> str:
    """Return the recipe lines (tab-indented) for a named target, or '' if absent.

    The first declaration that carries a recipe; a prerequisite-only declaration is skipped.
    """
    for match in _declarations(text, target):
        if match.group(2).strip():
            return match.group(2)
    return ""


def _unwrap_target(text: str, target: str) -> str:
    """The target that holds the gate's body: the private `_<target>` when the public one
    names it as a prerequisite, else the public one.

    The canonical `check`, `check-full` and `arch` each name their private `_<target>`
    (`check: _check`), which holds the real prerequisites and recipe.
    """
    if f"_{target}" in _target_prereqs(text, target):
        return "_" + target
    return target


def _resolve_makefile_text(
    root: Path, mk_text: str
) -> tuple[str | None, list[Finding]]:
    """Combine the owned Makefile with racecar.mk for validation.

    The Makefile fold (PACKAGING.md §7): canonical targets live in racecar.mk, which
    the owned Makefile includes. Returns (combined_text, findings). combined_text is
    None when validation should stop at a Blocker that supersedes per-target checks.

    Four states, keyed on whether racecar.mk exists and whether the Makefile includes it:

    - included + present  -> combine and validate the union.
    - included + absent    -> Blocker: the include resolves to nothing; `SYNC`.
    - not included + present -> Blocker: racecar.mk is inert while a monolithic
      Makefile drives the build.
    - not included + absent -> Finding: predates the fold; `SYNC`.

    `SYNC` is the command `make racecar-sync` runs; the target itself lives in the
    missing racecar.mk.
    """
    rcmk = root / "racecar.mk"
    includes_rcmk = bool(
        re.search(r"^\s*-?include\s+racecar\.mk\b", mk_text, re.MULTILINE)
    )
    if includes_rcmk:
        if rcmk.exists():
            try:
                rcmk_text = rcmk.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                return None, [
                    Finding("Blocker", "racecar.mk", "encoding-error", str(exc))
                ]
            return mk_text + "\n" + rcmk_text, []
        return None, [
            Finding(
                "Blocker",
                "racecar.mk",
                "missing-file",
                f"Makefile includes racecar.mk but it is absent; run {SYNC} to "
                "regenerate it",
            )
        ]
    if rcmk.exists():
        return None, [
            Finding(
                "Blocker",
                "Makefile",
                "racecar-mk-not-included",
                "racecar.mk is present but the Makefile does not `include` it; the "
                "canonical build is inert and the monolithic Makefile still drives the "
                "build. Add `include racecar.mk` and remove the canonical recipes from "
                "the Makefile, keeping only project-specific targets (PACKAGING.md §7)",
            )
        ]
    return mk_text, [
        Finding(
            "Finding",
            "Makefile",
            "no-racecar-mk",
            f"no racecar.mk: this repo predates the Makefile fold; run {SYNC} "
            "to adopt it (PACKAGING.md §7)",
        )
    ]


# Recipe lines allowed to name a venv directory outright: `venv` creates one, `distclean`
# removes it. Everything else resolves through $(RC_PYTHON) / $(RC_PIP) / $(RC_BIN).
_VENV_LITERAL_TARGETS = frozenset({"venv", ".venv", "distclean", "install-dev"})
# Matches a tool resolved through a venv's `bin/`, not any mention of a venv directory
# (e.g. `-not -path '*/venv/*'` in a `find` is fine).
_VENV_LITERAL = re.compile(r"(?:\.\./)*\.?venv/bin/")


def _hardcoded_venv_paths(text: str) -> list[Finding]:
    """Report a recipe that names a venv directory where `$(RC_BIN)` would resolve it.

    `racecar.mk` detects `.venv` / `venv` / `../venv` into `$(RC_VENV)` and `$(RC_BIN)`; a literal
    breaks the other layouts and the no-venv `$(HOME)/.local/bin` branch. A Finding.
    """
    findings: list[Finding] = []
    current = ""
    for number, line in enumerate(text.splitlines(), start=1):
        if line and not line.startswith(("\t", " ", "#")) and ":" in line:
            current = line.split(":", 1)[0].strip()
            continue
        if not line.startswith("\t") or current in _VENV_LITERAL_TARGETS:
            continue
        if _VENV_LITERAL.search(line):
            findings.append(
                Finding(
                    "Finding",
                    f"Makefile:{number}",
                    "makefile:hardcoded-venv-path",
                    f"{current or '(recipe)'} names a venv directory outright; resolve the "
                    "tool through $(RC_BIN) -- $(abspath $(RC_BIN)) after a `cd` -- so the venv/ "
                    "and ../venv/ layouts racecar.mk detects are not silently unsupported",
                )
            )
    return findings


_RETIRED_USE = re.compile(r"\$[({](" + "|".join(RETIRED_MAKE_VARS) + r")[)}]")
_INCLUDE = re.compile(r"^\s*-?include\s+racecar\.mk\b", re.M)


def _replacement(name: str) -> str:
    """What to write instead of a retired name, in words."""
    new = RETIRED_MAKE_VARS[name]
    return f"$({new})" if new else "nothing (racecar.mk derives it)"


def _defines(name: str) -> re.Pattern[str]:
    """A line of a Makefile that sets `name`."""
    return re.compile(
        rf"^\s*(?:(?:override|export)\s+)*{name}\s*(?::{{1,3}}=|[?+!]?=)", re.M
    )


def _retired_variables(text: str) -> list[Finding]:
    """Report each line of the owned Makefile that uses or overrides a name racecar.mk retired.

    make expands a variable nothing defines to empty, so `$(PYTHON) x.py` runs `x.py` and
    `$(PIP) install` runs the system `install`, with no error. A line above `include racecar.mk`
    that sets a retired name is an override racecar.mk no longer reads. A Makefile that sets the
    name only below the include is using its own variable, and its uses are left alone.
    """
    findings: list[Finding] = []
    include = _INCLUDE.search(text)
    head = text[: include.start()] if include else ""
    for name in RETIRED_MAKE_VARS:
        for match in _defines(name).finditer(head):
            number = text.count("\n", 0, match.start()) + 1
            findings.append(
                Finding(
                    "Finding",
                    f"Makefile:{number}",
                    f"makefile:retired-variable:{name}",
                    f"sets {name}, which racecar.mk no longer reads, so the setting does nothing; "
                    f"set {_replacement(name)} instead",
                )
            )
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        for name in dict.fromkeys(_RETIRED_USE.findall(line)):
            if _defines(name).search(text):
                continue
            findings.append(
                Finding(
                    "Finding",
                    f"Makefile:{number}",
                    f"makefile:retired-variable:{name}",
                    f"$({name}) is defined by nothing since racecar.mk retired it, so make expands "
                    f"it to empty and this line runs something else; use {_replacement(name)}",
                )
            )
    return findings


def check_makefile(root: Path) -> list[Finding]:
    """Validate the Makefile (and its included racecar.mk) against the §7 contract."""
    path = root / "Makefile"
    if not path.exists():
        return [Finding("Blocker", "Makefile", "missing-file", "Makefile is required")]
    try:
        makefile_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [Finding("Blocker", "Makefile", "encoding-error", str(exc))]
    text, findings = _resolve_makefile_text(root, makefile_text)
    findings += _retired_variables(makefile_text)
    if text is None:
        return findings

    found = set(_MAKEFILE_TARGET_RE.findall(text))
    missing = REQUIRED_MAKEFILE_TARGETS - found
    for target in sorted(missing):
        findings.append(
            Finding(
                "Blocker",
                "Makefile",
                f"missing-target:{target}",
                "required canonical target absent; see PACKAGING.md §7",
            )
        )

    for tool in FORBIDDEN_MAKEFILE_TOOLS:
        if re.search(rf"(^|[\s\t])({re.escape(tool)})(\s|$)", text, re.MULTILINE):
            findings.append(
                Finding(
                    "Blocker",
                    "Makefile",
                    f"non-canon-tool:{tool}",
                    f"invocation of {tool!r} is non-canon (PACKAGING.md §1 §2)",
                )
            )

    # Fast `check` = fmt-check + lint + the tests, read from its private `_check`.
    # Either `test` or `test-fast` satisfies the test dependency (racecar.mk SLOW_TIER_GLOB).
    check_target = _unwrap_target(text, "check")
    if _declarations(text, check_target):
        deps = _target_prereqs(text, check_target)
        for required in ("fmt-check", "lint", ("test", "test-fast")):
            names = (required,) if isinstance(required, str) else required
            if not deps.intersection(names):
                findings.append(
                    Finding(
                        "Finding",
                        "Makefile",
                        f"check-chain:{names[0]}",
                        "fast `check` should depend on "
                        + " or ".join(repr(n) for n in names)
                        + " (PACKAGING.md §7)",
                    )
                )

    # install-dev: must depend on install, install the dev group, wire pre-commit.
    if _declarations(text, "install-dev") and "install" not in _target_prereqs(
        text, "install-dev"
    ):
        findings.append(
            Finding(
                "Blocker",
                "Makefile",
                "install-dev:missing-install-dep",
                "install-dev must depend on 'install' (PACKAGING.md §7)",
            )
        )
    body = _target_body(text, "install-dev")
    if body and "--group" not in body:
        findings.append(
            Finding(
                "Blocker",
                "Makefile",
                "install-dev:pip-group",
                "install-dev must run 'pip install --group' for the PEP 735 "
                "dev group (PACKAGING.md §7)",
            )
        )
    if body and "pre-commit install" not in body:
        findings.append(
            Finding(
                "Blocker",
                "Makefile",
                "install-dev:pre-commit-install",
                "install-dev must run 'pre-commit install' (PACKAGING.md §7)",
            )
        )

    # fmt: isort must precede black.
    body = _target_body(text, "fmt")
    if body:
        isort_pos = body.find("isort")
        black_pos = body.find("black")
        if isort_pos == -1 or black_pos == -1 or isort_pos > black_pos:
            findings.append(
                Finding(
                    "Blocker",
                    "Makefile",
                    "fmt:isort-before-black",
                    "fmt must invoke isort before black (PACKAGING.md §7)",
                )
            )

    findings += _hardcoded_venv_paths(text)

    # arch must invoke the canonical check scripts (follow the public `arch` to `_arch`).
    body = _target_body(text, _unwrap_target(text, "arch"))
    for script in ("check_upward_imports.py", "check_packaging.py"):
        if body and script not in body:
            findings.append(
                Finding(
                    "Blocker",
                    "Makefile",
                    f"arch:{script}",
                    f"arch must invoke scripts/{script} (PACKAGING.md §7)",
                )
            )

    # docs must invoke both doc-coherence scripts.
    body = _target_body(text, "docs")
    for script in (
        "check_docs.py",
        "check_file_placement.py",
    ):
        if body and script not in body:
            findings.append(
                Finding(
                    "Blocker",
                    "Makefile",
                    f"docs:{script}",
                    f"docs must invoke scripts/{script} (PACKAGING.md §7)",
                )
            )

    # help must use ##@ section markers.
    body = _target_body(text, "help")
    if body and "##@" not in body:
        findings.append(
            Finding(
                "Finding",
                "Makefile",
                "help:no-section-markers",
                "help should use ##@ section markers to group non-canon targets (PACKAGING.md §7)",
            )
        )

    return findings
