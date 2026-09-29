"""Latinization and orthographic sanitation.

Newly formed prokaryotic names must use Latin letters only: no diacritics
(ü→ue German-style, é→e), no hyphens (compounds join via connecting vowels),
no digits or punctuation.
"""

from __future__ import annotations

import unicodedata

VOWELS = frozenset("aeiouy")

# Multi-letter transliterations must run BEFORE NFKD decomposition, otherwise
# e.g. ü would collapse to a plain "u" (Delbrück → delbrueckii, not delbrucii).
# German-style digraphs first; then letters NFKD cannot decompose (Nordic /
# Icelandic / Slavic digraphs and letters with no combining-accent form).
# The final table is subject to M0 expert sign-off against the ICNP
# orthography appendix; additions follow established latinization practice
# for personal and geographic names.
_SPECIAL = {
    "ß": "ss",
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "æ": "ae",
    "œ": "oe",
    "ø": "oe",
    "å": "aa",
    "đ": "d",
    "ð": "d",
    "þ": "th",
    "ł": "l",
    "ı": "i",
}


def latinize(text: str) -> str:
    """Transliterate to lowercase a-z; drop anything else."""
    s = text.lower()
    for src, dst in _SPECIAL.items():
        s = s.replace(src, dst)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if "a" <= ch <= "z")


def latinize_words(text: str) -> list[str]:
    """Latinize each whitespace-separated token (spaces are significant for
    binomials like 'Bacillus subtilis'); empty tokens dropped."""
    return [t for t in (latinize(tok) for tok in text.split()) if t]


def is_vowel(ch: str) -> bool:
    return ch in VOWELS


def stem_ending_type(stem: str) -> str:
    """Classify a stem as ending in a vowel or a consonant.

    Decides which cell of the person-genitive table applies (the
    section 2.4, category B).
    """
    if stem and is_vowel(stem[-1]):
        return "vowel"
    return "consonant"


def join(stem: str, suffix: str, connecting_vowel: str | None = "o") -> str:
    """Join stem and suffix, inserting a connecting vowel at a
    consonant-consonant boundary when one is configured."""
    if not stem or not suffix:
        return stem + suffix
    if connecting_vowel and not is_vowel(stem[-1]) and not is_vowel(suffix[0]):
        return stem + connecting_vowel + suffix
    return stem + suffix


def attach_ending(stem: str, ending: str) -> str:
    """Attach an inflectional ending to a stem with Latin vowel coalescence.

    'kitahara' + 'ae' → kitaharae (NOT kitaharaae), 'rossi' + 'i' → rossi,
    while 'hensel' + 'ae' → henselae and 'boyd' + 'ii' → boydii are plain
    appends. This is the composition side of the declension-paradigm model
    validate_agreement() reverses it to recover the surname.
    """
    if not stem or not ending:
        return stem + ending
    if is_vowel(ending[0]) and stem[-1] == ending[0]:
        return stem[:-1] + ending
    return stem + ending


def split_ending(word: str, ending: str) -> list[str]:
    """Every stem that attach_ending(stem, ending) could have produced."""
    if not word.endswith(ending):
        return []
    candidates = [word[: len(word) - len(ending)]]
    if ending and is_vowel(ending[0]):
        restored = candidates[0] + ending[0]
        if attach_ending(restored, ending) == word:
            candidates.append(restored)
    return [c for c in candidates if c]


def orthography_warnings(word: str) -> list[str]:
    """Orthographic problems detectable from spelling alone."""
    warnings: list[str] = []
    if word != word.lower():
        warnings.append("contains uppercase letters (epithets are all-lowercase)")
    stripped = latinize(word)
    if any(not ("a" <= ch <= "z") for ch in word.lower()):
        bad = sorted({ch for ch in word.lower() if not ("a" <= ch <= "z")})
        warnings.append(
            "contains non-Latin characters which were dropped/transliterated "
            f"during latinization ({', '.join(repr(b) for b in bad)}); "
            "ICNP names must consist of Latin letters only (no diacritics, "
            "no hyphens, no digits)"
        )
    if not stripped:
        warnings.append("latinized form is empty")
    return warnings
