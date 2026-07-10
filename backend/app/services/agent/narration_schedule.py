"""Narration scheduler — delivers narration one sentence at a time so an object can be
WOVEN in at a sentence boundary instead of cutting a line mid-word.

The producer (main.py) drives it: it pulls the next sentence to speak, and when a place
enters the narrate bubble it asks the scheduler to pause the current line (its remaining
sentences are parked) and slot the object in; once the object is done the paused line
RESUMES (with a spoken connective), unless we've walked too far for it to still make sense.

Pure state + logic (no I/O), so it's unit-testable on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.services.agent.languages import resume_connective
from app.services.agent.narrator import split_sentences
from app.services.agent.orchestrator import OrchestratorOutput
from app.services.agent.significance import rank
from app.shared.geo_math import haversine_m
from app.shared.schemas import GeoPoint, Significance


@dataclass
class NarrItem:
    """One narration (object / area line / greeting), split into sentences, with a cursor
    at the next sentence to speak. `out` carries the place / state / significance so each
    emitted sentence keeps them."""

    out: OrchestratorOutput
    sentences: list[str] = field(default_factory=list)
    cursor: int = 0
    pause_pos: GeoPoint | None = None  # where we paused it (relevance check on resume)
    resumed: bool = False

    def has_next(self) -> bool:
        return self.cursor < len(self.sentences)

    def next_frame(self) -> OrchestratorOutput:
        """The out-frame for the next sentence (advances the cursor)."""
        s = self.sentences[self.cursor]
        self.cursor += 1
        return replace(self.out, text=s)

    @property
    def is_object(self) -> bool:
        return self.out.place_id is not None


class NarrationScheduler:
    def __init__(self, language: str = "ru") -> None:
        self.current: NarrItem | None = None
        self.paused: list[NarrItem] = []  # stack — most-recent interruption resumes first
        self.language = language
        self._resume_i = 0  # rotates the resume connective so it doesn't repeat verbatim

    def set_current(self, out: OrchestratorOutput) -> None:
        self.current = NarrItem(out, split_sentences(out.text) or [out.text])

    def next_frame(self) -> OrchestratorOutput | None:
        if self.current is not None and self.current.has_next():
            return self.current.next_frame()
        return None

    def pause_current(self, at: GeoPoint | None) -> bool:
        """Park the current line's REMAINING sentences to resume after a weave-in. Returns
        True if there was anything left to resume."""
        cur = self.current
        self.current = None
        if cur is not None and cur.has_next():
            cur.pause_pos = at
            self.paused.append(cur)
            return True
        return False

    def resume(self, live_pos: GeoPoint | None, max_dist_m: float) -> bool:
        """Make the most-recent still-relevant paused line current again, with a spoken
        connective. Discards lines we've walked away from (a resume there would be stale).
        Returns True if a line was resumed."""
        while self.paused:
            item = self.paused.pop()
            if not item.has_next():
                continue
            if (
                item.pause_pos is not None
                and live_pos is not None
                and haversine_m(live_pos, item.pause_pos) > max_dist_m
            ):
                continue  # walked too far — don't resume a line about somewhere behind us
            if not item.resumed:
                item.sentences.insert(
                    item.cursor, resume_connective(self.language, self._resume_i)
                )
                self._resume_i += 1
                item.resumed = True
            self.current = item
            return True
        return False

    def current_outranks(self, new_sig: Significance) -> bool:
        """True when the object being narrated outranks the newcomer — so we finish it in
        full and cover the newcomer briefly afterwards ('кстати, мы прошли') rather than
        interrupting. An area line / greeting always yields to an object (returns False)."""
        cur = self.current
        if cur is None or not cur.is_object or cur.out.significance is None:
            return False
        return rank(Significance(cur.out.significance)) >= rank(new_sig)
