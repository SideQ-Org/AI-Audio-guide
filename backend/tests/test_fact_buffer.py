from pathlib import Path

from app.services.enrichment.enricher import EnrichmentCache
from app.services.enrichment.fact_buffer import FactBatchMeta, FactBuffer


def test_fact_buffer_persists_place_and_area_facts(tmp_path: Path):
    path = tmp_path / "fact-buffer.sqlite3"
    buf1 = FactBuffer(str(path))
    buf1.put_place("place-1", "museum fact", "ru")
    buf1.put_area("district-1", "district fact", "ru", angle=0)
    buf2 = FactBuffer(str(path))
    assert buf2.get_place("place-1", "ru") == "museum fact"
    assert buf2.get_area("district-1", "ru", angle=0) == "district fact"


def test_fact_buffer_supports_generic_subject_scopes_and_metadata(tmp_path: Path):
    path = tmp_path / "fact-buffer.sqlite3"
    buf1 = FactBuffer(str(path))
    buf1.put_subject(
        "street",
        "street:moscow|tverskaya",
        "Street facts.",
        "ru",
        angle=1,
        meta=FactBatchMeta(source_tier="web", expires_at=1234.5),
    )
    buf1.record_subject_attempt(
        "city",
        "city:moscow|ru",
        "ru",
        angle=2,
        status="dry",
        source_tier="free",
        expires_at=5678.0,
    )

    buf2 = FactBuffer(str(path))
    assert (
        buf2.get_subject("street", "street:moscow|tverskaya", "ru", angle=1)
        == "Street facts."
    )
    meta = buf2.get_subject_meta("street", "street:moscow|tverskaya", "ru", angle=1)
    assert meta is not None
    assert meta.source_tier == "web"
    assert meta.status == "ready"
    assert meta.fact_count == 1
    assert meta.char_count == len("Street facts.")

    dry = buf2.get_subject_meta("city", "city:moscow|ru", "ru", angle=2)
    assert dry is not None
    assert dry.status == "dry"
    assert dry.source_tier == "free"
    assert dry.fact_count == 0
    assert dry.char_count == 0


def test_enrichment_cache_reads_through_fact_buffer(tmp_path: Path):
    path = tmp_path / "fact-buffer.sqlite3"
    buf = FactBuffer(str(path))
    buf.put_place("place-2", "cached fact", "ru")
    cache = EnrichmentCache(buf)
    assert cache.get("place-2", "ru") == "cached fact"
    assert cache.has("place-2", "ru") is True
