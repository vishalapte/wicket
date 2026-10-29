"""Build a graph from a body of source material.

`build()` restructures whatever is at `--data` into the shape `GRAPH.md` describes.
A markdown source is read directly; anything else becomes a `document`-kind residue
node: the original preserved verbatim under `files/`, and a stub rendering recording
that no converter exists for its format. Format converters are not built here.

Classification reuses `racecar.graph.ontology`'s `check()`/`derive()`: markdown sources
with frontmatter are scored against the ontology; everything else is unclassified and
stamped kind `document`.

Two sources that would file to the same `<category>/<document>/` path are a collision:
the second is suffixed, and both are named in `BuildResult.collisions` for a person to
resolve.

Part of `lib.graph`; `scripts/graph.py` parses the command line and calls `main`. `run` is
the build and returns its `BuildResult`; `renderer.text` is how that record reads.

Complexity: O(S) in source files copied, plus one ontology derivation.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import yaml
from lib import not_a_command
from lib.graph.renderer import text
from lib.ontology import _kinds
from lib.shared import _frontmatter
from lib.shared._as_json import run_json

# Never named from the source's own filename (`GRAPH.md` §Naming). Absent a converter the
# slugged stem is used and the node is stamped `status: unknown`.
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_UNCATEGORIZED = "uncategorized"
_DOCUMENT_KIND = "document"
_LOCK_NAME = ".graph-build.lock"
_ONTOLOGY_FILENAME = "ontology.yaml"


def _slug(stem: str) -> str:
    slugged = _SLUG_RE.sub("-", stem.lower()).strip("-")
    return slugged or "untitled"


@dataclasses.dataclass(frozen=True)
class Collision:
    """Two sources that would file to one `<category>/<document>/` path; never overwritten."""

    path: str  # the "<category>/<document>" slug both sources produced
    sources: tuple[Path, ...]


@dataclasses.dataclass(frozen=True)
class BuildResult:
    """What `build()` did, and how completely.

    `sources_in` and `sources_indexed` are always both reported and equal on success; a
    source `build()` cannot write a node for is a hard failure.
    """

    root: Path
    manifest: Path
    sources_in: int
    sources_indexed: int
    unmapped_fraction: float
    by_kind: dict[str, int]
    collisions: tuple[Collision, ...]
    backup: Path | None


def _walk_sources(data: Path) -> list[Path]:
    """Every regular file under `data`, recursively (`rglob`), sorted."""
    return sorted(
        p for p in data.rglob("*") if p.is_file() and p.name != _ONTOLOGY_FILENAME
    )


def _ontology_version(path: Path | None) -> str:
    """A stable version tag for the pinned ontology: a short content hash of it."""
    if path is None or not path.is_file():
        return "unversioned"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:12]


def _classify(
    source: Path, ontology: dict[str, Any] | None
) -> tuple[str | None, dict[str, Any], str]:
    """(kind or None, frontmatter, body) for one source.

    `kind` is `None` when the source is not markdown, has no frontmatter, or its declared
    kind is not one the ontology names.
    """
    if source.suffix.lower() != ".md":
        return None, {}, ""
    frontmatter, body = _frontmatter.parse(
        source.read_text(encoding="utf-8", errors="replace")
    )
    if not frontmatter or not ontology:
        return None, frontmatter, body
    # The frontmatter field naming a source's kind: the ontology's declared override, or
    # `kind` (the same default `lib.ontology._kinds` reads).
    discriminator = str(ontology.get("discriminator") or "kind")
    raw_kind = frontmatter.get(discriminator)
    kinds = ontology.get("kinds") or {}
    if raw_kind and str(raw_kind) in kinds:
        return str(raw_kind), frontmatter, body
    return None, frontmatter, body


def _backup(dest: Path) -> Path | None:
    """A durable backup of `dest`'s current content, taken before any write below.

    Written beside `dest`, not under `/tmp` (`GRAPH.md` §Disciplines). `None` when there
    is nothing yet to protect.
    """
    if not dest.exists() or not any(p.name != _LOCK_NAME for p in dest.iterdir()):
        return None
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    archive = dest.parent / f"{dest.name}.backup-{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(dest, arcname=dest.name)
    return archive


class _ExclusiveLock:
    """One writer per tree: an `O_CREAT | O_EXCL` lock file makes a second concurrent
    `build()` against the same `dest` refuse immediately."""

    def __init__(self, dest: Path) -> None:
        self._path = dest / _LOCK_NAME

    def __enter__(self) -> "_ExclusiveLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(
                f"{self._path}: another build is already writing this tree "
                "(one writer per tree — see GRAPH.md)"
            ) from exc
        os.close(fd)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._path.unlink(missing_ok=True)


@dataclasses.dataclass(frozen=True)
class _Ontology:
    """The ontology `build()` pins at the graph root, resolved once up front."""

    declared: dict[str, Any] | None
    name: str
    version: str


def _resolve_ontology(data: Path, meta: Path | None) -> _Ontology:
    """The ontology to pin: at `meta` if given, else `data`'s own declared/derived one.

    A `derive()` proposal is reported, never adopted as the pin.
    """
    path: Path | None
    if meta and meta.is_file():
        path = meta
    elif meta and meta.is_dir():
        candidate = meta / _ONTOLOGY_FILENAME
        path = candidate if candidate.is_file() else None
    else:
        candidate = data / _ONTOLOGY_FILENAME
        path = candidate if candidate.is_file() else None

    declared = (
        yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if path
        else _kinds.load_ontology(data)
    )
    fit = _kinds.check(data, path.parent if path else data)
    name = fit.best or (declared or {}).get("name") or "unnamed"
    return _Ontology(declared=declared, name=name, version=_ontology_version(path))


def _rendering_body(name: str, source: Path, kind: str | None, body: str) -> str:
    """The rendering's content: the source's own body when classified, else an
    explicit residue note for a format with no converter."""
    if kind:
        return body or f"# {name}\n"
    return (
        f"# {name}\n\nNo converter is available for "
        f"`{source.suffix or '(no extension)'}` sources — the original is preserved "
        f"unmodified at `files/{source.name}`. Content was not extracted; "
        "`status: unknown` reflects that honestly rather than guessing a kind for "
        "this node.\n"
    )


def _write_node(
    source: Path,
    data: Path,
    dest: Path,
    ontology: dict[str, Any] | None,
    *,
    seen_paths: dict[str, list[Path]],
    collisions: list[Collision],
) -> dict[str, Any]:
    """Write one source's node (files/ copy + dated rendering); return its manifest row.

    Category directories and their `README.md` are created on first use.
    """
    kind, _frontmatter, body = _classify(source, ontology)
    category = kind or _UNCATEGORIZED
    node_kind = kind or _DOCUMENT_KIND

    name = _slug(source.stem)
    rel_key = f"{category}/{name}"
    prior = seen_paths.setdefault(rel_key, [])
    if prior:
        name = f"{name}-{len(prior) + 1}"
        collisions.append(Collision(path=rel_key, sources=(prior[0], source)))
    prior.append(source)

    cat_dir = dest / category
    files_dir = cat_dir / "files"
    doc_dir = cat_dir / name
    files_dir.mkdir(parents=True, exist_ok=True)
    doc_dir.mkdir(parents=True, exist_ok=True)
    cat_readme = cat_dir / "README.md"
    if not cat_readme.exists():
        cat_readme.write_text(
            f"---\npnode: [../README.md]\n---\n\n# {category}\n", encoding="utf-8"
        )

    shutil.copy2(source, files_dir / source.name)

    refdate = time.strftime("%Y%m%d", time.gmtime(source.stat().st_mtime))
    status = "draft" if kind else "unknown"
    rendering_fm = {
        "pnode": ["../README.md"],
        "kind": node_kind,
        "status": status,
        "source": f"files/{source.name}",
    }
    rendering_path = doc_dir / f"{name}_{refdate}.md"
    rendering_path.write_text(
        "---\n"
        + yaml.safe_dump(rendering_fm, sort_keys=False)
        + "---\n\n"
        + _rendering_body(name, source, kind, body),
        encoding="utf-8",
    )

    return {
        "path": str(source.relative_to(data)),
        "name": name,
        "rendering": str(rendering_path.relative_to(dest)),
        "category": category,
        "refdate": f"{refdate[:4]}-{refdate[4:6]}-{refdate[6:]}",
        "kind": node_kind,
        "status": status,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }


def _write_root(dest: Path, ontology: _Ontology) -> None:
    (dest / "README.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "pnode": [],
                "graph": True,
                "ontology": {"name": ontology.name, "version": ontology.version},
            },
            sort_keys=False,
        )
        + "---\n\n# graph\n",
        encoding="utf-8",
    )


def build(data: Path, dest: Path, meta: Path | None = None) -> BuildResult:
    """Restructure `data` into a graph at `dest`, conforming to the ontology at `meta`.

    `meta` is a path to an `ontology.yaml` (or a directory holding one); when it is
    `None`, the corpus-declared ontology at `data`'s own root is used if `check()`
    fits it, else `derive()`'s proposal (see `_resolve_ontology`).
    """
    if not data.is_dir():
        raise NotADirectoryError(f"{data} is not a directory")

    resolved = _resolve_ontology(data, meta)
    dest.mkdir(parents=True, exist_ok=True)

    with _ExclusiveLock(dest):
        backup = _backup(dest)

        sources = _walk_sources(data)
        by_kind: dict[str, int] = {}
        collisions: list[Collision] = []
        seen_paths: dict[str, list[Path]] = {}
        manifest_rows = [
            _write_node(
                source,
                data,
                dest,
                resolved.declared,
                seen_paths=seen_paths,
                collisions=collisions,
            )
            for source in sources
        ]
        for row in manifest_rows:
            by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1

        _write_root(dest, resolved)

        manifest_path = dest / "manifest.jsonl"
        with manifest_path.open("w", encoding="utf-8") as handle:
            for row in manifest_rows:
                handle.write(json.dumps(row, sort_keys=True))
                handle.write("\n")

    total = len(sources)
    unclassified = by_kind.get(_DOCUMENT_KIND, 0)
    unmapped_fraction = 1.0 if total == 0 else unclassified / total

    return BuildResult(
        root=dest,
        manifest=manifest_path,
        sources_in=total,
        sources_indexed=len(manifest_rows),
        unmapped_fraction=unmapped_fraction,
        by_kind=by_kind,
        collisions=tuple(collisions),
        backup=backup,
    )


def run(data: Path, dest: Path, meta: Path | None = None) -> BuildResult:
    """The `build` verb's record: what `build` wrote at `dest`, and how completely.

    Raises `NotADirectoryError` when `data` is not a directory, and `RuntimeError` when
    another build holds `dest`.
    """
    return build(data, dest, meta=meta)


def _show(data: Path, dest: Path, meta: Path | None) -> int:
    """Run the build and print what landed where; 0, or 1 when it could not run."""
    try:
        result = run(data, dest, meta)
    except (NotADirectoryError, RuntimeError) as exc:
        print(text.refusal("build", exc), file=sys.stderr)
        return 1
    print(text.build(result))
    return 0


def main(
    data: Path, dest: Path, meta: Path | None = None, *, as_json: bool = False
) -> int:
    """Build a graph at `dest` from `data`, and report what landed where; under `as_json`
    the text goes to stderr and stdout carries the exit code, the result this verb declares.
    """
    return run_json(as_json, lambda: _show(data, dest, meta))


if __name__ == "__main__":
    not_a_command()
