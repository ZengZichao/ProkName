"""Person-name genitive endings.

Person-derived epithets are GENITIVE NOUNS: indeclinable, independent of the
genus gender. What decides the ending is the DECLENSION PARADIGM the surname
was latinized into — not the honoured person's sex, and not whether the stem
happens to end in a vowel or a consonant:

    -a  (1st declension)  -> -ae   Bartonella henselae, Mycobacterium gordonae
                              (both honour a MAN: sex is not the trigger)
    -ius/-us (2nd)        -> -ii   Shigella boydii, Coxiella burnetii
    -er (3rd declension)  -> -i    Borrelia burgdorferi, Pseudomonas stutzeri

The paradigm of a given surname is only knowable from the published name, so
`person_genitive.json` carries a surname lexicon (each entry anchored to an
LPSN epithet) plus per-paradigm `verified` flags. Values taken from an
unverified cell are returned with `verified=False`, which the callers turn into
needs_review / compliant=None — the project's "don't guess" contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import data
from .orthography import latinize, stem_ending_type

VALID_PERSON_GENDERS = ("male", "female")


class GenitiveCellUnavailable(Exception):
    """Raised when no person-genitive value is available (no attestation and no
    documented default may be proposed for the given input)."""

    def __init__(self, key: str, why: str):
        self.key = key
        self.why = why
        super().__init__(
            f"person-genitive value for '{key}' unavailable: {why}. "
            "Fill it from the ICNP orthography appendix / an LPSN instance with "
            "expert sign-off (M0 deliverable) — do not guess."
        )


@dataclass(frozen=True)
class GenitiveForm:
    """One resolution of a surname to its genitive ending, with provenance."""

    surname: str
    ending: str
    paradigm: str
    verified: bool
    from_lexicon: bool
    person_gender: str | None
    reason: str

    @property
    def needs_review(self) -> bool:
        return not self.verified

    def as_dict(self) -> dict:
        return {
            "surname": self.surname,
            "ending": self.ending,
            "paradigm": self.paradigm,
            "verified": self.verified,
            "from_lexicon": self.from_lexicon,
            "person_gender": self.person_gender,
            "reason": self.reason,
            "needs_review": self.needs_review,
        }


def paradigms() -> dict:
    return data.person_genitive()["paradigms"]


def _ending_of(paradigm_id: str) -> str:
    try:
        return paradigms()[paradigm_id]["genitive_ending"]
    except KeyError as exc:  # data-asset defect: never silently invent a value
        raise GenitiveCellUnavailable(
            paradigm_id, "unknown declension paradigm in person_genitive.json"
        ) from exc


def _paradigm_verified(paradigm_id: str) -> bool:
    return bool(paradigms().get(paradigm_id, {}).get("verified"))


def person_genitive_form(stem: str, person_gender: str | None = None) -> GenitiveForm:
    """Resolve a latinized surname stem to its genitive ending (+ provenance).

    Raises GenitiveCellUnavailable when neither the surname lexicon nor a
    documented default can propose a value; raises ValueError for an unknown
    person_gender (accepted for back-compatibility).
    """
    if person_gender is not None and person_gender not in VALID_PERSON_GENDERS:
        raise ValueError(
            f"person_gender must be one of {VALID_PERSON_GENDERS}, "
            f"got {person_gender!r}"
        )
    surname = latinize(stem)
    if not surname:
        raise ValueError("stem latinizes to nothing")

    asset = data.person_genitive()
    entry = asset["surnames"].get(surname)
    if entry is not None:
        ending = _ending_of(entry["paradigm"])
        verified = bool(entry.get("verified")) and _paradigm_verified(entry["paradigm"])
        return GenitiveForm(
            surname=surname,
            ending=ending,
            paradigm=entry["paradigm"],
            verified=verified,
            from_lexicon=True,
            person_gender=person_gender,
            reason=(
                f"attested surname, paradigm '{entry['paradigm']}' "
                f"(ending -{ending}) — {entry.get('source', 'no source given')}"
            ),
        )

    for rule in asset["defaults"]:
        if rule.get("if_person_gender") and rule["if_person_gender"] != person_gender:
            continue
        condition = rule.get("if_surname_ends_with")
        if condition == "consonant":
            if stem_ending_type(surname) != "consonant":
                continue
        elif condition == "vowel":
            if stem_ending_type(surname) != "vowel":
                continue
        elif condition and not surname.endswith(condition):
            continue
        ending = _ending_of(rule["paradigm"])
        verified = bool(rule.get("verified")) and _paradigm_verified(rule["paradigm"])
        return GenitiveForm(
            surname=surname,
            ending=ending,
            paradigm=rule["paradigm"],
            verified=verified,
            from_lexicon=False,
            person_gender=person_gender,
            reason=(
                f"proposed by default rule '{rule['id']}' ({rule.get('note', '')}) — "
                "the surname is not attested, so the paradigm is unverified"
            ),
        )

    raise GenitiveCellUnavailable(
        f"{person_gender or 'gender-unset'}/{surname}",
        "surname is not attested and no documented default covers this ending; "
        "the latinization paradigm of a vowel-final surname cannot be inferred "
        "from spelling",
    )


def person_genitive_ending(stem: str, person_gender: str | None = None) -> str:
    """Ending for a person-derived genitive epithet.

    Parameter order deliberately matches person_genitive_form(stem,
    person_gender) — it used to be (person_gender, stem), the reverse of its own
    sibling. Two string arguments in swapped order do not raise, they return a
    different ending, which is the worst kind of mistake this module can make
    about a nomenclatural ruling.

    person_gender : 'male' | 'female' — gender of the HONOURED PERSON,
                    not of the genus (the earlier conflation). It only
                    selects a *default* paradigm for unattested surnames; the
                    attested paradigm of the surname always wins.
    stem          : the latinized name stem.

    Use person_genitive_form() when the verified/needs_review flag matters.
    """
    return person_genitive_form(stem, person_gender).ending


def person_genitive_endings() -> list[str]:
    """Every distinct genitive ending the asset knows, longest first.

    Used by validate_agreement() to split an epithet into stem + ending.
    """
    endings = {p["genitive_ending"] for p in paradigms().values()}
    return sorted((e for e in endings if e), key=len, reverse=True)
