"""The `config` verb's argparse face: `parser()` + `dispatch()`.

Not a CLI entry (no `__main__.py` in this package on purpose) -- `python -m
wicket config ...` is the only way in. The root `wicket/__main__.py`
(Pattern 2) imports this module, folds `parser()`'s tree into its own
`config` subparser via `parents=[...]`, and calls `dispatch()` once argparse
has resolved the leaf.

Shape: `config` is a noun with two sub-nouns (`account`, `domain`), each with
one or two further sub-nouns naming a resource (`aliases`, `routes`) -- the
three owner-authored maps at the mail root. Each resource is a noun leaf with
four verb subcommands (`list`/`create`/`update`/`delete`), so the full tree is:

    wicket config account aliases {list,create,update,delete}
    wicket config domain  aliases {list,create,update,delete}
    wicket config domain  routes  {list,create,update,delete}

All three resources take the same shape (`--primary`, repeatable `--item`,
`--add`/`--remove`), so one `_attach_crud` builds all twelve leaf subcommands.
"""

from __future__ import annotations

import argparse
import json
import sys

from wicket.config.api import create, delete, list_map, update


def _attach_crud(
    sub: "argparse._SubParsersAction[argparse.ArgumentParser]", resource: str
) -> None:
    """Add the four `list`/`create`/`update`/`delete` leaves for `resource`."""
    p_list = sub.add_parser("list", help=f"List every entry in {resource}")
    p_list.set_defaults(config_resource=resource, config_verb="list")

    p_create = sub.add_parser("create", help=f"Add a new primary to {resource}")
    p_create.add_argument("--primary", required=True, help="The new entry's key")
    p_create.add_argument(
        "--item",
        action="append",
        default=[],
        help="An item under --primary (repeatable)",
    )
    p_create.set_defaults(config_resource=resource, config_verb="create")

    p_update = sub.add_parser("update", help=f"Add/remove items on {resource}")
    p_update.add_argument("--primary", required=True, help="The entry to update")
    p_update.add_argument(
        "--add", action="append", default=[], dest="add_items", help="Repeatable"
    )
    p_update.add_argument(
        "--remove", action="append", default=[], dest="remove_items", help="Repeatable"
    )
    p_update.set_defaults(config_resource=resource, config_verb="update")

    p_delete = sub.add_parser("delete", help=f"Delete from {resource}")
    p_delete.add_argument("--primary", required=True, help="The entry to delete from")
    p_delete.add_argument(
        "--item",
        default=None,
        help="One item to remove; omit to delete the whole --primary",
    )
    p_delete.set_defaults(config_resource=resource, config_verb="delete")


def _attach_resource(
    map_sub: "argparse._SubParsersAction[argparse.ArgumentParser]",
    map_name: str,
    resource: str,
) -> None:
    """Add one `<map_name>` sub-noun (e.g. `aliases`) with its own CRUD subparsers."""
    node = map_sub.add_parser(map_name, help=f"{resource}.json")
    _attach_crud(
        node.add_subparsers(dest="config_verb_choice", required=True), resource
    )


def parser() -> argparse.ArgumentParser:
    """Argument definitions only (`add_help=False`): folded into the root's subparser."""
    build = argparse.ArgumentParser(
        add_help=False,
        # Explicit, matching where the root folds this in (`python -m wicket
        # config`): add_subparsers() below computes each descendant's usage
        # prefix from THIS prog at build time, and the parents=[] fold at the
        # root does not retroactively fix up a prog it never set.
        prog="python -m wicket config",
        description="Manage the three owner-authored maps: account-aliases, "
        "domain-aliases, domain-routes.",
    )
    top = build.add_subparsers(dest="config_noun", required=True)

    account = top.add_parser("account", help="Which addresses are you")
    account_sub = account.add_subparsers(dest="config_map", required=True)
    _attach_resource(account_sub, "aliases", "account-aliases")

    domain = top.add_parser(
        "domain", help="Domains that are NOT you, and their identity"
    )
    domain_sub = domain.add_subparsers(dest="config_map", required=True)
    _attach_resource(domain_sub, "aliases", "domain-aliases")
    _attach_resource(domain_sub, "routes", "domain-routes")
    return build


def dispatch(args: argparse.Namespace) -> int:
    """Call the api for the resolved resource + verb, print the result as JSON."""
    resource = args.config_resource
    verb = args.config_verb
    try:
        if verb == "list":
            result = list_map(resource)
        elif verb == "create":
            result = create(resource, args.primary, args.item)
        elif verb == "update":
            result = update(resource, args.primary, args.add_items, args.remove_items)
        else:
            result = delete(resource, args.primary, args.item)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0
