"""Unit tests: category-aware agreement validation.

The Shigella boydii cases anchor the central v1.3 fix: person-derived
epithets are genitive nouns, indeclinable, independent of genus gender.
"""

from prokname.engine.gender import Gender
from prokname.engine.validate import validate_agreement


# ---------------------------------------------------------------- person
def test_boydii_masculine_person_in_feminine_genus_is_compliant():
    r = validate_agreement("Shigella", "boydii", "person", person_gender="male")
    assert r.compliant is True
    assert r.grammatical_category == "genitive"


def test_boydiae_for_male_person_is_noncompliant_regardless_of_genus():
    r = validate_agreement("Shigella", "boydiae", "person", person_gender="male")
    assert r.compliant is False
    assert any("genus gender is irrelevant" in w for w in r.warnings)


def test_person_without_person_gender_needs_review():
    r = validate_agreement("Shigella", "boydii", "person")
    assert r.compliant is None
    assert any("person_gender" in w for w in r.warnings)


def test_delbrueckii_with_diacritic_source_is_latinized():
    r = validate_agreement(
        "Lactobacillus", "delbrueckii", "person", person_gender="male"
    )
    assert r.compliant is True


# ---------------------------------------------------------------- place
def test_place_adjectives_across_all_three_genders():
    assert validate_agreement(
        "Klebsiella", "michiganensis", "place").compliant is True   # f
    assert validate_agreement(
        "Rhizobium", "mongolense", "place").compliant is True       # n
    assert validate_agreement(
        "Bacillus", "velezensis", "place").compliant is True        # m


def test_place_gender_mismatch_is_flagged():
    r = validate_agreement("Rhizobium", "mongolensis", "place")     # n genus, m/f form
    assert r.compliant is False
    assert r.expected_ending == "ense"


# ---------------------------------------------------------------- adjectives
def test_third_declension():
    assert validate_agreement(
        "Bacillus", "subtilis", "feature",
        adjective_formation="third_declension").compliant is True
    assert validate_agreement(
        "Xanthomonas", "campestris", "feature",
        adjective_formation="third_declension").compliant is True


def test_tertium_is_not_third_declension():
    """'Clostridium tertium' was listed as a third-declension example, but
    -um is 2nd-declension neuter. It must not silently pass as one, and it must
    not be classified as a noun in apposition either — the honest answer is
    needs_review."""
    r = validate_agreement(
        "Clostridium", "tertium", "feature",
        adjective_formation="third_declension",
    )
    assert r.compliant is None and r.needs_review is True
    # ...and it validates as what it really is: 2nd declension neuter.
    assert validate_agreement(
        "Clostridium", "tertium", "feature",
        adjective_formation="second_declension",
    ).compliant is True


def test_nonsense_epithet_is_never_asserted_compliant():
    """Control experiment: 'qqqzz' used to come back compliant=True as an
    appositive, which made the CI assertion unfailable."""
    r = validate_agreement("Bacillus", "qqqzz", "feature")
    assert r.compliant is None
    assert r.needs_review is True
    assert any("apposition" in w or "indeclinable" in w for w in r.warnings)


def test_nourishing_paradigm_anchored_to_real_names():
    assert validate_agreement(
        "Streptomyces", "autotrophicus", "feature",
        adjective_formation="nourishing").compliant is True
    assert validate_agreement(
        "Pseudonocardia", "autotrophica", "feature",
        adjective_formation="nourishing").compliant is True


def test_adjective_with_unknown_genus_gender_needs_review():
    r = validate_agreement("Zzz", "somethingensis", "place")
    assert r.compliant is None
    assert r.gender.needs_review


def test_gender_override_drives_adjective_check():
    r = validate_agreement(
        "Zzz", "somethingense", "place", gender_override=Gender.N
    )
    assert r.compliant is True


# ---------------------------------------------------------------- thing & appositive
def test_thing_genitive_is_documented_needs_review():
    r = validate_agreement("Vibrio", "cholerae", "thing")
    assert r.compliant is None
    assert any("declension" in n for n in r.notes)


def test_coli_is_not_asserted_as_an_adjective():
    """'coli' is an indeclinable noun (LPSN: genitive 'of the colon'),
    so nothing about it can be asserted from spelling alone — the verdict is
    needs_review, not a ✓ for the whole apposition class."""
    r = validate_agreement("Escherichia", "coli", "feature")
    assert r.grammatical_category == "genitive"
    assert r.compliant is None
    assert r.needs_review is True


# ------------------------------------------------- participle / 4th category (M2)
def test_participles_and_common_gender_adjectives_are_not_misjudged():
    for genus, epithet in [
        ("Mycobacterium", "tuberculosis"),   # neuter genus, -is common gender
        ("Streptococcus", "pyogenes"),        # Greek -ες, one form for all
        ("Clostridium", "perfringens"),       # present participle -ens
        ("Agrobacterium", "tumefaciens"),
        ("Bacillus", "halodurans"),
    ]:
        r = validate_agreement(genus, epithet, "feature")
        assert r.compliant is True, (genus, epithet, r.warnings)
        assert r.grammatical_category == "participle", (genus, epithet)


def test_place_adjective_paradigms_beyond_ensis():
    """M2: 'place' used to mean -ensis only, so Thermus aquaticus and
    Lactobacillus helveticus were reported as violations."""
    assert validate_agreement("Thermus", "aquaticus", "place").compliant is True
    assert validate_agreement("Lactobacillus", "helveticus", "place").compliant is True
    assert validate_agreement("Rhodopseudomonas", "palustris", "place").compliant is True


# --------------------------------------------------- prohibited combinations (B2)
def test_epithet_identical_to_genus_is_never_compliant():
    r = validate_agreement("Bacillus", "bacillus", "feature")
    assert r.compliant is not True
    assert r.compliant is False
    assert any("epithet_identical_to_genus" in w for w in r.warnings)


def test_latinized_repeat_of_genus_is_never_compliant():
    r = validate_agreement("Bacillus", "bacillusus", "feature")
    assert r.compliant is not True
    assert any("epithet_latinized_repeat_of_genus" in w for w in r.warnings)


def test_doubled_inflection_is_needs_review_not_a_verdict():
    r = validate_agreement("Bacillus", "tunicensisensis", "place")
    assert r.compliant is None
    assert any("epithet_repeated_inflection" in w for w in r.warnings)


# ------------------------------------------------------ person genitive (B3/M1)
def test_person_genitives_from_every_paradigm_are_accepted():
    cases = [
        ("Borrelia", "burgdorferi", "male"),      # 3rd declension -er → -i
        ("Bartonella", "henselae", "male"),       # 1st declension -a → -ae
        ("Mycobacterium", "gordonae", "male"),    # -ae although the person is male
        ("Coxiella", "burnetii", "male"),         # 2nd declension → -ii
        ("Rickettsia", "rickettsii", "male"),
        ("Lactobacillus", "werkmanii", "male"),
        ("Oenococcus", "kitaharae", "male"),
    ]
    for genus, epithet, gender in cases:
        r = validate_agreement(genus, epithet, "person", person_gender=gender)
        assert r.compliant is True, (epithet, r.warnings)


def test_unattested_person_ending_is_needs_review():
    """B3(c): an unverified cell may never produce a ✓."""
    r = validate_agreement("Escherichia", "smithiae", "person", person_gender="female")
    assert r.compliant is None and r.needs_review is True


def test_i_ii_variant_tolerance_branch_is_reachable():
    """M1: the branch used to be dead code; -i is now a populated ending."""
    accepted = validate_agreement(
        "Escherichia", "smithi", "person", person_gender="male"
    )
    assert accepted.compliant is True
    assert any("variant" in w for w in accepted.warnings)
    # …but an attested published form is not overridable by the tolerance.
    attested = validate_agreement(
        "Borrelia", "burgdorferii", "person", person_gender="male"
    )
    assert attested.compliant is False


def test_hyphenated_epithet_warns():
    r = validate_agreement("Klebsiella", "michigan-ensis", "place")
    assert any("non-Latin" in w for w in r.warnings)
