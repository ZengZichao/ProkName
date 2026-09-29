"""Grammatical categories of specific epithets.

The FIRST decision of both generation and validation is the grammatical
category, driven by the etymology type annotation — never guessed from
spelling alone:

- adjective   : true adjective, declines with the genus gender
- participle  : participle / common-gender adjective — ONE form for m, f and n
                (e.g. Clostridium perfringens, Streptococcus pyogenes). It is a
                MODIFIER, not a noun in apposition, and it must never be
                rendered as "N.L. n. in app." in the registration etymology
                table.
- genitive    : genitive noun, indeclinable; ending decided by the etymology
                object (honoured person's latinization, source noun's declension)
- appositive  : noun in apposition / invariant compound, indeclinable
"""

from __future__ import annotations

from enum import Enum

from . import data


class GrammaticalCategory(str, Enum):
    ADJECTIVE = "adjective"
    PARTICIPLE = "participle"
    GENITIVE = "genitive"
    APPOSITIVE = "appositive"


class EtymologyType(str, Enum):
    PLACE = "place"
    PERSON = "person"
    THING = "thing"
    FEATURE = "feature"


# Legacy spellings kept working (rules.json may hold a list or one of these
# strings; other modules and old exports used the string form only).
_LEGACY_MAPPINGS = {
    "adjective_or_appositive": [
        GrammaticalCategory.ADJECTIVE, GrammaticalCategory.APPOSITIVE
    ],
    "adjective_or_participle_or_appositive": [
        GrammaticalCategory.ADJECTIVE,
        GrammaticalCategory.PARTICIPLE,
        GrammaticalCategory.APPOSITIVE,
    ],
}


def categories_for(etymology_type: str | EtymologyType) -> list[GrammaticalCategory]:
    """Map an etymology type to its grammatical category (or categories).

    The mapping lives in rules.json ("epithet_type_to_category") so experts can
    review it without touching code. 'feature' is deliberately ambiguous
    (adjective, participle or appositive); the engine emits each analysis it can
    actually support and refuses to assert compliance for the rest.
    """
    key = EtymologyType(etymology_type).value
    mapped = data.rules()["epithet_type_to_category"][key]
    if isinstance(mapped, (list, tuple)):
        return [GrammaticalCategory(m) for m in mapped]
    if mapped in _LEGACY_MAPPINGS:
        return list(_LEGACY_MAPPINGS[mapped])
    return [GrammaticalCategory(mapped)]
