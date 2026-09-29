"""Unit tests: candidate generation."""

import pytest

from prokname.engine.generate import generate


def test_person_genitive_generation_ignores_genus_gender():
    cands = generate(
        "Boyd", "person", "species", genus="Shigella", person_gender="male"
    )
    assert [c.name for c in cands] == ["Shigella boydii"]


def test_person_genitive_requires_person_gender():
    cands = generate("Boyd", "person", "species", genus="Shigella")
    assert len(cands) == 1
    assert cands[0].epithet is None
    assert any("--person-gender" in w for w in cands[0].warnings)


def test_attested_vowel_stem_person_genitive_is_used():
    """B3: Oenococcus kitaharae is a published 1st-declension genitive; the old
    2x2 table had no cell for it at all and blocked the name."""
    cands = generate(
        "Kitahara", "person", "species", genus="Oenococcus", person_gender="male"
    )
    assert [c.name for c in cands] == ["Oenococcus kitaharae"]
    assert cands[0].compliant is True and cands[0].needs_review is False


def test_unattested_vowel_stem_person_blocked():
    """The 'do not guess' path: a vowel-final surname with no attested
    latinization yields a blocked candidate, never a invented ending."""
    cands = generate(
        "Lee", "person", "species", genus="Bacterium", person_gender="male"
    )
    assert cands[0].epithet is None
    assert any("do not guess" in w for w in cands[0].warnings)


def test_place_adjective_uses_genus_gender():
    assert [c.name for c in generate("Beijing", "place", "species", genus="Klebsiella")] == \
        ["Klebsiella beijingensis"]
    assert [c.name for c in generate("Beijing", "place", "species", genus="Rhizobium")] == \
        ["Rhizobium beijingense"]


def test_place_adjective_with_unknown_gender_blocks():
    cands = generate("Beijing", "place", "species", genus="Zzz")
    assert cands[0].epithet is None


def test_gender_override_unblocks():
    from prokname.engine.gender import Gender

    cands = generate(
        "Beijing", "place", "species", genus="Zzz", gender_override=Gender.F
    )
    assert cands[0].name == "Zzz beijingensis"


def test_feature_emits_both_adjective_and_appositive():
    cands = generate("Wukong", "feature", "species", genus="Bacillus")
    names = [c.name for c in cands]
    assert "Bacillus wukongus" in names      # adjective, m genus
    assert "Bacillus wukong" in names        # appositive


def test_higher_rank_suffixes_are_gated_on_the_type_genus_stem():
    """B1: -aceae/-ales/-ota/-ia attach to the GENITIVE STEM of the type genus.

    A stem that is not an attested genus name still gets a mechanical proposal,
    but never a compliance assertion.
    """
    assert [c.name for c in generate("bacteri", "feature", "phylum")] == ["Bacteriota"]
    assert generate("bacteri", "feature", "phylum")[0].needs_review is True

    beijing = generate("Beijing", "place", "family")[0]
    assert beijing.name == "Beijingaceae"          # proposal…
    assert beijing.compliant is None               # …not a verdict (B1)
    assert beijing.needs_review is True
    assert any("type genus" in w.lower() for w in beijing.warnings)

    closter = generate("closter", "feature", "order")[0]
    assert closter.name == "Closterales"           # 'er' fallback is unverified
    assert closter.compliant is None and closter.needs_review is True
    # the real order name comes from the attested type genus
    assert [c.name for c in generate("Clostridium", "feature", "order")] == [
        "Clostridiales"
    ]
    assert generate("Clostridium", "feature", "order")[0].compliant is True


def test_conserved_class_name_is_not_over_regularized():
    """B1: the class of Bacillus is the protected name 'Bacilli'; 'Bacillia'
    has never been published and must not be emitted — not even when the user
    supplies the already-reduced stem 'Bacill'."""
    for stem in ("Bacillus", "Bacill", "bacill"):
        names = [c.name for c in generate(stem, "feature", "class")]
        assert names == ["Bacilli"], (stem, names)
        assert "Bacillia" not in names


def test_genus_rank_suggests_endings():
    cands = generate("Wukong", "feature", "genus")
    names = [c.name for c in cands]
    # consonant-final stem takes a connecting vowel before consonant-initial
    # endings (wukong + o + monas), the regular compound formation
    assert "Wukongomonas" in names
    assert "Wukongobacter" in names
    assert "Wukongococcus" in names
    assert "Wukongovibrio" in names


def test_genus_suffix_override():
    cands = generate("Wukong", "feature", "genus", genus_suffix="monas")
    assert [c.name for c in cands] == ["Wukongomonas"]


def test_subspecies_takes_binomial_and_uses_genus_for_gender():
    cands = generate(
        "Spizizen", "person", "subspecies",
        genus="Bacillus subtilis", person_gender="male",
    )
    # Bacillus is masculine; person genitive is genus-independent anyway
    assert cands[0].name == "Bacillus subtilis subsp. spizizenii"


def test_determinism():
    a = generate("Beijing", "place", "species", genus="Rhizobium")
    b = generate("Beijing", "place", "species", genus="Rhizobium")
    assert [c.as_dict() for c in a] == [c.as_dict() for c in b]


def test_invalid_inputs():
    with pytest.raises(ValueError):
        generate("###", "feature", "species", genus="Bacillus")
    with pytest.raises(ValueError):
        generate("Wukong", "feature", "kingdom")
    with pytest.raises(ValueError):
        generate("Wukong", "feature", "species")  # species needs --genus
    with pytest.raises(ValueError):
        generate("Wukong", "emoji", "genus")
