"""Corpus loading/splitting + eval harness (Block 4 Phase 1). Offline, no keys."""

from __future__ import annotations

import json

from sim.interest_corpus import Sample, load_e2e, split
from sim.interest_eval import evaluate

_E2E = {
    "scenarios": [
        {
            "key": "msk-red-square", "kind": "турист", "lang": "ru",
            "narrations": [
                {"place": "Мавзолей", "sig": "HIGH", "text": "Тело забальзамировали в 1924 году."},
                {"place": "ГУМ", "sig": "LOW", "text": ""},  # empty -> skipped
                {"place": "Собор", "sig": "HIGH", "text": "Храм возвели при Иване Грозном."},
            ],
        },
        {
            "key": "spb-kupchino", "kind": "окраина", "lang": "ru",
            "narrations": [
                {"place": "Швейк", "sig": "LOW", "text": "Памятник бравому солдату Швейку."},
            ],
        },
    ]
}


def _write(tmp_path):
    p = tmp_path / "e2e_results.json"
    p.write_text(json.dumps(_E2E, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_e2e_skips_empty_and_carries_context(tmp_path):
    samples = load_e2e(_write(tmp_path))
    assert len(samples) == 3  # the empty-text ГУМ narration is dropped
    s = samples[0]
    assert s.region == "msk-red-square"
    assert s.kind == "турист"
    assert s.language == "ru"
    assert s.significance == "HIGH"
    assert not s.has_facts  # e2e source carries no facts


def test_load_missing_file_returns_empty(tmp_path):
    assert load_e2e(tmp_path / "nope.json") == []


def test_split_is_deterministic_and_total_preserving():
    samples = [
        Sample(id=f"r{r}:{i}", region=f"r{r}", kind="k", language="ru", text=f"текст номер {r}-{i}")
        for r in range(4)
        for i in range(25)
    ]
    a = split(samples)
    b = split(samples)
    # deterministic: identical partition across calls
    assert {k: [s.id for s in v] for k, v in a.items()} == {
        k: [s.id for s in v] for k, v in b.items()
    }
    # total-preserving + disjoint
    all_ids = {s.id for s in samples}
    got: set[str] = set()
    for v in a.values():
        got |= {s.id for s in v}
    assert got == all_ids
    assert sum(len(v) for v in a.values()) == len(samples)
    # a train-heavy split exists and holdout is non-empty at this size
    assert len(a["train"]) > len(a["holdout"]) > 0


def test_evaluate_produces_region_scores():
    samples = load_e2e_from_dict()
    scores = evaluate(samples)
    regions = {r.region for r in scores}
    assert regions == {"msk-red-square", "spb-kupchino"}
    for r in scores:
        assert 0.0 <= r.score <= 100.0
        assert r.n >= 1


def load_e2e_from_dict() -> list[Sample]:
    out = []
    for sc in _E2E["scenarios"]:
        for i, n in enumerate(sc["narrations"]):
            if not n["text"].strip():
                continue
            out.append(Sample(
                id=f"{sc['key']}:{i}", region=sc["key"], kind=sc["kind"],
                language=sc["lang"], text=n["text"], place=n["place"], significance=n["sig"],
            ))
    return out
