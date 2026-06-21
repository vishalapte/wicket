"""wicket: portable, read-only mail tooling over a year-sharded manifest.

A namespace, not an API surface. Each verb is a self-contained vertical under
``wicket/`` (worker + api + cli face); import from the true home, never from
here (this file holds no code):

    from wicket.catalog.api import catalog, CatalogOptions
    from wicket.fetch.api import fetch, FetchOptions, held_messages
    from wicket.report.api import report, senders, addresses, manifest
    from wicket.config import resolve_store_dir, resolve_archive_dir

Every entry point takes ``account=`` (multi-user, n accounts); a single account
under ``~/mail`` is the default.
"""
