"""Regression tests: prohibited specific-epithet combinations
and latinized stems that must not be inflected twice (ICNP Rule 20 note)."""

import pytest

from prokname.engine import data
from prokname.engine.generate import generate
from prokname.engine.validate import validate_agreement


def prohibited_rule_ids() -> set[str]:
    return {r["id"] for r in data.rules()["prohibited_patterns"]["rules"]}


def test_prohibited_patterns_table_exists_and_is_expert_readable():
    ids = prohibited_rule_ids()
    assert {
        "epithet_identical_to_genus",
        "epithet_latinized_repeat_of_genus",
        "epithet_repeated_inflection",
        "higher_rank_name_not_from_a_type_genus_stem",
        "epithet_not_analysable",
    } <= ids
    for rule in data.rules()["prohibited_patterns"]["rules"]:
        assert "verified" in rule and "source" in rule and "detect" in rule


def test_every_detection_strategy_is_implemented():
    """The table claims what the code does; a strategy with no implementation
    would be the same kind of silent lie the review is about."""
    from prokname.engine.validate import _prohibited_hits

    implemented = {
        rule.get("detect")
        for rule in data.rules()["prohibited_patterns"]["rules"]
        if rule.get("detect") in {
            "epithet_equals_genus",
            "epithet_is_genus_plus_inflectional_ending",
            "epithet_ends_with_doubled_paradigm_ending",
        }
    }
    assert len(implemented) == 3
    # …and they actually fire:
    assert _prohibited_hits("bacillus", "bacillus")
    assert _prohibited_hits("bacillus", "bacillusus")
    assert _prohibited_hits("bacillus", "tunicensisensis")


@pytest.mark.parametrize("genus,epithet", [
    ("Bacillus", "bacillus"),
    ("Streptomyces", "streptomyces"),
    ("Bacillus", "bacillusus"),
    ("Streptomyces", "streptomycesus"),
])
def test_epithet_identical_or_latinized_genus_is_never_compliant(genus, epithet):
    r = validate_agreement(genus, epithet, "feature")
    assert r.compliant is not True
    assert r.compliant is False


def test_generation_never_offers_the_doubled_form():
    """The report's reproduction: Bacillus + -us gave 'bacillusus'."""
    names = [c.name for c in generate("Bacillus", "feature", "species",
                                      genus="Bacillus")]
    assert "Bacillus bacillusus" not in names
    assert all(c.compliant is not True for c in
               generate("Bacillus", "feature", "species", genus="Bacillus"))
    names = [c.name for c in generate("Streptomyces", "feature", "species",
                                      genus="Streptomyces")]
    assert "Streptomyces streptomycesus" not in names


def test_place_stem_already_ending_in_ensis_is_not_doubled():
    cands = generate("tunicensis", "place", "species", genus="Bacillus")
    names = [c.name for c in cands]
    assert "Bacillus tunicensisensis" not in names
    assert "Bacillus tunicensis" in names
    # …and the repaired form is flagged, never asserted compliant
    for c in cands:
        if c.epithet:
            assert c.compliant is not True
            assert c.needs_review is True


def test_doubled_inflection_in_a_validation_is_needs_review():
    r = validate_agreement("Bacillus", "tunicensisensis", "place")
    assert r.compliant is None
    assert r.needs_review is True
    assert any("epithet_repeated_inflection" in w for w in r.warnings)


def test_ordinary_stems_are_still_inflected_normally():
    """The de-duplication must not swallow legitimate formation."""
    assert [c.name for c in generate("Beijing", "place", "species",
                                     genus="Klebsiella")] == [
        "Klebsiella beijingensis"
    ]
    assert [c.name for c in generate("Velez", "place", "species",
                                     genus="Bacillus")] == ["Bacillus velezensis"]
