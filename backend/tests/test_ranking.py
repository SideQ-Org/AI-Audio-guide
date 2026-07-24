from app.services.geo.ranking import build_candidates
from app.shared.schemas import GazeConfidence, GeoPoint, Heading, Place


def _place(pid: str, name: str, category: str, lat: float, lon: float) -> Place:
    return Place(id=pid, name=name, category=category, location=GeoPoint(lat=lat, lon=lon))


POSITION = GeoPoint(lat=55.7537, lon=37.6205)
PLACES = [
    _place("p_museum", "Museum", "museum", 55.7553, 37.6178),
    _place("p_shop", "Shop", "shop", 55.7538, 37.6206),  # very close, low weight
    _place("p_far", "Far church", "place_of_worship", 55.9000, 37.9000),  # out of radius
]


def test_radius_filters_far_places():
    cands = build_candidates(POSITION, Heading(), PLACES, radius_m=300.0)
    ids = {c.place.id for c in cands}
    assert "p_far" not in ids
    assert {"p_museum", "p_shop"} <= ids


def test_polygon_place_measured_from_live_position():
    # B1: a park polygon the user stands INSIDE reads as distance ~0 with no misleading
    # "ahead/behind", instead of snapping to a far boundary vertex.
    c = POSITION
    d = 0.002  # ~200 m box
    ring = [
        [c.lat - d, c.lon - d], [c.lat - d, c.lon + d],
        [c.lat + d, c.lon + d], [c.lat + d, c.lon - d], [c.lat - d, c.lon - d],
    ]
    # location is a boundary CORNER (the stale snap) far from the user; geometry is the box
    park = Place(
        id="park", name="Парк", category="park",
        location=GeoPoint(lat=c.lat - d, lon=c.lon - d), geometry=ring,
    )
    heading = Heading(direction_deg=90.0, gaze_confidence=GazeConfidence.HIGH)
    cands = build_candidates(c, heading, [park], radius_m=75.0)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.distance_m < 1.0  # inside -> "here", not the ~280 m corner
    assert cand.side is None and cand.in_gaze_cone is False  # no direction when inside


def test_seen_are_excluded():
    cands = build_candidates(POSITION, Heading(), PLACES, radius_m=300.0, seen=["p_museum"])
    assert all(c.place.id != "p_museum" for c in cands)


def test_high_weight_can_outrank_closer_low_weight():
    # museum is farther but far more interesting than an adjacent shop
    cands = build_candidates(POSITION, Heading(), PLACES, radius_m=400.0)
    assert cands[0].place.id == "p_museum"


def test_gaze_cone_detected_when_heading_known():
    # heading roughly toward the museum (north-west)
    heading = Heading(direction_deg=330.0, gaze_confidence=GazeConfidence.HIGH)
    cands = build_candidates(POSITION, heading, PLACES, radius_m=400.0)
    museum = next(c for c in cands if c.place.id == "p_museum")
    assert museum.in_gaze_cone is True
    assert museum.gaze_confidence is GazeConfidence.HIGH


# objects around POSITION when facing north (0°): east is to the right, west to the
# left, north ahead, south behind.
_SIDE_PLACES = [
    _place("east", "E", "monument", 55.7537, 37.6220),
    _place("west", "W", "monument", 55.7537, 37.6190),
    _place("north", "N", "monument", 55.7546, 37.6205),
    _place("south", "S", "monument", 55.7528, 37.6205),
]


def test_side_left_right_only_at_high_gaze():
    hi = Heading(direction_deg=0.0, gaze_confidence=GazeConfidence.HIGH)
    by = {c.place.id: c for c in build_candidates(POSITION, hi, _SIDE_PLACES, radius_m=300.0)}
    assert by["east"].side == "right"
    assert by["west"].side == "left"
    assert by["north"].side == "ahead"
    assert by["south"].side == "behind"


def test_side_no_left_right_at_low_gaze_but_ahead_behind_ok():
    lo = Heading(direction_deg=0.0, gaze_confidence=GazeConfidence.LOW)
    by = {c.place.id: c for c in build_candidates(POSITION, lo, _SIDE_PLACES, radius_m=300.0)}
    # lateral objects get no side at low confidence (never fake left/right)...
    assert by["east"].side is None
    assert by["west"].side is None
    # ...but ahead/behind are knowable from the GPS course
    assert by["north"].side == "ahead"
    assert by["south"].side == "behind"


def test_side_needs_clear_lateral_angle():
    """Left/right only for a CLEARLY-lateral object (>=50°): a near-ahead object stays
    'ahead' so a skewed heading (magnetic declination ~12°) can't flip left<->right."""
    from app.services.geo.ranking import _side
    from app.shared.schemas import GazeConfidence as GC

    # near-ahead (within 50°) => ahead regardless of sign, even at high confidence
    assert _side(30, GC.HIGH) == "ahead"
    assert _side(-30, GC.HIGH) == "ahead"
    assert _side(48, GC.HIGH) == "ahead"
    # a 12° heading error near dead-ahead can't produce a side (both stay 'ahead')
    assert _side(10, GC.HIGH) == _side(-14, GC.HIGH) == "ahead"
    # clearly lateral => a stable side
    assert _side(90, GC.HIGH) == "right"
    assert _side(-90, GC.HIGH) == "left"
    assert _side(70, GC.HIGH) == "right"
    # behind
    assert _side(170, GC.HIGH) == "behind"
    # low confidence never names a side
    assert _side(90, GC.LOW) == ""
