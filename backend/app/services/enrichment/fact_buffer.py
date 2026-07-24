from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path

from app.services.agent.languages import normalize

_SCOPE_PLACE = "place"
_SCOPE_AREA = "area"
_SCOPE_STREET = "street"
_SCOPE_DISTRICT = "district"
_SCOPE_CITY = "city"
_DEFAULT_LANG = "ru"
_READY = "ready"
_DRY = "dry"


@dataclass(frozen=True)
class FactBatchMeta:
    source_tier: str | None = None
    status: str = _READY
    fetched_at: float | None = None
    last_attempt_at: float | None = None
    expires_at: float | None = None
    fact_count: int | None = None
    char_count: int | None = None


class FactBuffer:
    """Small persistent fact buffer shared by object and area warming paths.

    This is intentionally narrower than a full content-addressed store: it persists the
    factual substrate we can cheaply reuse across startup, guided routing, and transient
    network loss, while rendered narration stays in the existing in-memory warm caches.

    The buffer now has a generic subject layer under the legacy place/area helpers, so the
    same persistence seam can back wider hierarchy warming (`street`/`district`/`city`) and
    keep lightweight metadata about freshness / coverage without changing current callers.
    """

    def __init__(self, path: str = "") -> None:
        self._path = Path(path) if path else None
        self._subject_mem: dict[tuple[str, str, str, int], str] = {}
        self._meta_mem: dict[tuple[str, str, str, int], FactBatchMeta] = {}
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _init_db(self) -> None:
        assert self._path is not None
        with sqlite3.connect(self._path) as db:
            db.execute(
                """
                create table if not exists fact_buffer (
                    scope text not null,
                    subject_key text not null,
                    language text not null,
                    angle integer not null default 0,
                    facts text not null,
                    fetched_at real not null,
                    primary key (scope, subject_key, language, angle)
                )
                """
            )
            db.execute(
                """
                create table if not exists fact_buffer_meta (
                    scope text not null,
                    subject_key text not null,
                    language text not null,
                    angle integer not null default 0,
                    source_tier text,
                    status text not null,
                    fetched_at real,
                    last_attempt_at real not null,
                    expires_at real,
                    fact_count integer,
                    char_count integer,
                    primary key (scope, subject_key, language, angle)
                )
                """
            )
            db.execute(
                "create index if not exists idx_fact_buffer_fetched on fact_buffer(fetched_at)"
            )
            db.execute(
                "create index if not exists idx_fact_buffer_meta_status on fact_buffer_meta(status, last_attempt_at)"
            )
            db.commit()

    def _key(self, scope: str, subject_key: str, language: str, angle: int) -> tuple[str, str, str, int]:
        return scope, subject_key, normalize(language), angle

    def _estimate_fact_count(self, facts: str) -> int:
        text = (facts or "").strip()
        if not text:
            return 0
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]
        return max(1, len(parts))

    def _normalize_meta(self, facts: str, meta: FactBatchMeta | None) -> FactBatchMeta:
        now = time.time()
        base = meta or FactBatchMeta()
        text = (facts or "").strip()
        return replace(
            base,
            status=base.status or (_READY if text else _DRY),
            fetched_at=base.fetched_at if base.fetched_at is not None else (now if text else None),
            last_attempt_at=base.last_attempt_at if base.last_attempt_at is not None else now,
            fact_count=base.fact_count if base.fact_count is not None else self._estimate_fact_count(text),
            char_count=base.char_count if base.char_count is not None else len(text),
        )

    def _get_db(self, scope: str, subject_key: str, language: str, angle: int) -> str | None:
        if not self._path:
            return None
        with sqlite3.connect(self._path) as db:
            row = db.execute(
                "select facts from fact_buffer where scope=? and subject_key=? and language=? and angle=?",
                self._key(scope, subject_key, language, angle),
            ).fetchone()
        return row[0] if row else None

    def _get_meta_db(self, scope: str, subject_key: str, language: str, angle: int) -> FactBatchMeta | None:
        if not self._path:
            return None
        with sqlite3.connect(self._path) as db:
            row = db.execute(
                """
                select source_tier, status, fetched_at, last_attempt_at, expires_at, fact_count, char_count
                from fact_buffer_meta
                where scope=? and subject_key=? and language=? and angle=?
                """,
                self._key(scope, subject_key, language, angle),
            ).fetchone()
        if not row:
            return None
        return FactBatchMeta(
            source_tier=row[0],
            status=row[1],
            fetched_at=row[2],
            last_attempt_at=row[3],
            expires_at=row[4],
            fact_count=row[5],
            char_count=row[6],
        )

    def _put_db(self, scope: str, subject_key: str, language: str, angle: int, facts: str) -> None:
        if not self._path:
            return
        now = time.time()
        with sqlite3.connect(self._path) as db:
            db.execute(
                """
                insert into fact_buffer(scope, subject_key, language, angle, facts, fetched_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(scope, subject_key, language, angle)
                do update set facts=excluded.facts, fetched_at=excluded.fetched_at
                """,
                (*self._key(scope, subject_key, language, angle), facts, now),
            )
            db.commit()

    def _put_meta_db(
        self,
        scope: str,
        subject_key: str,
        language: str,
        angle: int,
        meta: FactBatchMeta,
    ) -> None:
        if not self._path:
            return
        with sqlite3.connect(self._path) as db:
            db.execute(
                """
                insert into fact_buffer_meta(
                    scope, subject_key, language, angle,
                    source_tier, status, fetched_at, last_attempt_at, expires_at, fact_count, char_count
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(scope, subject_key, language, angle)
                do update set
                    source_tier=excluded.source_tier,
                    status=excluded.status,
                    fetched_at=excluded.fetched_at,
                    last_attempt_at=excluded.last_attempt_at,
                    expires_at=excluded.expires_at,
                    fact_count=excluded.fact_count,
                    char_count=excluded.char_count
                """,
                (
                    *self._key(scope, subject_key, language, angle),
                    meta.source_tier,
                    meta.status,
                    meta.fetched_at,
                    meta.last_attempt_at,
                    meta.expires_at,
                    meta.fact_count,
                    meta.char_count,
                ),
            )
            db.commit()

    def get_subject(
        self,
        scope: str,
        subject_key: str,
        language: str = _DEFAULT_LANG,
        *,
        angle: int = 0,
    ) -> str | None:
        key = self._key(scope, subject_key, language, angle)
        if key in self._subject_mem:
            return self._subject_mem[key]
        facts = self._get_db(scope, subject_key, language, angle)
        if facts is not None:
            self._subject_mem[key] = facts
        return facts

    def put_subject(
        self,
        scope: str,
        subject_key: str,
        facts: str,
        language: str = _DEFAULT_LANG,
        *,
        angle: int = 0,
        meta: FactBatchMeta | None = None,
    ) -> None:
        key = self._key(scope, subject_key, language, angle)
        self._subject_mem[key] = facts
        norm_meta = self._normalize_meta(facts, meta)
        self._meta_mem[key] = norm_meta
        self._put_db(scope, subject_key, language, angle, facts)
        self._put_meta_db(scope, subject_key, language, angle, norm_meta)

    def has_subject(
        self,
        scope: str,
        subject_key: str,
        language: str = _DEFAULT_LANG,
        *,
        angle: int = 0,
    ) -> bool:
        return self.get_subject(scope, subject_key, language, angle=angle) is not None

    def get_subject_meta(
        self,
        scope: str,
        subject_key: str,
        language: str = _DEFAULT_LANG,
        *,
        angle: int = 0,
    ) -> FactBatchMeta | None:
        key = self._key(scope, subject_key, language, angle)
        if key in self._meta_mem:
            return self._meta_mem[key]
        meta = self._get_meta_db(scope, subject_key, language, angle)
        if meta is not None:
            self._meta_mem[key] = meta
        return meta

    def record_subject_attempt(
        self,
        scope: str,
        subject_key: str,
        language: str = _DEFAULT_LANG,
        *,
        angle: int = 0,
        status: str = _DRY,
        source_tier: str | None = None,
        expires_at: float | None = None,
    ) -> None:
        key = self._key(scope, subject_key, language, angle)
        meta = FactBatchMeta(
            source_tier=source_tier,
            status=status,
            fetched_at=None,
            last_attempt_at=time.time(),
            expires_at=expires_at,
            fact_count=0,
            char_count=0,
        )
        self._meta_mem[key] = meta
        self._put_meta_db(scope, subject_key, language, angle, meta)

    def get_place(self, place_id: str, language: str = _DEFAULT_LANG) -> str | None:
        return self.get_subject(_SCOPE_PLACE, place_id, language)

    def put_place(
        self,
        place_id: str,
        facts: str,
        language: str = _DEFAULT_LANG,
        *,
        meta: FactBatchMeta | None = None,
    ) -> None:
        self.put_subject(_SCOPE_PLACE, place_id, facts, language, meta=meta)

    def has_place(self, place_id: str, language: str = _DEFAULT_LANG) -> bool:
        return self.has_subject(_SCOPE_PLACE, place_id, language)

    def get_area(self, area_key: str | None, language: str = _DEFAULT_LANG, *, angle: int = 0) -> str | None:
        if not area_key:
            return None
        return self.get_subject(_SCOPE_AREA, area_key, language, angle=angle)

    def put_area(
        self,
        area_key: str | None,
        facts: str,
        language: str = _DEFAULT_LANG,
        *,
        angle: int = 0,
        meta: FactBatchMeta | None = None,
    ) -> None:
        if not area_key:
            return
        self.put_subject(_SCOPE_AREA, area_key, facts, language, angle=angle, meta=meta)

    def dump_debug(self) -> dict[str, list[dict[str, object]]]:
        debug: dict[str, list[dict[str, object]]] = {
            "subjects": [],
            "metadata": [],
        }
        for (scope, subject_key, language, angle), facts in self._subject_mem.items():
            debug["subjects"].append(
                {
                    "scope": scope,
                    "subject_key": subject_key,
                    "language": language,
                    "angle": angle,
                    "facts": facts,
                }
            )
        for (scope, subject_key, language, angle), meta in self._meta_mem.items():
            debug["metadata"].append(
                {
                    "scope": scope,
                    "subject_key": subject_key,
                    "language": language,
                    "angle": angle,
                    "source_tier": meta.source_tier,
                    "status": meta.status,
                    "fetched_at": meta.fetched_at,
                    "last_attempt_at": meta.last_attempt_at,
                    "expires_at": meta.expires_at,
                    "fact_count": meta.fact_count,
                    "char_count": meta.char_count,
                }
            )
        return debug
