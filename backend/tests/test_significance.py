"""significance_from_weight — the type-weight buckets, the factless-downgrade invariant,
and the wiki-richness lift."""

from app.services.agent.significance import (
    significance_from_weight,
    tags_have_wiki,
)
from app.shared.schemas import Significance


def test_buckets_from_weight():
    assert significance_from_weight(0.9, True) is Significance.LANDMARK
    assert significance_from_weight(0.7, True) is Significance.HIGH
    assert significance_from_weight(0.5, True) is Significance.MEDIUM
    assert significance_from_weight(0.3, True) is Significance.LOW
    assert significance_from_weight(0.1, True) is Significance.SKIP


def test_factless_never_landmark():
    # the "only facts" invariant: no facts => never LANDMARK, even at top weight
    assert significance_from_weight(0.9, False) is Significance.HIGH


def test_wiki_lifts_one_tier_when_facts():
    # MEDIUM weight + facts + a wiki link => lifted to HIGH
    assert significance_from_weight(0.5, True, has_wiki=True) is Significance.HIGH
    # without the wiki link it stays MEDIUM
    assert significance_from_weight(0.5, True) is Significance.MEDIUM


def test_wiki_lift_saturates_at_landmark():
    # HIGH weight + facts + wiki => LANDMARK (one tier up); already-LANDMARK stays put
    assert significance_from_weight(0.7, True, has_wiki=True) is Significance.LANDMARK
    assert significance_from_weight(0.9, True, has_wiki=True) is Significance.LANDMARK


def test_wiki_never_conjures_a_factless_landmark():
    # wiki link but NO facts: downgrade caps at HIGH and the lift requires facts => HIGH
    assert significance_from_weight(0.7, False, has_wiki=True) is Significance.HIGH


def test_wiki_does_not_revive_a_skip():
    # a bench (sub-0.25 weight) with a stray wikidata tag stays SKIP
    assert significance_from_weight(0.1, True, has_wiki=True) is Significance.SKIP


def test_tags_have_wiki():
    assert tags_have_wiki({"wikipedia": "ru:Кремль"}) is True
    assert tags_have_wiki({"wikidata": "Q1"}) is True
    assert tags_have_wiki({"building": "yes"}) is False
    assert tags_have_wiki(None) is False
    assert tags_have_wiki({}) is False
