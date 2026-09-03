"""Makefile (and its included racecar.mk) validation against the §7 contract."""

from __future__ import annotations

import re
from pathlib import Path

from ._constants import FORBIDDEN_MAKEFILE_TOOLS, REQUIRED_MAKEFILE_TARGETS
from ._findings import Finding

_MAKEFILE_TARGET_RE = re.compile(r"^([a-zA-Z_][\w-]*)\s*:", re.MULTILINE)


def _declarations(text: str, target: str) -> list[re.Match[str]]:
    """Every declaration of a target, in file order.

    A target may legitimately be declared more than once, because the Makefile fold puts
    canon in `racecar.mk` and the project's own extensions in the owned `Makefile`. Both
    land in the combined text this module validates, so a single `re.search` reads only
    the first one.
    """
    return list(
        re.finditer(
            rf"^{re.escape(target)}\s*:(?!=)([^\n]*)\n((?:[\t][^\n]*\n?)*)",
            text,
            re.MULTILINE,
        )
    )


def _target_prereqs(text: str, target: str) -> set[str]:
    """The UNION of a target's prerequisites across every declaration of it.

    Make unions prerequisites across declarations; a checker that reads only the first one
    does not. That mismatch inverted the Makefile fold's own sanctioned extension: adding a
    prerequisite in owned space (`install-dev: install-ansible`) is exactly what §7 tells an
    adopter to do, and it read here as canon's prerequisites having gone missing, pushing
    the adopter to restate canon in owned space to go green. Restating canon in owned space
    is the drift the fold exists to prevent.
    """
    found: set[str] = set()
    for match in _declarations(text, target):
        found.update(match.group(1).split("##", 1)[0].split())
    return found


def _target_body(text: str, target: str) -> str:
    """Return the recipe lines (tab-indented) for a named target, or '' if absent.

    The FIRST declaration that carries a recipe, not the first declaration. A
    prerequisite-only declaration (the sanctioned owned-space extension) contributes
    prerequisites and no recipe, which is make's own rule; skipping it here is what lets
    the canonical body in `racecar.mk` still be graded behind such an extension. A genuine
    recipe override in owned space is still the one read, so §7's "do not redefine a
    canonical recipe" stays enforced.
    """
    for match in _declarations(text, target):
        if match.group(2).strip():
            return match.group(2)
    return ""


def _unwrap_target(text: str, target: str) -> str:
    """Resolve the gate body through the optional build-telemetry wrapper.

    The canonical `check` / `arch` may route through the build-telemetry wrap so the run
    is recorded to the build ledger (DRIFT.md), moving the real prerequisites and recipe
    to a private `_<target>`. When they do, validate that private target; otherwise the
    public one. So the checker follows the canonical shape whether the gate is wrapped or
    direct — the wrapper is transparent to §7, not a way to hide the body.

    Two spellings of the wrap are recognized: the literal `record_gate.py <label> --
    $(MAKE) _<target>` a target's own recipe can still write directly, and racecar.mk's
    own canonical `$(call gate_wrap,<label>,_<target>)` -- the one home for that wrap,
    collapsed out of three near-identical copies. Recognizing only one would make this
    checker itself the next place the two drift apart.
    """
    body = _target_body(text, target)
    if re.search(
        rf"record_gate\.py\s+\S+\s+--\s+\$\(MAKE\)[^\n]*\b(_{re.escape(target)})\b",
        body,
    ):
        return "_" + target
    if re.search(
        rf"\$\(call\s+gate_wrap\s*,\s*\S+?\s*,\s*_{re.escape(target)}\s*\)",
        body,
    ):
        return "_" + target
    return target


def _resolve_makefile_text(
    root: Path, mk_text: str
) -> tuple[str | None, list[Finding]]:
    """Combine the owned Makefile with racecar.mk for validation.

    The Makefile fold (PACKAGING.md §7): canonical targets live in racecar.mk, which
    the owned Makefile includes. Returns (combined_text, findings). combined_text is
    None when validation should stop at a Blocker that supersedes per-target checks.

    Four states, keyed on BOTH whether racecar.mk exists AND whether the Makefile
    actually includes it (fold adoption is the include, not the file's mere presence):

    - included + present  -> combine and validate the union (the adopted fold).
    - included + absent    -> Blocker: the include resolves to nothing; `make sync`.
    - not included + present -> Blocker: racecar.mk is inert dead weight while a
      monolithic Makefile still drives the build. This is the half-migrated state an
      upgrade leaves when sync drops racecar.mk beside a pre-fold Makefile; without
      this branch the checker false-greened on `rcmk.exists()` alone.
    - not included + absent -> Finding: predates the fold; nudge to `make sync`.

    racecar.mk is identical in every repo and self-detects the shape, so there is no
    per-repo stamp to validate.
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
                "Makefile includes racecar.mk but it is absent; run `make sync` to regenerate it",
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
            "no racecar.mk: this repo predates the Makefile fold; run `make sync` "
            "to adopt it (PACKAGING.md §7)",
        )
    ]


# Recipe lines allowed to name a venv directory outright. CREATING a venv picks one
# location by design -- it is DETECTION that must stay plural -- and `distclean` removes
# what `venv` made. Everything else resolves through $(PYTHON) / $(PIP) / $(BIN).
_VENV_LITERAL_TARGETS = frozenset({"venv", ".venv", "distclean", "install-dev"})
# `bin/` is required, and is what makes this precise rather than a mention-of-venv grep.
# The defect is resolving a TOOL through a guessed venv layout; naming a venv directory for
# any other reason is not it. `-not -path '*/venv/*'` in a `find` is the case that proves it
# -- an exclusion glob, matched by an earlier draft of this rule, and correct as written.
_VENV_LITERAL = re.compile(r"(?:\.\./)*\.?venv/bin/")


def _hardcoded_venv_paths(text: str) -> list[Finding]:
    """Report a recipe that names a venv directory where `$(BIN)` would resolve it.

    `racecar.mk` detects `.venv` / `venv` / `../venv` into `$(VENV)` and `$(BIN)` precisely
    so a recipe never has to guess. A literal defeats that for the two layouts it does not
    name, and for the no-venv `$(HOME)/.local/bin` branch -- and it fails in the worst way,
    reporting the tool absent while the tool sits exactly where `$(BIN)` points.

    racecar shipped this defect in its own `lint-ansible` while stating the policy in three
    other places, and an adopter reading that target reproduced it. Nothing graded it, which
    is why this rule exists: the argument was written down and only the text was wrong.

    A Finding, not a Blocker. This grades the OWNED Makefile, where the fold deliberately
    leaves an adopter free -- racecar says what it would do, and does not overrule a repo
    that has a reason.
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
                    "tool through $(BIN) -- $(abspath $(BIN)) after a `cd` -- so the venv/ "
                    "and ../venv/ layouts racecar.mk detects are not silently unsupported",
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

    # Fast `check` = fmt-check + lint + test (pre-commit cadence). Follow the record_gate
    # wrapper to the private `_check` when the gate is recorded to the build ledger.
    check_target = _unwrap_target(text, "check")
    if _declarations(text, check_target):
        deps = _target_prereqs(text, check_target)
        for required in ("fmt-check", "lint", "test"):
            if required not in deps:
                findings.append(
                    Finding(
                        "Finding",
                        "Makefile",
                        f"check-chain:{required}",
                        f"fast `check` should depend on {required!r} (PACKAGING.md §7)",
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

    # fmt: isort must precede black (DRIFT.md / memory: isort before black).
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

    # arch must invoke the canonical check scripts (follow the record_gate wrapper to _arch).
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

    # docs must invoke all four doc-coherence scripts.
    body = _target_body(text, "docs")
    for script in (
        "check_docs.py",
        "check_todo_format.py",
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
