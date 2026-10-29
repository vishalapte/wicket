"""The `create` verb on the delivered route: it cannot run here, and says where it can.

`create` scaffolds the `src/<pkg>` library from racecar's `templates/classic/`, renders the
server shell and its REST and MCP surfaces with racecar's server generator
(`racecar.lib._server`, which `racecar.surface` shares), and writes the Authorization Server
from `arch-python/templates/authserver/`. None of that is delivered, so an adopter's
`scripts/package.py` cannot build any of it.

`python -m racecar.package create` does not reach this module: it runs
`racecar.package.api.create`, whose rungs are racecar's own code. The flags are
declared once, in `scripts/package.py`'s parser, so the two routes offer the same command.

Exit: 2, always.

Complexity: O(1)
"""

from __future__ import annotations

import sys
from typing import NoReturn

from lib import not_a_command
from lib.package._error import PackageError
from lib.package.renderer import text


def run() -> NoReturn:
    """Refuse: the scaffolders and their templates are not delivered."""
    raise PackageError(text.create_refused())


def main() -> int:
    """Say why `create` cannot run on this route, and where it runs; exit 2."""
    try:
        run()
    except PackageError as exc:
        print(exc, file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    not_a_command()
