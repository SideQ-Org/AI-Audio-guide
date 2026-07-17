"""Map raw OSM tags to a coarse category + a 0..1 type weight.

The weight ranks *a priori* interestingness by type (museum/monument > shop),
later combined with proximity and gaze in ``ranking.py``.
"""

from __future__ import annotations

# Categories for LINEAR features that OSM splits into many `way` segments sharing one name
# (a river/canal runs as several ways; a promenade too). Each segment is a distinct object with
# its own id, so id-based dedup lets the SAME river be narrated once per segment ("проходишь мимо
# речки Чуры" twice). These are deduped by NAME instead — see build_candidates. Area water
# (ponds/lakes = "water") is NOT here: a pond is one object, not a split line.
LINEAR_CATEGORIES: frozenset[str] = frozenset({"river", "pedestrian"})


WEIGHT_BY_CATEGORY: dict[str, float] = {
    # landmarks / culture
    "museum": 0.9,
    "gallery": 0.9,
    "monument": 0.9,
    "memorial": 0.9,
    "castle": 0.9,
    "attraction": 0.85,
    "fort": 0.85,
    "ruins": 0.8,
    "archaeological_site": 0.8,
    "place_of_worship": 0.8,
    "manor": 0.8,
    "estate": 0.7,
    "heritage": 0.7,
    "farm": 0.45,  # farmhouse / barn / stable — rural heritage buildings
    "historic": 0.75,
    "viewpoint": 0.6,
    "artwork": 0.6,
    "theatre": 0.6,
    "arts_centre": 0.6,
    "concert_hall": 0.6,
    "cinema": 0.55,
    "palace": 0.9,
    "monastery": 0.85,
    # civic / cultural institutions
    "townhall": 0.7,
    "courthouse": 0.55,
    "university": 0.55,
    "college": 0.5,
    "library": 0.55,
    "marketplace": 0.55,
    "exhibition_centre": 0.5,
    "train_station": 0.6,
    "school": 0.4,
    "kindergarten": 0.35,
    "hospital": 0.5,
    "clinic": 0.45,
    "community_centre": 0.4,
    "club": 0.4,
    "sports_centre": 0.45,
    "pitch": 0.4,
    "track": 0.4,
    "sports_hall": 0.42,
    "recreation_ground": 0.4,
    "square": 0.55,
    "pedestrian": 0.4,
    "stadium": 0.55,
    "marina": 0.45,
    "common": 0.4,
    "cemetery": 0.55,
    # green & nature
    "nature_reserve": 0.7,
    "peak": 0.7,
    "hill": 0.5,
    "ridge": 0.5,
    "waterfall": 0.75,
    "geyser": 0.8,
    "volcano": 0.85,
    "glacier": 0.8,
    "reservoir": 0.65,
    "water": 0.6,
    "river": 0.55,
    "spring": 0.6,
    "beach": 0.6,
    "bay": 0.6,
    "cliff": 0.6,
    "cave_entrance": 0.65,
    "arch": 0.7,
    "rock": 0.5,
    "wetland": 0.5,
    "forest": 0.45,
    "wood": 0.45,
    "orchard": 0.45,
    "vineyard": 0.5,
    "allotments": 0.4,
    "park": 0.5,
    "garden": 0.5,
    # notable structures
    "lighthouse": 0.8,
    "tower": 0.6,
    "bridge": 0.55,
    "windmill": 0.6,
    "watermill": 0.6,
    "obelisk": 0.6,
    "aqueduct": 0.75,
    "city_gate": 0.75,
    "water_tower": 0.5,
    "gasometer": 0.45,
    "telescope": 0.6,
    "fountain": 0.45,
    # everyday / commercial
    "cafe": 0.3,
    "restaurant": 0.3,
    "bar": 0.3,
    "pub": 0.3,
    "fast_food": 0.25,
    "shop": 0.25,
    "building": 0.15,
    "place": 0.2,
}
DEFAULT_WEIGHT = 0.2

# tags worth keeping on the Place (used by enrichment later, and by
# languages.display_name to localize the title to the session language — so we keep
# the localized name:<lang> tags for every supported guide language, not just en).
KEEP_TAGS = frozenset(
    {
        "name",
        "name:en",
        "name:ru",
        "name:es",
        "name:fr",
        "name:de",
        "name:it",
        "name:pt",
        "name:zh",
        "int_name",
        "wikidata",
        "wikipedia",
        "tourism",
        "historic",
        "amenity",
        "leisure",
        "natural",
        "water",
        "waterway",
        "landuse",
        "man_made",
        "shop",
        "building",
        "religion",
        "place",
        "highway",
        "club",
        "ruins",
        "heritage",
        "sport",
        "healthcare",
    }
)


def weight_for(category: str) -> float:
    return WEIGHT_BY_CATEGORY.get(category, DEFAULT_WEIGHT)


def classify(tags: dict[str, str]) -> tuple[str, float]:
    """Return (category, weight) for a set of OSM tags."""
    category = _category(tags)
    return category, weight_for(category)


# Private service / commerce that carries no sightseeing value — the "junk" the wide amenity
# search now drags in (private clinics, dentists, vet clinics, pharmacies, kindergartens /
# child-development centres, social facilities). Kept OUT: hospital + school (large public
# landmarks the user wants to keep). Cut by TAG, not by weight/threshold, so nothing else
# is affected. See is_junk.
_JUNK_AMENITIES: frozenset[str] = frozenset({
    "clinic", "doctors", "dentist", "veterinary", "childcare", "kindergarten",
    "pharmacy", "social_facility", "nursing_home",
})


def is_junk(tags: dict[str, str] | None) -> bool:
    """True for a private service/commerce object that shouldn't be shown/narrated/routed. A
    cultural/historic anchor (tourism/historic/heritage) always wins — a museum in a former
    clinic stays. Otherwise: a junk `amenity`, or ANY `healthcare=*` except a hospital (catches
    dentists/doctors/physio/labs/vet tagged only under healthcare)."""
    if not tags:
        return False
    if tags.get("tourism") or tags.get("historic") or tags.get("heritage"):
        return False
    if tags.get("amenity") in _JUNK_AMENITIES:
        return True
    healthcare = tags.get("healthcare")
    return bool(healthcare) and healthcare != "hospital"


def _category(t: dict[str, str]) -> str:
    tourism = t.get("tourism")
    if tourism in {"museum", "gallery"}:
        return tourism
    if tourism in {"attraction", "artwork", "viewpoint"}:
        return tourism

    historic = t.get("historic")
    if historic:
        if historic in {
            "monument",
            "memorial",
            "castle",
            "fort",
            "ruins",
            "archaeological_site",
            "manor",
            "estate",
        }:
            return historic
        return "historic"

    # standalone ruins (ruins=stable/church/yes) with no historic= key
    if t.get("ruins") and t.get("ruins") != "no":
        return "ruins"

    if t.get("club"):  # biker/social/sport club-house
        return "club"

    amenity = t.get("amenity")
    if amenity == "place_of_worship":
        return "place_of_worship"
    if amenity == "monastery":
        return "monastery"
    if amenity == "grave_yard":
        return "cemetery"
    if amenity in {
        "theatre",
        "cinema",
        "arts_centre",
        "concert_hall",
        "townhall",
        "courthouse",
        "university",
        "college",
        "library",
        "marketplace",
        "exhibition_centre",
        "school",
        "kindergarten",
        "hospital",
        "clinic",
        "community_centre",
        "cafe",
        "restaurant",
        "bar",
        "pub",
        "fast_food",
    }:
        return amenity

    # named squares & pedestrian promenades — the connective tissue of a city walk
    if t.get("place") == "square":
        return "square"
    if t.get("highway") == "pedestrian":
        return "pedestrian"

    leisure = t.get("leisure")
    if leisure in {
        "park", "garden", "nature_reserve", "common", "marina", "stadium", "sports_centre",
        "pitch", "track", "sports_hall", "recreation_ground",
    }:
        return leisure

    # nature & water
    if t.get("landuse") == "reservoir" or t.get("water") == "reservoir":
        return "reservoir"
    natural = t.get("natural")
    if natural in {
        "water", "wood", "peak", "hill", "ridge", "bay", "beach", "cape", "cliff",
        "cave_entrance", "arch", "rock", "spring", "geyser", "waterfall",
        "volcano", "glacier", "wetland",
    }:
        return natural
    if "water" in t:
        return "water"
    waterway = t.get("waterway")
    if waterway in {"river", "canal", "waterfall", "dam", "lock", "weir"}:
        return "waterfall" if waterway == "waterfall" else "river"
    landuse = t.get("landuse")
    if landuse in {"forest", "orchard", "vineyard", "allotments", "cemetery"}:
        return landuse

    man_made = t.get("man_made")
    if man_made in {
        "bridge", "tower", "lighthouse", "watermill", "windmill", "obelisk",
        "aqueduct", "water_tower", "city_gate", "gasometer", "telescope",
    }:
        return man_made
    if t.get("amenity") == "fountain":
        return "fountain"

    # landmark buildings — map the notable subtypes to their cultural category so
    # they rank like the real thing, not a generic "building".
    building = t.get("building")
    if building in {"cathedral", "church", "chapel", "temple", "mosque", "synagogue"}:
        return "place_of_worship"
    if building == "monastery":
        return "monastery"
    if building == "palace":
        return "palace"
    if building in {"castle", "fort"}:
        return "castle"
    if building == "manor":
        return "manor"
    if building == "ruins":
        return "ruins"
    if building in {"farmhouse", "barn", "stable"}:
        return "farm"
    if building == "school":
        return "school"
    if building == "train_station":
        return "train_station"
    if building == "triumphal_arch":
        return "arch"
    if building == "gatehouse":
        return "city_gate"
    if building in {
        "government", "townhall", "university", "library", "theatre",
        "museum", "tower", "stadium", "windmill",
    }:
        return building

    # heritage-listed / protected site with no better category above
    if t.get("heritage"):
        return "heritage"

    if "shop" in t:
        return "shop"
    if "building" in t:  # an ordinary building with no other interesting tag
        return "building"
    return "place"
