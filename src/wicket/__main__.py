"""CLI entry: python -m wicket

Pattern 1 (pure discovery): lists sub-packages and exits.
"""

import sys


def commands() -> list[tuple[str, str]]:
    return [
        ("catalog", "Observe the mailbox into the year-sharded manifest"),
        ("fetch", "Download .eml for matching threads, filed by sender domain"),
        ("report", "Read-only reports over the manifest (senders, addresses)"),
    ]


def _print_commands() -> None:
    entries = [(f"python -m {__package__}.{n}", d) for n, d in commands()]
    width = max(len(p) for p, _ in entries)
    print(f"python -m {__package__}\n")
    for path, desc in entries:
        print(f"  {path.ljust(width)}   {desc}")
    print("\nAppend --help to any command for its options.")


if __name__ == "__main__":
    _print_commands()
    sys.exit(0)
