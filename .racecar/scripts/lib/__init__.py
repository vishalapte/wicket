"""Implementation behind the delivered scripts. Nothing here is ever run directly.

A file directly under `scripts/` is something an adopter runs -- `lexicon.py`,
`ontology.py`, `topology.py`, `graph.py`, and every `check_*.py`. Anything under
`scripts/lib/` is something a run imports. Every top-level `scripts/*.py` carries a
`__main__` guard, and nothing below here does.

Nouns live at `lib/<noun>/`, not `<noun>/lib/`: a `scripts/lexicon/` package beside
`scripts/lexicon.py` would shadow it for the flat sibling imports. No noun may be called
`lib`.

The base nouns (ontology, topology, lexicon, surface, graph, package) live here entirely;
`src/racecar/<noun>/` is only a wrapper, through which `python -m racecar.<noun>` and the
server reach the same code. `scripts/` is delivered, so an adopter with no racecar
installed runs every one of them.

Every noun has one shape:

    scripts/
      <noun>.py              the entry: `parser(prog)` and `main(argv, prog)`, nothing else
      lib/<noun>/
        __init__.py          re-exports, nothing else
        _<verb>.py           one per verb: `run(...)` returns the verb's record, printing
                             nothing; `main(...)` prints that record -- through
                             `renderer.text` in prose, or as the record itself under `--json`
        renderer/
          text.py            every line this noun prints, rendered from a record
        _<helper>.py         anything else, named for its job

  - `scripts/<noun>.py` builds the parser in `parser(prog)` and dispatches in `main`. It
    holds no verb's work.
  - `lib/<noun>/_<verb>.py` exists for each verb that parser declares (`check-json` is
    `_check_json.py`) and ends in the `not_a_command()` guard. `run` is the work and its
    record is the verb's one result; `main` only chooses how to show it.
  - `lib/<noun>/renderer/text.py` is the only place a verb's text is made; both routes
    print through it.
  - `src/racecar/<noun>/__main__.py` builds its parser from `parser()` and hands its argv to
    `main()`, under its own `prog`; `src/racecar/<noun>/api.py` returns what the verbs'
    `run` return, and prints nothing. Neither renders.

Anything else below `lib/<noun>/` is a helper named for its job. `tests/test_noun_shape.py`
holds these rules for every `lib/<noun>/` that has a `scripts/<noun>.py`.

Complexity: O(1) -- a namespace, a rule, one path and one loader
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import NoReturn

#: `scripts/`, where the delivered checkers sit as flat siblings; the one home for this
#: depth. Not put on `sys.path` here: running `scripts/<noun>.py` and
#: `racecar.lib._dispatch.load_package` both already do.
SCRIPTS = Path(__file__).resolve().parent.parent


def not_a_command() -> NoReturn:
    """Refuse a direct `python scripts/lib/<noun>/<module>.py`. Never returns.

    The delivered twin of `racecar.lib._exit.not_a_command`: exits 1, since an unguarded
    module would run nothing and exit 0. `check_cli_commands._declares_not_a_command`
    recognises the guard by this name.
    """
    raise SystemExit(1)


def load_file(source: Path, name: str) -> ModuleType:
    """The module in `source`, imported by file location under the private `name`.

    For code that belongs to the repo being read rather than to this one. `sys.modules`
    caches by name, so the caller keys `name` on the repo or the file. The name is
    registered only while the module executes, and removed afterwards.

    Raises ImportError when `source` has no import spec. Whatever the module raises as
    it executes propagates.
    """
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"no import spec for {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module
