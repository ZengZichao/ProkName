"""Unit tests: person-genitive declension-paradigm model.

The ending is decided by the Latinization PARADIGM of the surname, not by
'honoured person's sex x stem-final letter'. Every value that is not anchored to
a published LPSN epithet is a proposal: verified=False → needs_review.
"""

import pytest

from prokname.engine.genitive import (
    GenitiveCellUnavailable,
    person_genitive_ending,
    person_genitive_endings,
    person_genitive_form,
)

# The six names the 2x2 table got wrong or right.
ANCHORED = [
    ("Burgdorfer", "male", "i", "burgdorferi"),      # Borrelia burgdorferi
    ("Hensel", "male", "ae", "henselae"),           # Bartonella henselae
    ("Gordon", "male", "ae", "gordonae"),           # Mycobacterium gordonae
    ("Burnet", "male", "ii", "burnetii"),           # Coxiella burnetii
    ("Ricketts", "male", "ii", "rickettsii"),       # Rickettsia rickettsii
    ("Werkman", "male", "ii", "werkmanii"),         # Lactobacillus werkmanii
    ("Kitahara", "male", "ae", "kitaharae"),        # Oenococcus kitaharae
    ("Boyd", "male", "ii", "boydii"),               # Shigella boydii
    ("Delbrück", "male", "ii", "delbrueckii"),      # Lactobacillus delbrueckii
]


@pytest.mark.parametrize("surname,gender,ending,epithet", ANCHORED)
def test_attested_surnames_reproduce_the_published_latinization(
    surname, gender, ending, epithet
):
    form = person_genitive_form(surname, gender)
    assert form.ending == ending
    assert form.verified is True
    assert form.needs_review is False
    assert person_genitive_ending(surname, gender) == ending


def test_person_gender_does_not_select_the_ending():
    """Gordon was a man; 'gordonae' is a 1st-declension genitive (B3)."""
    for gender in ("male", "female"):
        assert person_genitive_ending("Gordon", gender) == "ae"
        assert person_genitive_ending("Hensel", gender) == "ae"


def test_unattested_surname_is_a_proposal_never_a_verified_value():
    """B3(e): the old test locked 'smith' → 'iae' in as an unquestionable rule.

    The string is still what the default rule proposes for a female-consonant
    surname, but it is now flagged unverified, so no caller may stamp ✓.
    """
    form = person_genitive_form("smith", "female")
    assert form.ending == "iae"
    assert form.verified is False
    assert form.needs_review is True
    assert form.from_lexicon is False


def test_unattested_male_consonant_surname_proposes_ii_unverified():
    form = person_genitive_form("smith", "male")
    assert form.ending == "ii"
    assert form.verified is False


def test_vowel_final_surname_that_is_not_attested_still_refuses():
    """The 'refuse to guess' path survives; it is just no longer applied to
    surnames whose published epithet is in the lexicon (kitaharae)."""
    with pytest.raises(GenitiveCellUnavailable):
        person_genitive_form("lee", "male")      # ends in a bare vowel

    with pytest.raises(ValueError):
        person_genitive_form("###", "male")      # latinizes to nothing


def test_invalid_person_gender():
    with pytest.raises(ValueError):
        person_genitive_ending("boyd", "masculine")


def test_endings_include_the_third_declension_i():
    """M1: the -i / -ii tolerance branch used to be dead because no cell ever
    held 'i'; the 3rd-declension -er paradigm now populates it."""
    endings = person_genitive_endings()
    assert "i" in endings and "ii" in endings
    assert endings == sorted(endings, key=len, reverse=True)


def test_paradigms_carry_provenance():
    from prokname.engine import data

    asset = data.person_genitive()
    for paradigm_id, entry in asset["paradigms"].items():
        assert "verified" in entry, paradigm_id
        assert "genitive_ending" in entry, paradigm_id
        if entry["verified"]:
            assert entry["lpsn_instances"], (
                f"verified paradigm {paradigm_id} must cite an LPSN instance"
            )
