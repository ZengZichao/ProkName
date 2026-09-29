"""Regression tests: adjective paradigms, the fourth grammatical category,
and the classifier that must not accept any epithet as a noun in apposition."""

import pytest

from prokname.engine.categories import (
    EtymologyType,
    GrammaticalCategory,
    categories_for,
)
from prokname.engine.generate import generate
from prokname.engine.validate import _ending_matches, validate_agreement

# (genus, epithet, category the engine must report)
PARTICIPLES = [
    ("Mycobacterium", "tuberculosis"),   # neuter genus, common-gender adjective
    ("Streptococcus", "pyogenes"),       # Greek -ες
    ("Clostridium", "perfringens"),
    ("Agrobacterium", "tumefaciens"),
    ("Bacillus", "halodurans"),
    ("Comamonas", "denitrificans"),
]


def test_participle_category_exists():
    assert GrammaticalCategory.PARTICIPLE.value == "participle"
    assert GrammaticalCategory.PARTICIPLE in categories_for(EtymologyType.FEATURE)


@pytest.mark.parametrize("genus,epithet", PARTICIPLES)
def test_participles_are_not_misjudged_or_called_appositive(genus, epithet):
    r = validate_agreement(genus, epithet, "feature")
    assert r.compliant is True, r.warnings
    assert r.grammatical_category == "participle"


@pytest.mark.parametrize("genus,epithet", PARTICIPLES)
def test_participle_generation_offers_one_analysis(genus, epithet):
    """A participle is never declined, so no '-us' twin of it is emitted."""
    cands = generate(epithet, "feature", "species", genus=genus)
    assert [c.grammatical_category for c in cands] == ["participle"]
    assert cands[0].epithet == epithet


def test_place_paradigms_beyond_ensis():
    for genus, epithet in [("Thermus", "aquaticus"),
                           ("Lactobacillus", "helveticus"),
                           ("Rhodopseudomonas", "palustris")]:
        r = validate_agreement(genus, epithet, "place")
        assert r.compliant is True, (genus, epithet, r.warnings)
        assert r.grammatical_category == "adjective"


def test_place_gender_mismatch_is_still_a_real_violation():
    assert validate_agreement("Rhizobium", "mongolensis", "place").compliant is False
    assert validate_agreement("Klebsiella", "beijingense", "place").compliant is False


def test_unanalysable_epithet_is_never_asserted_compliant():
    """Control experiment: 'qqqzz' returned compliant=True as 'appositive'."""
    for nonsense in ["qqqzz", "zxcvb", "bkdkd", "qqqzzus"]:
        r = validate_agreement("Bacillus", nonsense, "feature")
        assert r.compliant is None, (nonsense, r.compliant)
        assert r.needs_review is True


@pytest.mark.parametrize("epithet,ending,expected", [
    ("aeruginosa", "a", True),      # legitimate -a on a consonant
    ("qqqzz", "z", False),
    ("mba", "a", False),            # too short to be a stem
    ("eua", "a", False),            # single letter after a vowel: no evidence
    ("rosa", "a", True),            # legitimate -a on a consonantal stem
    ("tuberculosis", "is", True),
    ("subtilis", "e", False),       # -is word must not read as neuter -e
])
def test_single_letter_endings_only_count_on_a_consonantal_stem(
    epithet, ending, expected
):
    assert _ending_matches(epithet, ending) is expected


def test_thing_genitive_still_refuses_to_guess():
    r = validate_agreement("Vibrio", "cholerae", "thing")
    assert r.compliant is None
    assert any("declension" in n for n in r.notes)


def test_invariant_cola_rule_is_consumed():
    """rules.json epithet_invariant used to be dead data."""
    r = validate_agreement("Bacillus", "sedamicola", "feature")
    assert r.grammatical_category == "appositive"
    assert r.compliant is None            # the rule is unverified → no ✓
    assert any("-cola" in w or "cola" in w for w in r.warnings + r.notes)
    cands = generate("sedami-cola", "feature", "species", genus="Bacillus")
    assert [c.epithet for c in cands] == ["sedamicola"]   # never 'sedamicolus'
