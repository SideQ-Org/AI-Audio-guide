"""Subscription receipt verification (feature: account tiers).

The store (Google Play / App Store) is the payment rail; this package is the server
side that verifies a client's purchase token against the store's API and grants the
paid tier. Import-safe for the base install: the FastAPI router imports only
FastAPI/pydantic; the store SDK/credentials are pulled in lazily inside the handlers.
"""
