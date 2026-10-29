"""account: a namespace only; import ``api`` or ``lib``, no re-exports.

An account is a folder, ``~/.config/wicket/accounts/<name>/``, holding an ``imap.json``
link to its credential file: made by hand with ``mkdir`` and ``ln -s`` or by this noun.
``fetch`` and ``catalog`` resolve it; ``halt --all`` reads the same folders.
"""
