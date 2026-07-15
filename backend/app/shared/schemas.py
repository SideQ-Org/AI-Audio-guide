"""Shared domain & transport schemas — the single source of truth for all roles.

Grouped as:
  * primitives        — GeoPoint, Address, enums
  * domain            — Place, Candidate, ControlPatch
  * role I/O          — Scorer / Narrator / Companion inputs & outputs
  * session           — SessionState
  * websocket         — client<->server message contract
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.shared.memory import WalkMemory


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
class Significance(StrEnum):
    SKIP = "SKIP"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    LANDMARK = "LANDMARK"


class GazeConfidence(StrEnum):
    HIGH = "high"
    LOW = "low"


class Pace(StrEnum):
    STILL = "still"
    SLOW = "slow"
    FAST = "fast"


class GeoPoint(BaseModel):
    lat: float
    lon: float


class Address(BaseModel):
    country: str | None = None
    city: str | None = None
    district: str | None = None
    street: str | None = None
    # True only when the user is close enough to the resolved street to assert it
    # ("идёшь по улице X"); otherwise the guide speaks of "здесь/в районе" (P3/wish c).
    street_confident: bool = False


class Heading(BaseModel):
    direction_deg: float | None = None  # bearing 0..360; None if unknown
    gaze_confidence: GazeConfidence = GazeConfidence.LOW


# --------------------------------------------------------------------------- #
# domain
# --------------------------------------------------------------------------- #
class Place(BaseModel):
    id: str
    name: str
    category: str  # museum, park, shop, church, memorial, ...
    location: GeoPoint  # representative point (for a polygon/line: a boundary vertex)
    # Downsampled way outline [[lat, lon], ...] for polygons/lines, so ranking can
    # measure distance to the whole shape from the LIVE position (0 when inside) instead
    # of to a single stale snapped vertex (B1). None for point objects (nodes).
    geometry: list[list[float]] | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class Candidate(BaseModel):
    place: Place
    distance_m: float
    type_weight: float
    in_gaze_cone: bool
    gaze_confidence: GazeConfidence
    facts_available: bool = False
    facts_snippet: str | None = None
    # Spatial side relative to heading: "ahead"/"behind" are knowable from the GPS
    # course; "left"/"right" only when gaze_confidence=high (a real facing/compass).
    # None means lateral but confidence too low to call a side.
    relative_bearing_deg: float | None = None
    side: str | None = None


class ControlPatch(BaseModel):
    """User-driven steering extracted by the Companion."""

    skip_categories: list[str] = Field(default_factory=list)
    focus_topics: list[str] = Field(default_factory=list)
    verbosity: Literal["shorter", "normal", "longer"] | None = None
    mute: bool = False


# --------------------------------------------------------------------------- #
# role I/O — Scorer
# --------------------------------------------------------------------------- #
class ScorerInput(BaseModel):
    candidates: list[Candidate]
    address: Address = Field(default_factory=Address)
    seen: list[str] = Field(default_factory=list)
    preferences: ControlPatch | None = None
    language: str = "ru"


class ScoredPlace(BaseModel):
    place_id: str
    significance: Significance
    reason: str = ""


class ScorerOutput(BaseModel):
    scored: list[ScoredPlace] = Field(default_factory=list)
    next: str | None = None
    expand_radius: bool = False


# --------------------------------------------------------------------------- #
# role I/O — Narrator
# --------------------------------------------------------------------------- #
class NarrationContext(BaseModel):
    time_of_day: str | None = None
    city: str | None = None
    district: str | None = None
    street: str | None = None
    street_confident: bool = False  # assert the street only when true (P3/wish c)


class NarratorFlags(BaseModel):
    switching: bool = False
    nothing_new: bool = False
    elaborate: bool = False  # tell MORE about an already-covered place (nothing new nearby)
    passing: bool = False  # user is right beside this object — introduce it, never SKIP
    # The object is already BEHIND the walker (a lower-priority newcomer covered after a
    # higher-priority object finished). Frame it in the past ("мы прошли …"), never "проходишь
    # мимо". `passing` stays true so the never-dead-air floor still applies.
    passed: bool = False
    # The walker RETURNED to a place told earlier this walk — acknowledge it briefly and add a
    # FRESH detail (see the REVISIT block), never re-tell what HISTORY already covered.
    revisit: bool = False
    preferences: ControlPatch | None = None


class CallbackRef(BaseModel):
    """A pointer to an earlier-narrated object worth referencing when telling a related one."""

    name: str
    category: str = ""


class LookaheadRef(BaseModel):
    """A notable object coming up ahead — lets the narrator tease it ('впереди справа, метрах в
    ста — усадьба') so the tour reads as a forward-leaning story AND the walker knows where to
    look. `distance_m`/`side` come from the candidate; `side` is left/right only when the facing
    is trustworthy (else "ahead"/None), so the narrator never invents a direction."""

    name: str
    category: str = ""
    distance_m: float | None = None
    side: str | None = None


class NarratorInput(BaseModel):
    place: Place
    significance: Significance
    facts: str | None = None
    distance_m: float
    heading: Heading = Field(default_factory=Heading)
    side: str | None = None  # ahead|behind|left|right (left/right only at high gaze)
    # True when the object is in the forward gaze cone AND within the narrate bubble —
    # i.e. the user can actually SEE it now, vs. merely being close (behind/lateral).
    # Lets the narrator say "вон то, перед тобой" vs "проходишь мимо / не видно" (A5/P6).
    in_view: bool = False
    pace: Pace = Pace.SLOW
    context: NarrationContext = Field(default_factory=NarrationContext)
    history: list[str] = Field(default_factory=list)
    flags: NarratorFlags = Field(default_factory=NarratorFlags)
    # narrative arc — so the object is woven INTO the running story, not dropped in
    theme: str | None = None  # the through-line to keep the object inside
    told: list[str] = Field(default_factory=list)  # topics/places already covered (don't repeat)
    next_hook: str | None = None  # the transition the previous paragraph set up
    # An earlier-narrated object this one relates to — lets the narrator weave a brief callback
    # ("как та церковь, что мы видели раньше…") for a coherent story instead of disconnected blurbs.
    callback: CallbackRef | None = None
    # A notable object coming up ahead — the narrator may tease it so the tour leans forward.
    lookahead: LookaheadRef | None = None
    # When elaborating (FLAGS.elaborate), the facet to approach the object from this time
    # (history/people/function/detail/context) so successive follow-ups go DEEPER from a
    # DIFFERENT angle instead of rewording the same fact. None on a normal first narration.
    elaborate_angle: str | None = None
    language: str = "ru"


# --------------------------------------------------------------------------- #
# role I/O — Area narrator (the "general -> specific" monologue spine)
# --------------------------------------------------------------------------- #
class AreaInput(BaseModel):
    """One beat of the area-level monologue: advance the story arc about the
    city / district / street, bridging the gaps between objects."""

    address: Address = Field(default_factory=Address)
    facts: str | None = None  # verified area facts (web), may be empty
    theme: str | None = None  # the through-line for this area
    topic: str | None = None  # the specific outline topic this beat should cover
    told: list[str] = Field(default_factory=list)  # covered topics/places (don't repeat)
    next_hook: str | None = None  # transition the previous paragraph set up
    last_place_name: str | None = None  # to weave a smooth return from the last object
    history: list[str] = Field(default_factory=list)
    pace: Pace = Pace.SLOW
    # Rotating rhetorical angle for this beat (observation|history|human|sensory|
    # transition) so consecutive area paragraphs differ in SHAPE, not just wording (A1).
    beat_mode: str | None = None
    language: str = "ru"


# --------------------------------------------------------------------------- #
# role I/O — Planner (forms the story arc for a freshly entered area)
# --------------------------------------------------------------------------- #
class PlannerInput(BaseModel):
    address: Address = Field(default_factory=Address)
    facts: str | None = None  # verified area facts, if already fetched
    theme_override: str | None = None  # a topic the user explicitly asked for
    language: str = "ru"


class PlannerOutput(BaseModel):
    theme: str = ""  # the through-line for this area (one phrase)
    outline: list[str] = Field(default_factory=list)  # 3-5 ordered topics to cover
    opener: str = ""  # the spoken opening paragraph (introduces area + theme)


# --------------------------------------------------------------------------- #
# role I/O — Companion
# --------------------------------------------------------------------------- #
class CompanionInput(BaseModel):
    user_message: str
    context: NarrationContext = Field(default_factory=NarrationContext)
    last_narration: str | None = None
    address: Address = Field(default_factory=Address)
    history: list[str] = Field(default_factory=list)
    language: str = "ru"
    # Two-tier answer: the fast tier already spoke this first sentence. The strong tier CONTINUES
    # from it (adds detail), must NOT repeat it — or returns [SILENCE] if nothing to add.
    already_said: str | None = None


class CompanionOutput(BaseModel):
    reply: str
    control_patch: ControlPatch | None = None


# --------------------------------------------------------------------------- #
# narrative plan (the story arc formed at session/area start, augmented en route)
# --------------------------------------------------------------------------- #
class NarrativePlan(BaseModel):
    area_key: str | None = None  # which area this plan was built for
    theme: str = ""  # the auto-chosen through-line for this area
    theme_override: str | None = None  # a topic the user picked (wins over `theme`)
    outline: list[str] = Field(default_factory=list)  # ordered topics to cover
    told: list[str] = Field(default_factory=list)  # covered topics/place-names (dedup)
    pending_focus: list[str] = Field(default_factory=list)  # user-asked topics to weave next
    next_hook: str | None = None  # transition note to the next paragraph

    def active_theme(self) -> str:
        return self.theme_override or self.theme

    def next_topic(self) -> str | None:
        """The first outline topic not yet covered (case-insensitive)."""
        told_lc = {t.lower() for t in self.told}
        for topic in self.outline:
            if topic.lower() not in told_lc:
                return topic
        return None


# --------------------------------------------------------------------------- #
# session
# --------------------------------------------------------------------------- #
class SessionState(BaseModel):
    session_id: str
    # Supabase user id (JWT `sub`) once the client authenticates over WS; None = guest
    # (no history written). Set on an `auth` message, degrades to None on an invalid
    # token. Forward-compatible: old clients never send `auth` and stay guests.
    user_id: str | None = None
    # Effective account tier for this session (feature: account tiers): "free" (DeepSeek
    # + wiki-only + ads + caps) | "paid" (Gemini + web facts + no ads + unlimited).
    # Set from the DB on an `auth` message; drives model/enrichment/quota. Guests = free.
    tier: str = "free"
    # durable walk-history bookkeeping (phase 4). walk_id = the current row in the
    # durable store; walk_last_event_at (epoch s) drives the gap-split so a long pause
    # on the same session starts a NEW walk. Both stay None for guests / when the
    # durable store is off — the history layer is never touched.
    walk_id: str | None = None
    walk_last_event_at: float | None = None
    language: str = "ru"
    # How the guide addresses the LISTENER grammatically: "masculine" | "feminine" | "" (neutral,
    # the default — avoid gendered 2nd-person forms). The user's optional, self-set choice.
    user_address: str = ""
    position: GeoPoint | None = None
    # Downsampled GPS breadcrumb of the current walk ([[lat, lon], ...]) so the walk
    # history can draw the real route. Appended in on_position (distance-gated, capped),
    # reset when a new walk starts, snapshotted into the durable walk row on each event.
    # A point walked while the tour is PAUSED carries a trailing 1.0 ([lat, lon, 1.0]) so
    # the history map can style that stretch differently; unpaused points stay 2-element.
    path: list[list[float]] = Field(default_factory=list)
    # Cumulative distance walked along the route (metres), accumulated with the breadcrumb.
    # Used as the revisit gate: an object is only re-narrated once the walker has covered this
    # much route SINCE it was told, so "снова тут" never fires right after the main narration.
    route_len_m: float = 0.0
    heading: Heading = Field(default_factory=Heading)
    pace: Pace = Pace.SLOW
    address: Address = Field(default_factory=Address)
    seen_place_ids: list[str] = Field(default_factory=list)
    # Cross-object anti-repeat, beyond id dedup (seen_place_ids). Together they stop the SAME
    # real-world thing (mapped as several OSM objects) being narrated twice:
    #  * linear features (river/promenade) by NAME (segments can be far apart);
    #  * same `wikidata=Q…` = the same entity (a landmark mapped as node+way+relation);
    #  * a same-named object within dedup_name_radius_m of a narrated one (a park's label+polygon).
    seen_linear_names: list[str] = Field(default_factory=list)
    seen_wikidata: list[str] = Field(default_factory=list)
    seen_named: list[tuple[str, float, float]] = Field(default_factory=list)  # (name, lat, lon)
    narration_history: list[str] = Field(default_factory=list)
    conversation: list[str] = Field(default_factory=list)
    control_patch: ControlPatch = Field(default_factory=ControlPatch)
    current_radius_m: float = 80.0
    last_place_id: str | None = None  # last narrated place (for switching detection)
    last_place: Place | None = None  # full last place (to elaborate when nothing new)
    last_significance: Significance | None = None
    elaboration_count: int = 0  # follow-ups already told about last_place
    last_candidate_fingerprint: str | None = None  # heuristic gate
    # Objects already tried by the reach fallback that produced silence (facts-less,
    # non-notable). Excluded from future reach attempts so a parked user doesn't
    # re-spend an LLM call on them every tick, and so a facts-rich object behind a
    # silencing one still gets reached. Notable/ambient objects never land here (they
    # are floored to a one-liner, never silenced). Ring-buffered.
    reach_exhausted_ids: list[str] = Field(default_factory=list)
    # area-level monologue (general -> specific spine)
    last_geo_pos: GeoPoint | None = None  # where address was last resolved (move-gated)
    last_street: str | None = None  # last resolved street (a change => weave a transition)
    area_key: str | None = None  # district|city signature; change => new area, reset below
    area_facts: str | None = None  # verified facts about the current area (fetched once)
    area_intro_done: bool = False  # the area opener (+ plan) was already delivered
    area_beats: int = 0  # area beats told in the current area (variety + bound)
    area_bridge_said: bool = False  # a "пройдём дальше" bridge already closed this lull
    # city -> district -> street cascade for the gap-filler monologue: keep telling
    # atypical facts at one level, descend when it runs dry, go quiet after street.
    area_level: int = 0  # 0=city, 1=district, 2=street (index into the levels present)
    area_level_beats: int = 0  # facts told at the current level (per-level soft budget)
    # Fact-less city fallback counter (see Orchestrator._area_line + area_cityless_max): caps
    # how many ungrounded city lines a dry stretch may emit before going quiet, since the
    # model fabricates fresh (non-repeating) specifics forever otherwise. Reset by object/area.
    area_cityless_beats: int = 0
    # the story arc — formed when an area is entered, augmented along the route
    narrative_plan: NarrativePlan = Field(default_factory=NarrativePlan)
    # working memory of the whole walk (narrative memory graph, phase 1): what was said,
    # which objects/topics were covered — anti-repeat over the ENTIRE walk (not the
    # narration_history window) and the substrate for callbacks / long-term memory.
    memory: WalkMemory = Field(default_factory=WalkMemory)
    state: str = "idle"  # FSM state name
    greeted: bool = False  # the instant session-opener greeting was already spoken (once)
    tick_seq: int = 0  # monotonic position-tick counter (walk-log correlation only)
    last_log_pos: GeoPoint | None = None  # last position printed to the walk log (move delta)


# --------------------------------------------------------------------------- #
# websocket contract
# --------------------------------------------------------------------------- #
# client -> server
class WSPositionUpdate(BaseModel):
    type: Literal["position"] = "position"
    # Bounded to valid WGS84 ranges: a garbage/out-of-range coordinate is rejected as a
    # ValidationError (→ error frame, socket stays) instead of driving Overpass/geocoder
    # with a nonsense point.
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    direction_deg: float | None = Field(default=None, ge=0.0, le=360.0)
    gaze_confidence: GazeConfidence = GazeConfidence.LOW
    pace: Pace = Pace.SLOW


class WSUserUtterance(BaseModel):
    type: Literal["utterance"] = "utterance"
    text: str


class WSAuth(BaseModel):
    """Identify the user over the WS (design §6). Sent as a message (not in the query)
    so the token doesn't leak into proxy access logs. Absent/invalid => guest session."""

    type: Literal["auth"] = "auth"
    token: str


class WSControl(BaseModel):
    type: Literal["control"] = "control"
    patch: ControlPatch


class WSSetLanguage(BaseModel):
    """Runtime language switch from the client (and on every (re)connect)."""

    type: Literal["language"] = "language"
    language: str  # ISO-639-1: en|ru|es|fr|de|it|pt|zh


class WSSetAddressForm(BaseModel):
    """The user's chosen grammatical form of address (sent on connect / when changed).
    "masculine" | "feminine" | "" (neutral). Optional — empty means address neutrally."""

    type: Literal["address_form"] = "address_form"
    form: str = ""


class WSAudioInput(BaseModel):
    type: Literal["audio"] = "audio"
    data_b64: str  # recorded clip (webm/opus, wav, ...) for STT
    format: str = "webm"


class WSPlayed(BaseModel):
    """Client finished speaking the current paragraph — the cadence signal that
    tells the server's narration producer to emit the next one."""

    type: Literal["played"] = "played"


class WSSetTheme(BaseModel):
    """User picked/voiced a topic for the tour to revolve around (empty => auto)."""

    type: Literal["theme"] = "theme"
    theme: str = ""


# server -> client
class WSPlaceItem(BaseModel):
    """One discovered object for the map (lite: no facts)."""

    id: str
    name: str
    category: str
    lat: float
    lon: float


class WSPlaces(BaseModel):
    """The full set of nearby objects found in the search disc — pinned on the map
    as the user walks (distinct from the single narrated place). Pushed whenever the
    inventory disc is (re)fetched."""

    type: Literal["places"] = "places"
    items: list[WSPlaceItem] = Field(default_factory=list)


class WSNarration(BaseModel):
    type: Literal["narration"] = "narration"
    text: str
    place_id: str | None = None
    final: bool = False
    # PAID sessions with neural TTS on: the spoken audio for `text`, base64-encoded, so the
    # client plays it instead of speaking with its on-device voice. Absent => client speaks
    # the text with flutter_tts (free tier / TTS off / synth failed).
    audio_b64: str | None = None
    audio_mime: str | None = None  # e.g. "audio/mpeg"


class WSReply(BaseModel):
    type: Literal["reply"] = "reply"
    text: str
    # Same optional neural audio as WSNarration (a spoken barge-in answer).
    audio_b64: str | None = None
    audio_mime: str | None = None


class WSSummary(BaseModel):
    """Structured end-of-walk recap, pushed after `end` (kept walk) for the Stop sheet."""

    type: Literal["summary"] = "summary"
    text: str


class WSStateUpdate(BaseModel):
    type: Literal["state"] = "state"
    state: str
