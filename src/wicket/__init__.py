"""wicket: portable, read-only mail tooling over a year-sharded manifest.

A namespace, not an API surface. The four verbs are the root noun's: their api is
``wicket.root.api`` and each verb's worker ``wicket.root.lib.<verb>``; import from
the true home, never from here (this file holds no code):

    from wicket.root.api import catalog, CatalogOptions
    from wicket.root.api import fetch, FetchOptions, held_messages
    from wicket.root.api import report, sender_counts, all_addresses, manifest
    from wicket.env import resolve_store_dir, resolve_archive_dir

Every entry point takes ``account=`` (multi-user, n accounts); the only account
folder in ``~/.config/wicket/accounts`` is the default.
"""
