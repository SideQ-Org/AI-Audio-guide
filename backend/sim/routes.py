"""Sample virtual-walk routes (lat, lon)."""

from __future__ import annotations

from app.shared.schemas import GeoPoint

# Short stroll across Red Square, Moscow — passes several fixture landmarks.
RED_SQUARE: list[GeoPoint] = [
    GeoPoint(lat=55.7525, lon=37.6231),  # St. Basil's Cathedral
    GeoPoint(lat=55.7537, lon=37.6205),  # Lenin's Mausoleum
    GeoPoint(lat=55.7547, lon=37.6196),  # Kazan Cathedral / GUM
    GeoPoint(lat=55.7553, lon=37.6178),  # State Historical Museum
]
