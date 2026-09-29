"""Regression tests: higher-rank names are built on the GENITIVE STEM of
the type genus, and an underivable stem must never assert compliance.

Every case below is one row of the tribal/subtribal ending table, plus the
class-name exception known as the 'Bacillia' trap.
"""

import pytest

from prokname.engine import data
from prokname.engine.generate import generate, genitive_stem_of

# (type genus, rank, published name) — the report's table, 18 rows, with
# 'Bacillia' replaced by the real protected class name 'Bacilli' (see below).
TRUTH_TABLE = [
    ("Bacillus", "family", "Bacillaceae"),
    ("Pseudomonas", "family", "Pseudomonadaceae"),
    ("Streptomyces", "family", "Streptomycetaceae"),
    ("Micrococcus", "family", "Micrococcaceae"),
    ("Clostridium", "family", "Clostridiaceae"),
    ("Vibrio", "family", "Vibrionaceae"),
    ("Enterococcus", "family", "Enterococcaceae"),
    ("Lactococcus", "family", "Lactococcaceae"),
    ("Staphylococcus", "family", "Staphylococcaceae"),
    ("Mycobacterium", "family", "Mycobacteriaceae"),
    ("Lactobacillus", "family", "Lactobacillaceae"),
    ("Rickettsia", "family", "Rickettsiaceae"),
    ("Neisseria", "family", "Neisseriaceae"),
    ("Bacillus", "order", "Bacillales"),
    ("Streptomyces", "order", "Streptomycetales"),
    ("Pseudomonas", "phylum", "Pseudomonadota"),
    ("Deinococcus", "phylum", "Deinococcota"),
    ("Clostridium", "order", "Clostridiales"),
    ("Clostridium", "class", "Clostridia"),
    ("Bacillus", "class", "Bacilli"),          # protected name, NOT Bacillia
    ("Bacillus", "phylum", "Bacillota"),
]


@pytest.mark.parametrize("genus,rank,published", TRUTH_TABLE)
def test_type_genus_reproduces_the_published_higher_rank_name(
    genus, rank, published
):
    candidates = generate(genus, "feature", rank, genus=genus)
    assert [c.name for c in candidates] == [published]


@pytest.mark.parametrize("genus,rank,published", TRUTH_TABLE)
def test_attested_derivations_do_not_need_review(genus, rank, published):
    cand = generate(genus, "feature", rank, genus=genus)[0]
    assert cand.needs_review is False
    assert cand.compliant is True


def test_nominative_is_never_appended_to():
    """The bug: the suffix used to be pasted onto the nominative."""
    assert generate("Bacillus", "feature", "family")[0].name != "Bacillusaceae"
    assert "Bacillusaceae" not in [
        c.name for c in generate("Bacillus", "feature", "family")
    ]


def test_class_of_bacillus_is_the_protected_name_not_bacillia():
    """USAGE/verify_examples both say 'Bacillia' is the error class; the report's
    B1 table lists it as the target, which is itself wrong — the published class
    name is Bacilli (rules.json conserved_names)."""
    for stem in ("Bacillus", "Bacill", "bacill"):
        names = [c.name for c in generate(stem, "feature", "class")]
        assert names == ["Bacilli"], stem
        assert "Bacillia" not in names


def test_stem_is_attested_in_the_lexicon_with_provenance():
    derived = genitive_stem_of("Bacillus")
    assert derived.mode == "lexicon"
    assert derived.stem == "bacill"
    assert derived.verified is True
    assert "LPSN" in derived.reason


def test_irregular_stem_is_carried_by_the_lexicon_not_a_rule():
    """Bdellovibacter → Bdellovibrionaceae: no mechanical rule gets this right."""
    assert genitive_stem_of("Bdellovibacter").stem == "bdellovibrion"
    assert generate("Bdellovibacter", "feature", "family")[0].name == \
        "Bdellovibrionaceae"


def test_greek_ma_neuter_does_not_lose_the_t():
    assert generate("Treponema", "feature", "family")[0].name == "Treponemataceae"


def test_documented_fallback_is_flagged_not_asserted():
    """A genus missing from the lexicon gets the morphological fallback, which
    must be needs_review / compliant=None."""
    cand = generate("Streptosporix", "feature", "family")[0]
    assert cand.needs_review is True
    assert cand.compliant is None
    assert any("fallback" in w or "not attested" in w for w in cand.warnings)


def test_suprageneric_suffix_on_a_place_stem_is_never_asserted():
    """B1 aggravating case: bench used to assert 'Beijingaceae' is correct."""
    cand = generate("Beijing", "place", "family")[0]
    assert cand.compliant is None
    assert cand.needs_review is True
    assert any("type genus" in w.lower() for w in cand.warnings)


@pytest.mark.parametrize("rank", sorted(data.rules()["rank_suffix_examples"]))
def test_generator_reproduces_every_rank_suffix_example(rank):
    """The machine check the review asked for (B1): the data asset and the
    generator may no longer disagree in silence."""
    type_genera = data.rules()["rank_suffix_example_type_genera"]
    for name in data.rules()["rank_suffix_examples"][rank]:
        genus = type_genera.get(name)
        assert genus, f"{name} has no declared type genus"
        produced = [c.name for c in generate(genus, "feature", rank)]
        assert produced == [name], (rank, genus, produced)


def test_adopted_botanical_conventions_are_not_asserted_as_compliant():
    """-idae/-ineae/-oideae/-eae/-inae are adopted conventions, not
    mandatory ICNP terminations → never a ✓."""
    for genus, rank in [("Acidimicrobium", "subclass"), ("Bacillus", "subfamily"),
                        ("Corynebacterium", "suborder"), ("Lactobacillus", "tribe")]:
        cand = generate(genus, "feature", rank)[0]
        assert cand.compliant is None
        assert any("adopted botanical-code convention" in w for w in cand.warnings)


def test_vowel_coalescence_is_attested_not_invented():
    """clostridi + -ia → Clostridia (single i), as published."""
    assert generate("Clostridium", "feature", "class")[0].name == "Clostridia"
    assert generate("Methanobacterium", "feature", "class")[0].name == \
        "Methanobacteria"
