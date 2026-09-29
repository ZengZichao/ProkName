"""Unit tests: dual-mode genus gender determination."""

from prokname.engine.gender import Gender, gender_of


def test_lookup_mode_hits_lexicon_with_high_confidence():
    for genus, expected in [
        ("Escherichia", "f"), ("Shigella", "f"), ("Pseudomonas", "f"),
        ("Bacillus", "m"), ("Thermus", "m"), ("Rhizobium", "n"),
        ("Treponema", "n"),
    ]:
        gr = gender_of(genus)
        assert gr.mode == "lookup"
        assert gr.gender is Gender(expected)
        assert gr.confidence == "high"
        assert not gr.needs_review


def test_lookup_normalizes_case_and_diacritics():
    assert gender_of("escherichia").gender is Gender.F
    assert gender_of("BACILLUS").gender is Gender.M


def test_override_mode():
    gr = gender_of("Mysterygenus", override=Gender.NEUTER)
    assert gr.mode == "override"
    assert gr.gender is Gender.NEUTER
    assert not gr.needs_review


def test_inference_mode_always_needs_review():
    # -um ending via generic heuristic
    gr = gender_of("Closteridium")
    assert gr.mode == "inference"
    assert gr.gender is Gender.N
    assert gr.needs_review
    assert gr.confidence == "low"


def test_greek_ma_exception_overrides_generic_a():
    # synthetic genus: -ma must win over the generic '-a' → feminine rule
    gr = gender_of("Exempluma")
    assert gr.gender is Gender.N
    assert gr.needs_review
    assert "exception_endings" in gr.reason


def test_morpheme_heuristic():
    gr = gender_of("Frankomonas")  # synthetic; -monas is feminine
    assert gr.gender is Gender.F
    assert "morphemes" in gr.reason
    assert gr.needs_review


def test_unknown_never_guesses():
    gr = gender_of("Zzz")
    assert gr.gender is None
    assert gr.mode == "unknown"
    assert gr.needs_review
    assert "refusing to guess" in gr.reason
