#!/usr/bin/env python3
"""Name the kind of identifier a string is, or say nothing.

The shell's door into `identifiers.json`. Bash cannot run Luhn or Verhoeff, and it should
not have to: a rule about what an Aadhaar number is has one home, and every language reads
it through the same answer rather than reimplementing the arithmetic and drifting.

    $ identify.py 378282246310005
    card-amex
    $ identify.py "MH12 20110012345"
    in-dl
    $ identify.py hello
    $ echo $?
    1

Exit 0 when something claims the string, 1 when nothing does, so a shell can branch on the
status alone and never parse output. `--json` gives the full record -- country, ISO 3166-2
subdivision where the value encodes one, class, and the issuing authority the rule came
from. `--all` lists every type that claims it, for the rare value two rules both accept.

    $ identify.py --json "MH12 20110012345"
    {"type": "in-dl", "country": "IN", "subdivision": "IN-MH", ...}

Reading from stdin scans lines rather than whole strings, which is the form a pipeline
wants:

    $ git grep -h '' -- '*.csv' | identify.py --scan
    aadhaar 234567890124

Complexity: O(n) own overhead (stdin read, strip, output loop); the match cost is identifiers.py's.
"""

from __future__ import annotations

import argparse
import json
import sys

from identifiers import describe, identify_all, scan


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "value",
        nargs="?",
        help="the string to identify; omit to read from stdin",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full record rather than the type name",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="list every type that claims the value, not just the best",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="find identifiers ANYWHERE in each input line, rather than matching whole",
    )
    parser.add_argument(
        "--anchored-only",
        action="store_true",
        help="skip types whose only evidence is a checksum over naked digits",
    )
    return parser.parse_args(argv)


def _emit_scan(text: str, anchored_only: bool, as_json: bool) -> int:
    """Report every identifier occurring anywhere in `text`; 0 if any were found."""
    found = 0
    for name, raw in scan(text, anchored_only=anchored_only):
        found += 1
        if as_json:
            print(json.dumps({"type": name, "value": raw}))
        else:
            print(f"{name} {raw}")
    return 0 if found else 1


def main(argv: list[str] | None = None) -> int:
    """Identify a value from the argument or stdin; return an exit code."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    text = args.value if args.value is not None else sys.stdin.read()

    if args.scan:
        return _emit_scan(text, args.anchored_only, args.json)

    value = text.strip()
    names = identify_all(value, anchored_only=args.anchored_only)
    if not names:
        return 1
    if args.all:
        for name in names:
            print(name)
        return 0
    if args.json:
        print(
            json.dumps(
                describe(value, anchored_only=args.anchored_only), ensure_ascii=False
            )
        )
    else:
        print(names[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
