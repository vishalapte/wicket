"""halt: a namespace only; import ``api`` or ``lib``, no re-exports.

A HALT stops every IMAP connection to an account until it is cleared: a file under
``$XDG_STATE_HOME/wicket/halt/`` (present means halted), each set and clear logged.
"""
