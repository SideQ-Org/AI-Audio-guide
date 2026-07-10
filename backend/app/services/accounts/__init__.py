"""Durable accounts + walk-history layer (Postgres in prod, SQLite in tests).

SEPARATE from the ephemeral session store (``state/store.py``): the session store
holds the volatile live-tour state and evicts on TTL/LRU; this layer persists what a
logged-in user should keep — their walks and the objects the guide narrated.

Nothing here is wired into the live tour yet (that is phase 3/4). Phase 2 is just the
schema + repository + migrations, importable only when the ``accounts`` extra is
installed and ``settings.database_url`` is set. Guest mode = this layer untouched.
"""
