"""wicket: portable, read-only mail tooling over a year-sharded manifest.

The verbs are CLIs (``python -m wicket.{catalog,fetch,report}``). For consumers
that want the data directly, the public library surface is re-exported here:

    from wicket import held_messages, manifest, resolve_archive_dir
"""

from wicket.api import held_messages, manifest
from wicket.config import resolve_archive_dir, resolve_store_dir

__all__ = ["held_messages", "manifest", "resolve_archive_dir", "resolve_store_dir"]
