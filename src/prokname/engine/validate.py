"""Category-aware agreement validation.

True adjectives are checked against the genus gender; genitive and appositive
epithets are INDECLINABLE and are never gender-checked — instead the genitive
formation is checked against the honoured-person table. Participles and
common-gender adjectives (the fourth category) have a single form for
m/f/n, so agreement cannot fail for them and they must not be mislabelled as
nouns in apposition.

Three-state contract: `compliant` is only True when there is
positive evidence — an attested paradigm form, an attested indeclinable
epithet, or an attested surname latinization. Anything that merely "does not
look like an adjective" is needs_review (None), never an implied ✓.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import data
from .categories import EtymologyType, GrammaticalCategory, categories_for
from .gender import Gender, GenderResult, gender_of
from .genitive import (
    GenitiveCellUnavailable,
    person_genitive_endings,
    person_genitive_form,
)
from .orthography import (
    VOWELS,
    attach_ending,
    latinize,
    orthography_warnings,
    split_ending,
)

# Which adjective-formation table applies by default for each etymology type.
DEFAULT_ADJECTIVE_FORMATION = {
    "place": "place",
    "feature": "second_declension",
}

# Minimum number of letters that must remain in front of an ending before the
# ending counts as morphological evidence ('qqqzz' / 'mba' are not stems).
MIN_STEM_BEFORE_ENDING = 2
MIN_STEM_FOR_SINGLE_LETTER_ENDING = 3

# Endings that ICNP treats as inflectional for the purpose of the Rule 20 note
# ("the epithet may not be the latinized form of the genus name").
_INFLECTIONAL_ENDINGS = (
    "us", "um", "a", "ae", "i", "ii", "is", "e", "es", "o", "on", "er", "iae",
)


@dataclass
class ValidationResult:
    genus: str
    epithet: str
    etymology_type: str
    grammatical_category: str | None
    gender: GenderResult | None
    expected_ending: str | None
    compliant: bool | None  # None = needs review / not determinable
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    needs_review: bool = False  # True whenever a verdict could not be asserted

    def as_dict(self) -> dict:
        return {
            "genus": self.genus,
            "epithet": self.epithet,
            "etymology_type": self.etymology_type,
            "grammatical_category": self.grammatical_category,
            "gender": self.gender.as_dict() if self.gender else None,
            "expected_ending": self.expected_ending,
            "compliant": self.compliant,
            "warnings": self.warnings,
            "notes": self.notes,
            "needs_review": self.needs_review,
        }


def _adjective_endings(formation: str) -> dict:
    table = data.rules()["epithet_adjective"]
    if formation not in table:
        raise ValueError(
            f"unknown adjective formation {formation!r}; known: {sorted(table)}"
        )
    return {g: e for g, e in table[formation].items() if isinstance(e, str)}


def _place_formations() -> list[str]:
    """Formations a place epithet may legitimately take (M2).

    'place' used to mean '-ensis only', which misjudged real names such as
    Thermus aquaticus and Lactobacillus helveticus.
    """
    spec = data.rules().get("place_adjective_formations", {})
    formations = [f for f in spec.get("formations", []) if f]
    return formations or ["place"]


def _ending_matches(epithet: str, ending: str) -> bool:
    """Tightened ending test.

    A single-letter ending ('a', 'e', 'i') only counts as evidence when it sits
    directly on a consonant and the remainder is long enough to be a stem, so
    that arbitrary spellings can no longer be classified by accident.
    """
    if not ending or not isinstance(ending, str) or not epithet.endswith(ending):
        return False
    rest = epithet[: len(epithet) - len(ending)]
    if not any(ch in VOWELS for ch in rest):
        return False
    if len(ending) == 1:
        if len(rest) < MIN_STEM_FOR_SINGLE_LETTER_ENDING:
            return False
        if rest[-1] in VOWELS:
            return False
    elif len(rest) < MIN_STEM_BEFORE_ENDING:
        return False
    return True


def _matches_any_ending(epithet: str, endings: dict) -> list[str]:
    """Gender keys whose paradigm ending the epithet actually carries."""
    return [g for g, e in sorted(endings.items()) if _ending_matches(epithet, e)]


# --------------------------------------------------------------- data helpers
def _indeclinable_anchor(epithet_l: str) -> dict | None:
    """Attested participle / common-gender adjective / genitive-noun epithet."""
    spec = data.rules().get("epithet_indeclinable", {})
    for anchor in spec.get("anchors", []):
        if latinize(anchor.get("epithet", "")) == epithet_l:
            return anchor
    return None


def _participle_ending(epithet_l: str) -> dict | None:
    """Ending-based participle recognition (-ans / -ens)."""
    spec = data.rules().get("epithet_indeclinable", {})
    for entry in spec.get("endings", []):
        ending = entry.get("ending", "")
        if len(ending) < 2:
            continue
        rest = epithet_l[: len(epithet_l) - len(ending)]
        if epithet_l.endswith(ending) and len(rest) >= MIN_STEM_BEFORE_ENDING \
                and any(ch in VOWELS for ch in rest):
            return entry
    return None


def _invariant_endings() -> list[dict]:
    """rules.json "epithet_invariant" — consumed here and in generate()."""
    out = []
    for entry in data.rules().get("epithet_invariant", {}).values():
        if isinstance(entry, dict) and entry.get("ending"):
            out.append(entry)
    return out


def invariant_epithet_rule(epithet_l: str) -> dict | None:
    """Return the invariant-epithet rule an epithet falls under (e.g. -cola)."""
    for entry in _invariant_endings():
        ending = entry["ending"]
        if epithet_l.endswith(ending) and len(epithet_l) > len(ending):
            return entry
    return None


def _prohibited_rules() -> list[dict]:
    return data.rules().get("prohibited_patterns", {}).get("rules", [])


def prohibited_rule(rule_id: str) -> dict:
    """Public lookup of one prohibited_patterns entry, by id."""
    return _prohibited_rule(rule_id)


def _prohibited_rule(rule_id: str) -> dict:
    """Look one prohibited_patterns entry up by id (used to word the warnings)."""
    for rule in _prohibited_rules():
        if rule.get("id") == rule_id:
            return rule
    return {}


def _prohibited_hits(genus_l: str, epithet_l: str) -> list[dict]:
    """Which prohibited_patterns rules fire for this (genus, epithet) pair.

    The detection strategies are named in the data asset so an expert can read
    the table and see exactly what the code enforces. The two
    remaining entries of the table are enforced elsewhere:
    'suprageneric_suffix_on_a_non_genus_stem' in generate() and
    'epithet_not_analysable' in _appositive_result() below.
    """
    hits = []
    for rule in _prohibited_rules():
        detect = rule.get("detect")
        fired = False
        if detect == "epithet_equals_genus":
            fired = bool(epithet_l) and epithet_l == genus_l
        elif detect == "epithet_is_genus_plus_inflectional_ending":
            fired = (
                bool(genus_l)
                and len(epithet_l) > len(genus_l)
                and epithet_l.startswith(genus_l)
                and epithet_l[len(genus_l):] in _INFLECTIONAL_ENDINGS
            )
        elif detect == "epithet_ends_with_doubled_paradigm_ending":
            fired = bool(_doubled_ending(epithet_l))
        if fired:
            hits.append(rule)
    return hits


def _doubled_ending(epithet_l: str) -> str | None:
    """'tunicensis' + -ensis → 'tunicensisensis'; 'bacillus' + -us → 'bacillusus'.

    Only endings of two or more letters are considered, and a real stem has to
    survive in front of the doubled pair, so that genuine forms are not swept
    up by the test.

    Which ending vocabulary this uses, and why it is not the generator's
    --------------------------------------------------------------------
    This predicate looks at a FINISHED epithet and asks "is the tail of this
    string one ending written twice?". It therefore needs every ending that can
    legitimately appear at the end of a name, including the person-genitive
    family (-i, -ii, -ae, -iae, -is) and the higher-rank suffixes (-aceae,
    -idae, -oideae, …), because those are exactly the endings a user can double.

    generate._paradigm_endings() asks a DIFFERENT question — "does this input
    STEM already carry an inflectional ending, so appending another one would
    decline it twice?" — and so keys on the adjectival tables plus the invariant
    epithet endings (-cola), which is what stems actually look like.

    The two sets deliberately overlap only in `epithet_adjective`. They are not
    two copies of one rule and must not be merged: doing so would either blind
    this check to genitive doubling, or make the generator refuse ordinary
    stems that end like a family suffix. tests/test_ending_predicates.py pins
    the intended relationship between them, so a future edit to one table that
    silently changes the other's behaviour fails a test instead of quietly
    altering which names are called compliant.
    """
    endings: set[str] = set()
    for table in data.rules()["epithet_adjective"].values():
        endings.update(e for e in table.values() if isinstance(e, str))
    endings.update(person_genitive_endings())
    endings.update(
        s for s in data.rules()["rank_suffix"].values() if isinstance(s, str)
    )
    for ending in sorted(endings, key=len, reverse=True):
        if len(ending) < 2:
            continue
        doubled = ending + ending
        if epithet_l.endswith(doubled) and len(epithet_l) >= len(doubled) + 2:
            return ending
    return None


# ------------------------------------------------------------------ public API
def validate_agreement(
    genus: str,
    epithet: str,
    etymology_type: str,
    *,
    person_gender: str | None = None,
    gender_override: Gender | None = None,
    adjective_formation: str | None = None,
) -> ValidationResult:
    """Validate a specific epithet against its genus and etymology type.

    Raises ValueError when `genus` or `epithet` is missing or blank. They used
    to fail deeper in as `AttributeError: 'NoneType' object has no attribute
    'lower'`, which named neither the bad argument nor the caller. That was
    harmless while generate() was the only caller — it always passes both — and
    became a hazard the moment the benchmark and any future `validate` command
    reached the same function, because a supra-generic candidate legitimately
    has `epithet=None` and would crash on inspection rather than being refused
    with a reason.
    """
    for label, value in (("genus", genus), ("epithet", epithet)):
        if value is None or not str(value).strip():
            raise ValueError(
                f"validate_agreement() needs a non-empty {label}, got {value!r}. "
                "A supra-generic name (family, order, class, …) has no specific "
                "epithet and is outside this function's contract — rank "
                "suffixes are checked by generate()'s higher-rank path.")
    etype = EtymologyType(etymology_type).value
    epithet_l = latinize(epithet)
    genus_l = latinize(genus)
    warnings: list[str] = list(orthography_warnings(epithet))
    notes: list[str] = []
    categories = categories_for(etype)

    # ---- prohibited combinations (ICNP Rule 20 note) ------------------------
    hits = _prohibited_hits(genus_l, epithet_l)
    hard = [h for h in hits if h.get("verdict") == "non_compliant"]
    soft = [h for h in hits if h.get("verdict") != "non_compliant"]
    for rule in hits:
        warnings.append(
            f"prohibited pattern '{rule['id']}': {rule.get('example', epithet)} — "
            f"{rule.get('source', 'no source given')}"
        )
    if hard:
        return ValidationResult(
            genus=genus, epithet=epithet, etymology_type=etype,
            grammatical_category=None, gender=None, expected_ending=None,
            compliant=False, warnings=warnings,
            notes=notes + [
                "combination is prohibited outright, so no agreement check is "
                "performed (the name cannot be validated as publishable)"
            ],
            needs_review=False,
        )
    if soft:
        # e.g. a doubled inflection: an input defect, not a nomenclatural
        # violation — refuse to assert anything (B2).
        return ValidationResult(
            genus=genus, epithet=epithet, etymology_type=etype,
            grammatical_category=None, gender=None, expected_ending=None,
            compliant=None, warnings=warnings,
            notes=notes + ["input cannot be analysed as stated; needs expert review"],
            needs_review=True,
        )

    # ---- attested indeclinable epithets (participle / common gender) --------
    if etype in ("feature", "thing"):
        anchored = _indeclinable_anchor(epithet_l)
        if anchored is not None:
            return _indeclinable_result(
                genus, epithet, etype, anchored.get("kind", "participle"),
                bool(anchored.get("verified")), anchored.get("example", ""),
                warnings, notes, gender_override, categories,
            )
        if etype == "feature" and GrammaticalCategory.PARTICIPLE in categories:
            # -ans / -ens present participles are common-gender: their form is
            # identical before a masculine, feminine or neuter genus (M2).
            participle = _participle_ending(epithet_l)
            if participle is not None:
                return _indeclinable_result(
                    genus, epithet, etype, participle.get("kind", "participle"),
                    bool(participle.get("verified")), participle.get("source", ""),
                    warnings, notes, gender_override, categories,
                )

    # ---- invariant compounds (rules.json epithet_invariant) -----------------
    # Checked BEFORE the adjectival paradigms: an epithet in -cola is an
    # indeclinable compound and must never be declined for the genus gender.
    invariant = invariant_epithet_rule(epithet_l)
    if invariant is not None and etype != "person":
        return _invariant_result(
            genus, epithet, etype, invariant, warnings, notes
        )

    # ---- adjective branch ---------------------------------------------------
    if GrammaticalCategory.ADJECTIVE in categories:
        return _adjective_result(
            genus, epithet, epithet_l, etype, warnings, notes, categories,
            gender_override, adjective_formation,
        )

    # ---- person-genitive branch ---------------------------------------------
    if GrammaticalCategory.GENITIVE in categories and etype == "person":
        return _person_genitive_result(
            genus, epithet, epithet_l, etype, person_gender, warnings, notes
        )

    # ---- thing-genitive branch ----------------------------------------------
    if GrammaticalCategory.GENITIVE in categories:
        notes.append(
            "thing-genitive formation depends on the source noun's own "
            "declension (e.g. cholerae) and cannot be verified from the "
            "epithet spelling alone; check against the etymology"
        )
        return ValidationResult(
            genus=genus,
            epithet=epithet,
            etymology_type=etype,
            grammatical_category=GrammaticalCategory.GENITIVE.value,
            gender=None,
            expected_ending=None,
            compliant=None,
            warnings=warnings,
            notes=notes,
            needs_review=True,
        )

    # ---- appositive branch ---------------------------------------------------
    return _appositive_result(genus, epithet, etype, warnings, categories, notes)


def _adjective_result(
    genus, epithet, epithet_l, etype, warnings, notes, categories,
    gender_override, adjective_formation,
) -> ValidationResult:
    formations = (
        [adjective_formation] if adjective_formation
        else (_place_formations() if etype == "place"
              else [DEFAULT_ADJECTIVE_FORMATION.get(etype, "second_declension")])
    )
    ambiguous = len(categories) > 1

    gr = gender_of(genus, gender_override)
    tables = {f: _adjective_endings(f) for f in formations}
    matched_by = {
        f: _matches_any_ending(epithet_l, endings) for f, endings in tables.items()
    }

    if gr.gender is None:
        return ValidationResult(
            genus=genus, epithet=epithet, etymology_type=etype,
            grammatical_category=GrammaticalCategory.ADJECTIVE.value,
            gender=gr, expected_ending=None, compliant=None,
            warnings=warnings + [gr.reason], notes=notes, needs_review=True,
        )

    if ambiguous and not any(matched_by.values()):
        # Nothing adjectival about the spelling: fall through to the
        # non-adjectival analysis, which is a needs-review, not a ✓.
        return _appositive_result(genus, epithet, etype, warnings, categories, notes)

    gender_key = gr.gender.value
    for formation in formations:
        if gender_key in matched_by[formation]:
            expected = tables[formation][gender_key]
            notes.append(
                f"adjective agrees with {gender_key} genus "
                f"(formation '{formation}', ending -{expected})"
            )
            if formation != "place" and etype == "place":
                notes.append(
                    "place epithet formed with a non -ensis paradigm "
                    "(see rules.json place_adjective_formations)"
                )
            return ValidationResult(
                genus=genus, epithet=epithet, etymology_type=etype,
                grammatical_category=GrammaticalCategory.ADJECTIVE.value,
                gender=gr, expected_ending=expected, compliant=True,
                warnings=warnings, notes=notes,
            )

    matched_genders = sorted({g for gs in matched_by.values() for g in gs})
    if matched_genders:
        # Positive evidence of a gender disagreement (e.g. a neuter genus with a
        # masculine/feminine -ensis form) — this is a real violation.
        expected = tables[formations[0]].get(gender_key)
        warnings.append(
            f"epithet ends like the {'/'.join(matched_genders)} form(s) of "
            f"{sorted(tables)}, but genus {genus} is {gender_key} "
            f"(expected -{expected})"
        )
        return ValidationResult(
            genus=genus, epithet=epithet, etymology_type=etype,
            grammatical_category=GrammaticalCategory.ADJECTIVE.value,
            gender=gr, expected_ending=expected, compliant=False,
            warnings=warnings, notes=notes,
        )

    # No paradigm matches at all. Declaring the epithet an adjective here would
    # be a guess; declaring it a noun in apposition would be a bigger one.
    expected = tables[formations[0]].get(gender_key)
    warnings.append(
        f"no adjectival paradigm of {formations} matches this epithet"
        + (
            f"; the declared formation '{adjective_formation}' expects "
            f"-{expected} for a {gender_key} genus"
            if adjective_formation else ""
        )
        + " — refusing to classify it (expert review)"
    )
    return ValidationResult(
        genus=genus, epithet=epithet, etymology_type=etype,
        grammatical_category=None, gender=gr, expected_ending=expected,
        compliant=None, warnings=warnings,
        notes=notes + ["unanalysable epithet: neither adjective nor attested "
                       "indeclinable form"],
        needs_review=True,
    )


def _indeclinable_result(
    genus, epithet, etype, kind, verified, example,
    warnings, notes, gender_override, categories,
) -> ValidationResult:
    """Attested participle / common-gender adjective / genitive noun (M2)."""
    category = (
        GrammaticalCategory.PARTICIPLE.value
        if kind in ("participle", "indeclinable_adjective")
        else GrammaticalCategory.GENITIVE.value
    )
    gr = gender_of(genus, gender_override)
    notes.append(
        f"{kind} epithet: a single form serves m, f and n, so gender agreement "
        f"cannot fail (attested example: {example or 'n/a'})"
    )
    if not verified:
        warnings.append(
            f"anchor for '{epithet}' is flagged unverified in rules.json "
            "(epithet_indeclinable) — needs M0 sign-off"
        )
    return ValidationResult(
        genus=genus, epithet=epithet, etymology_type=etype,
        grammatical_category=category, gender=gr, expected_ending=None,
        compliant=True if verified else None, warnings=warnings, notes=notes,
        needs_review=not verified,
    )


def _person_genitive_result(
    genus, epithet, epithet_l, etype, person_gender, warnings, notes
) -> ValidationResult:
    category = GrammaticalCategory.GENITIVE.value

    def result(compliant, *, review=False, extra_notes=(), expected=None):
        return ValidationResult(
            genus=genus, epithet=epithet, etymology_type=etype,
            grammatical_category=category, gender=None,
            expected_ending=expected, compliant=compliant, warnings=warnings,
            notes=notes + list(extra_notes), needs_review=review,
        )

    if person_gender is None:
        warnings.append(
            "person_gender of the honoured person (male/female) is required "
            "to validate genitive formation"
        )
        return result(None, review=True)

    endings = person_genitive_endings()
    matches = [e for e in endings if epithet_l.endswith(e)]
    if not matches:
        warnings.append(
            "epithet does not end with any person-genitive ending known to the "
            "declension-paradigm table; verify the formation against the "
            "etymology"
        )
        return result(None, review=True)

    lexicon = data.person_genitive()["surnames"]
    tolerance = set(
        data.person_genitive().get("variant_tolerance", {}).get(
            "genitive_i_ii", {}
        ).get("acceptable_pair", ["i", "ii"])
    )

    # (1) the spelling matches an ATTESTED surname + its attested paradigm
    for ending in matches:
        for surname in split_ending(epithet_l, ending):
            entry = lexicon.get(surname)
            if not entry or not entry.get("verified"):
                continue
            form = person_genitive_form(surname, person_gender)
            if form.ending == ending and attach_ending(surname, ending) == epithet_l:
                notes.append(
                    "person genitive is indeclinable and independent of the "
                    f"genus gender; -{ending} from latinization paradigm "
                    f"'{form.paradigm}'"
                )
                notes.append(form.reason)
                return result(True, expected=ending)

    # (2) an attested surname carried by a DIFFERENT ending → real violation
    for surname, entry in sorted(lexicon.items()):
        if not entry or not entry.get("verified"):
            continue
        if not epithet_l.startswith(surname) or len(epithet_l) <= len(surname):
            continue
        remainder = epithet_l[len(surname):]
        if remainder not in endings:
            continue
        form = person_genitive_form(surname, person_gender)
        published = attach_ending(surname, form.ending)
        note = (
            "the -i/-ii tolerance does not override an attested published form"
            if {remainder, form.ending} == tolerance else ""
        )
        warnings.append(
            f"genitive ending -{remainder} does not match the attested "
            f"'{published}' (paradigm '{form.paradigm}'"
            + (f"; {note}" if note else "") + "). "
            "NOTE: the genus gender is irrelevant for genitive nouns."
        )
        return result(False, expected=form.ending)

    # (3) unattested surname: a proposal from the default rules, never a ✓
    matched = max(matches, key=len)
    # the plain strip (not the vowel-coalescence recovery of step 1, which only
    # makes sense for an attested surname)
    surname = min(split_ending(epithet_l, matched), key=len)
    try:
        form = person_genitive_form(surname, person_gender)
    except (GenitiveCellUnavailable, ValueError) as exc:
        warnings.append(str(exc))
        return result(None, review=True)

    expected = form.ending
    notes.append(
        f"surname '{surname}' is not attested; ending proposed by paradigm "
        f"'{form.paradigm}'"
    )
    if attach_ending(surname, expected) == epithet_l:
        warnings.append(
            f"ending -{expected} comes from an UNVERIFIED cell ({form.reason}); "
            "compliance is not asserted"
        )
        return result(None, review=True, expected=expected)
    if {expected, matched} == tolerance:
        warnings.append(
            f"accepted only as the documented -{matched}/-{expected} orthographic "
            "variant (person_genitive.json variant_tolerance): neither form is "
            "attested for this surname, so the pair cannot be refuted"
        )
        return result(True, expected=expected)
    warnings.append(
        f"genitive ending -{matched} does not match the proposed ending "
        f"-{expected} of the '{form.paradigm}' latinization for {person_gender} "
        f"honouree '{surname}'. NOTE: the genus gender is irrelevant for "
        "genitive nouns."
    )
    return result(
        None, review=True, expected=expected, extra_notes=[
            "surname latinization is unverified, so the mismatch is reported as "
            "needs_review rather than as a violation"
        ]
    )


def _invariant_result(
    genus, epithet, etype, invariant, warnings, notes
) -> ValidationResult:
    """An epithet carrying an invariant ending (rules.json epithet_invariant).

    '-cola' ('inhabitant of …') is indeclinable, so no gender agreement
    applies. The rule is shipped unverified, so the verdict is needs_review
    until an LPSN instance signs it off.
    """
    notes.append(
        f"epithet carries the invariant ending -{invariant['ending']}: "
        "indeclinable, no gender agreement applies"
    )
    verified = bool(invariant.get("verified"))
    if not verified:
        warnings.append(
            f"invariant-ending rule '-{invariant['ending']}' is flagged "
            "unverified in rules.json (epithet_invariant) — needs M0 sign-off"
        )
    return ValidationResult(
        genus=genus, epithet=epithet, etymology_type=etype,
        grammatical_category=invariant.get("category", "appositive"),
        gender=None, expected_ending=None,
        compliant=True if verified else None,
        warnings=warnings, notes=notes, needs_review=not verified,
    )


def _appositive_result(genus, epithet, etype, warnings, categories, notes) -> ValidationResult:
    """Invariant / non-adjectival fallback: needs review, never an implied ✓.

    Being "not an adjective" is not evidence of being a noun in apposition
    ('qqqzz' used to come out of here stamped compliant). The
    appositive category is therefore only asserted when the epithet is attested
    — see epithet_indeclinable / epithet_invariant above.
    """
    rule = prohibited_rule("epithet_not_analysable")
    warnings.append(
        "epithet is neither an attested adjective paradigm form nor an attested "
        "indeclinable epithet: noun-in-apposition status cannot be inferred "
        "from spelling and must be confirmed against the etymology"
        + (f" (prohibited pattern '{rule['id']}': {rule.get('source')})"
           if rule else "")
    )
    return ValidationResult(
        genus=genus,
        epithet=epithet,
        etymology_type=etype,
        grammatical_category=GrammaticalCategory.APPOSITIVE.value,
        gender=None,
        expected_ending=None,
        compliant=None,
        warnings=warnings,
        notes=notes + [
            "noun in apposition / invariant form: no gender agreement applies; "
            "only orthography is checkable"
        ],
        needs_review=True,
    )
