"""Unit tests: orthography."""

import pytest

from prokname.engine.orthography import (
    attach_ending,
    is_vowel,
    join,
    latinize,
    orthography_warnings,
    split_ending,
    stem_ending_type,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Delbrück", "delbrueck"),   # German-style ue, anchors delbrueckii
        ("Vélez", "velez"),
        ("Wukong", "wukong"),
        ("Boyd", "boyd"),
        ("Müller", "mueller"),
        ("Bæth", "baeth"),
        ("Århus", "aarhus"),   # Danish å → aa (ICNP-style transliteration)
        ("Bjørn", "bjoern"),   # Nordic ø → oe (no longer silently dropped)
        ("Łódź", "lodz"),      # Polish ł → l (NFKD handles ć/ś/ź/ż/ń)
        ("beijing-ensis", "beijingensis"),  # hyphen dropped by latinize
    ],
)
def test_latinize(raw, expected):
    assert latinize(raw) == expected


def test_special_digraphs_survive_nfkd():
    # ü must expand to 'ue' (not collapse to 'u') before decomposition
    assert latinize("Delbrück") == "delbrueck"


def test_stem_ending_type():
    assert stem_ending_type("boyd") == "consonant"
    assert stem_ending_type("kitahara") == "vowel"
    assert stem_ending_type("") == "consonant"


def test_join_inserts_connecting_vowel_only_at_consonant_boundary():
    assert join("beijing", "ensis") == "beijingensis"
    assert join("lact", "cola") == "lactocola"     # t|c → insert 'o'
    assert join("bacteri", "ota") == "bacteriota"  # i|o → direct
    assert join("lact", "cola", connecting_vowel=None) == "lactcola"


def test_is_vowel():
    assert is_vowel("a") and is_vowel("y")
    assert not is_vowel("b")


def test_attach_ending_coalesces_the_shared_vowel():
    # B3: a surname latinized in -a takes -ae without doubling the vowel
    assert attach_ending("kitahara", "ae") == "kitaharae"
    assert attach_ending("hensel", "ae") == "henselae"
    assert attach_ending("boyd", "ii") == "boydii"
    assert attach_ending("burgdorfer", "i") == "burgdorferi"
    assert attach_ending("rossi", "i") == "rossi"


def test_split_ending_recovers_both_parsings():
    assert split_ending("kitaharae", "ae") == ["kitahar", "kitahara"]
    assert "boyd" in split_ending("boydii", "ii")
    assert split_ending("boydii", "ae") == []
    # every returned parse re-attaches to the original word
    assert all(attach_ending(s, "ii") == "boydii"
               for s in split_ending("boydii", "ii"))


def test_split_then_attach_roundtrips():
    for surname, ending in [("kitahara", "ae"), ("boyd", "ii"), ("smith", "iae")]:
        word = attach_ending(surname, ending)
        assert surname in split_ending(word, ending)


def test_orthography_warnings_flags_forbidden_characters():
    assert any("hyphen" in w or "non-Latin" in w for w in orthography_warnings("beijing-ensis"))
    assert any("uppercase" in w for w in orthography_warnings("Beijingensis"))
    assert orthography_warnings("beijingensis") == []
