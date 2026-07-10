"""Small spherical-geometry helpers (good enough at city scale)."""

from __future__ import annotations

from math import asin, atan2, cos, degrees, hypot, radians, sin, sqrt

from app.shared.schemas import GeoPoint

EARTH_RADIUS_M = 6_371_000.0
_M_PER_DEG_LAT = 111_320.0  # metres per degree of latitude (≈ constant)


def haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    """Great-circle distance between two points, in metres."""
    dlat = radians(b.lat - a.lat)
    dlon = radians(b.lon - a.lon)
    la1, la2 = radians(a.lat), radians(b.lat)
    h = sin(dlat / 2) ** 2 + cos(la1) * cos(la2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(h))


def bearing_deg(a: GeoPoint, b: GeoPoint) -> float:
    """Initial bearing from a to b, degrees clockwise from north (0..360)."""
    la1, la2 = radians(a.lat), radians(b.lat)
    dlon = radians(b.lon - a.lon)
    y = sin(dlon) * cos(la2)
    x = cos(la1) * sin(la2) - sin(la1) * cos(la2) * cos(dlon)
    return (degrees(atan2(y, x)) + 360.0) % 360.0


def angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two bearings (0..180)."""
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _point_in_ring(p: GeoPoint, ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon for a ring given as [[lat, lon], ...] (lon=x, lat=y)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        yi, xi = ring[i][0], ring[i][1]
        yj, xj = ring[j][0], ring[j][1]
        if (xi > p.lon) != (xj > p.lon):
            x_cross = (yj - yi) * (p.lon - xi) / ((xj - xi) or 1e-12) + yi
            if p.lat < x_cross:
                inside = not inside
        j = i
    return inside


def nearest_on_geometry(
    p: GeoPoint, geometry: list[list[float]]
) -> tuple[float, GeoPoint]:
    """Nearest distance (m) from ``p`` to a way's polyline/polygon boundary (given as
    ``[[lat, lon], ...]``) and the nearest point on it. A CLOSED ring the user stands
    INSIDE returns ``(0.0, p)`` — so a park/pond you're within reads as "here", not as a
    far boundary vertex. This is the live-position fix for large polygons snapping to a
    stale fetch-anchor vertex (B1). Single-vertex geometry -> distance to that point."""
    if not geometry:
        return float("inf"), p
    if len(geometry) == 1:
        gp = GeoPoint(lat=geometry[0][0], lon=geometry[0][1])
        return haversine_m(p, gp), gp
    closed = len(geometry) >= 4 and geometry[0] == geometry[-1]
    if closed and _point_in_ring(p, geometry):
        return 0.0, p
    # Local equirectangular projection centred on p (accurate well under a metre at
    # street scale): project p (the origin) onto each segment, keep the nearest.
    mx = cos(radians(p.lat)) * _M_PER_DEG_LAT
    my = _M_PER_DEG_LAT
    best_d = float("inf")
    best = GeoPoint(lat=geometry[0][0], lon=geometry[0][1])
    for i in range(len(geometry) - 1):
        a, b = geometry[i], geometry[i + 1]
        ax, ay = (a[1] - p.lon) * mx, (a[0] - p.lat) * my
        bx, by = (b[1] - p.lon) * mx, (b[0] - p.lat) * my
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0.0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
        d = hypot(ax + t * dx, ay + t * dy)
        if d < best_d:
            best_d = d
            best = GeoPoint(lat=a[0] + t * (b[0] - a[0]), lon=a[1] + t * (b[1] - a[1]))
    return best_d, best


def relative_bearing(heading_deg: float, target_bearing_deg: float) -> float:
    """Signed angle of `target` relative to `heading`, in (-180, 180].

    Bearings are clockwise from north, so a positive result means the target is
    clockwise from where you face — i.e. **to your right**; negative is **left**.
    (Facing north, an object due east → +90 → right; due west → -90 → left.)
    Unlike `angle_diff`, this keeps the sign, which is what distinguishes left
    from right — only meaningful when the heading is a real facing direction
    (compass / gaze_confidence=high), not a noisy GPS course.
    """
    return ((target_bearing_deg - heading_deg + 180.0) % 360.0) - 180.0
