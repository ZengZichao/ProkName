"""Etymology-driven candidate generation.

Pipeline: stem extraction → grammatical-category determination → suffix
selection (per category) → join & orthographic cleanup → self-validation.

Suprageneric ranks (family … phylum) are built on the GENITIVE STEM of the type
genus, never on the nominative the user typed: *Bacillus*
(gen. *Bacilli*) → **bacill** + -aceae. The stem comes from the versioned
declension lexicon in data/type_genus_stems.json; where a genus is missing, a
documented morphological fallback proposes a stem but the candidate is flagged
needs_review, and compliance is only ever asserted for a name that the asset
attests as published.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import data
from .categories import EtymologyType, GrammaticalCategory, categories_for
from .gender import Gender, gender_of
from .genitive import GenitiveCellUnavailable, person_genitive_form
from .orthography import (
    attach_ending,
    join,
    latinize,
    latinize_words,
    stem_ending_type,
)
from .validate import (
    DEFAULT_ADJECTIVE_FORMATION,
    invariant_epithet_rule,
    prohibited_rule,
    validate_agreement,
)


def known_ranks() -> dict[str, str | None]:
    return data.rules()["rank_suffix"]


def _higher_ranks() -> set[str]:
    return {r for r, s in known_ranks().items() if s}


def rank_suffix_policy(rank: str) -> dict:
    """Mandatory-vs-convention metadata for a suprageneric suffix."""
    return data.rules().get("rank_suffix_policy", {}).get(rank, {})


@dataclass
class Candidate:
    name: str
    epithet: str | None
    rank: str
    grammatical_category: str | None
    gender: str | None
    derivation: str
    compliant: bool | None
    warnings: list[str] = field(default_factory=list)
    needs_review: bool = False  # True = the verdict could not be asserted

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "epithet": self.epithet,
            "rank": self.rank,
            "grammatical_category": self.grammatical_category,
            "gender": self.gender,
            "derivation": self.derivation,
            "compliant": self.compliant,
            "warnings": self.warnings,
            "needs_review": self.needs_review,
        }


# --------------------------------------------------------------- B1 machinery
@dataclass(frozen=True)
class GenitiveStem:
    """Result of reducing a type-genus nominative to its genitive stem."""

    query: str
    nominative: str | None
    stem: str
    mode: str          # lexicon | lexicon_stem | fallback | raw
    verified: bool
    rule: str | None
    reason: str

    def as_dict(self) -> dict:
        return {
            "query": self.query, "nominative": self.nominative, "stem": self.stem,
            "mode": self.mode, "verified": self.verified, "rule": self.rule,
            "reason": self.reason,
        }


def _asset() -> dict:
    return data.type_genus_stems()


def genitive_stem_of(name: str) -> GenitiveStem:
    """Derive the genitive (declension) stem behind a suprageneric name.

    Resolution order — attested lexicon, already-reduced stem, documented
    morphological fallback, otherwise the input is carried through UNVERIFIED
    (mode 'raw') so the caller can still show a proposal but never a ✓.
    """
    query = latinize(name)
    asset = _asset()
    genera = asset["genera"]
    capitalized = query.capitalize()

    entry = genera.get(capitalized)
    if entry is not None and isinstance(entry, dict):
        return GenitiveStem(
            query=query, nominative=capitalized, stem=entry["stem"],
            mode="lexicon", verified=bool(entry.get("verified")),
            rule=entry.get("declension"),
            reason=(
                f"attested genitive stem '{entry['stem']}' of {capitalized} "
                f"({entry.get('declension')}; genitive {entry.get('genitive')}) — "
                f"{entry.get('source', 'no source given')}"
            ),
        )

    for genus, value in genera.items():
        if isinstance(value, dict) and latinize(value.get("stem", "\x00")) == query:
            return GenitiveStem(
                query=query, nominative=genus, stem=value["stem"],
                mode="lexicon_stem", verified=bool(value.get("verified")),
                rule=value.get("declension"),
                reason=(
                    f"input is already the attested genitive stem of {genus} "
                    f"({value.get('source', 'no source given')})"
                ),
            )

    for rule in sorted(
        asset.get("fallback_rules", []),
        key=lambda r: len(r.get("nominative_ending", "")), reverse=True,
    ):
        ending = rule.get("nominative_ending", "")
        if not ending or not query.endswith(ending):
            continue
        if len(query) <= len(ending) + 1:
            continue
        stem = query[: len(query) - len(ending)] + rule.get("replacement", "")
        return GenitiveStem(
            query=query, nominative=capitalized, stem=stem, mode="fallback",
            verified=False, rule=rule.get("id"),
            reason=(
                f"no attested entry for '{capitalized}'; stem reduced by "
                f"documented fallback rule '{rule.get('id')}' "
                f"(-{ending} → -{rule.get('replacement', '')}) which is anchored "
                f"to {rule.get('anchor', 'no published instance')} — verify"
            ),
        )

    return GenitiveStem(
        query=query, nominative=capitalized, stem=query, mode="raw",
        verified=False, rule=None,
        reason=(
            f"'{capitalized}' carries no recognised Latin nominative ending, so no "
            "genitive stem can be derived; it is treated as an already-reduced "
            "stem and nothing may be asserted about the result"
        ),
    )


def _attestation(name: str) -> dict | None:
    for entry in _asset().get("attested_higher_rank_names", []):
        if latinize(entry.get("name", "")) == latinize(name):
            return entry
    return None


def _exception_name(nominative: str | None, query: str, rank: str) -> dict | None:
    """Conserved/historical suprageneric name that overrides suffixation."""
    exceptions = _asset().get("higher_rank_exceptions", {})
    if nominative and nominative in exceptions:
        entry = exceptions[nominative].get(rank)
        if entry:
            return entry
    for genus, ranks in exceptions.items():
        if latinize(genus) == query and rank in ranks:
            return ranks[rank]
    return None


def _compose_higher_rank(stem: str, suffix: str) -> str:
    """stem + rank suffix with Latin vowel coalescence.

    A genitive stem ending in -i + a suffix starting with i loses one i:
    clostridi + ia → Clostridia, corynebacteri + ineae → Corynebacterineae,
    acidimicrobi + idae → Acidimicrobidae (all published forms).
    """
    if stem.endswith("i") and len(suffix) > 1 and suffix.startswith("i"):
        suffix = suffix[1:]
    return (stem + suffix).capitalize()


def _higher_rank_candidates(stem_l: str, rank_l: str, etype: str) -> list[Candidate]:
    ranks = known_ranks()
    suffix = ranks[rank_l]
    policy = rank_suffix_policy(rank_l)
    derived = genitive_stem_of(stem_l)
    warnings: list[str] = []

    exception = _exception_name(derived.nominative, derived.query, rank_l)
    if exception is not None:
        word = str(exception["name"])
        attested = _attestation(word) or {}
        verified = bool(exception.get("verified")) and bool(attested.get("verified", True))
        derivation = (
            f"type genus {derived.nominative or stem_l!r} → conserved/historical "
            f"{rank_l} name '{word}' (suffix -{suffix} NOT applied; "
            f"{exception.get('source', '')})"
        )
        warnings.append(
            f"conserved name: '{word}' does not follow the regular -{suffix} "
            "formation and must not be 'corrected'"
        )
        return [Candidate(
            name=word, epithet=None, rank=rank_l, grammatical_category=None,
            gender=None, derivation=derivation,
            compliant=True if verified else None,
            warnings=warnings, needs_review=not verified,
        )]

    word = _compose_higher_rank(derived.stem, suffix)
    attested = _attestation(word)
    strength = policy.get("strength", "unknown")
    derivation = (
        f"type genus {derived.nominative or stem_l!r} → genitive stem "
        f"'{derived.stem}' ({derived.mode}; {derived.reason}) + {rank_l} suffix "
        f"-{suffix} ({strength})"
    )

    if derived.mode in ("raw", "fallback"):
        rule = prohibited_rule("higher_rank_name_not_from_a_type_genus_stem")
        warnings.append(
            "genitive stem is NOT attested: " + derived.reason
            + (f" (prohibited pattern '{rule['id']}': {rule.get('source')})"
               if rule else "")
        )
    if attested is None:
        warnings.append(
            f"'{word}' is not attested as a published {rank_l} name in "
            "data/type_genus_stems.json — compliance is not asserted"
        )
    if policy and policy.get("strength") == "adopted_botanical_code_convention":
        warnings.append(
            f"suffix -{suffix} is an adopted botanical-code convention for "
            f"{rank_l}, not a mandatory ICNP termination (rules.json "
            "rank_suffix_policy) — compliance is not asserted"
        )

    can_assert = (
        derived.verified
        and attested is not None
        and bool(attested.get("verified"))
        and policy.get("strength") in ("mandatory_icnp", "recommended_by_seqcode")
        and bool(policy)
    )
    if can_assert:
        derivation += f"; attested as published: {attested.get('source', '')}"
    return [Candidate(
        name=word, epithet=None, rank=rank_l, grammatical_category=None,
        gender=None, derivation=derivation,
        compliant=True if can_assert else None,
        warnings=warnings, needs_review=not can_assert,
    )]


# ------------------------------------------------------------------ public API
def generate(
    stem: str,
    etymology_type: str,
    rank: str,
    *,
    genus: str | None = None,
    person_gender: str | None = None,
    gender_override: Gender | None = None,
    genus_suffix: str | None = None,
    adjective_formation: str | None = None,
    connecting_vowel: str | None = None,
) -> list[Candidate]:
    """Generate candidate names for a stem at a target rank."""
    stem_l = latinize(stem)
    if not stem_l:
        raise ValueError(f"stem {stem!r} latinizes to nothing")
    etype = EtymologyType(etymology_type).value
    rank_l = rank.strip().lower()
    ranks = known_ranks()
    if rank_l not in ranks:
        raise ValueError(f"unknown rank {rank!r}; known: {sorted(ranks)}")
    cv = connecting_vowel if connecting_vowel is not None else data.rules()["connecting_vowel"]

    if rank_l in _higher_ranks():
        return _higher_rank_candidates(stem_l, rank_l, etype)

    if rank_l == "genus":
        parts = data.rules()["genus_formation_hints"]["suggestions"].get(etype)
        candidates: list[Candidate] = []
        endings = [genus_suffix] if genus_suffix else (parts or [None])
        for ending in endings:
            if ending:
                word = join(stem_l, latinize(ending), cv).capitalize()
                derivation = (
                    f"stem '{stem_l}' + suggestive ending -{latinize(ending)} "
                    "(genus names carry no mandatory suffix)"
                )
            else:
                word = stem_l.capitalize()
                derivation = f"stem '{stem_l}' (genus names carry no mandatory suffix)"
            candidates.append(
                Candidate(
                    name=word,
                    epithet=None,
                    rank="genus",
                    grammatical_category=None,
                    gender=None,
                    derivation=derivation,
                    compliant=None,
                    warnings=[] if ending else [
                        "no suffix applied; consider --genus-suffix "
                        f"(suggestions for '{etype}': {parts})"
                    ] if parts else [],
                    needs_review=True,
                )
            )
        return candidates

    # species / subspecies: epithet formation, category-aware
    if genus is None:
        raise ValueError(
            f"--genus is required for rank {rank_l!r} (epithets decline against a genus)"
        )
    genus_tokens = latinize_words(genus)
    if not genus_tokens:
        raise ValueError(f"--genus {genus!r} latinizes to nothing")
    # genus token capitalized; a species token in a supplied binomial stays lowercase
    genus_clean = " ".join([genus_tokens[0].capitalize(), *genus_tokens[1:]])
    # subspecies: --genus may be a full binomial ("Bacillus subtilis");
    # gender is decided by the genus (first word), the binomial carries the name
    genus_token = genus_tokens[0]
    gr = gender_of(genus_token, gender_override)

    out: list[Candidate] = []
    for category in _generation_categories(etype, stem_l):
        epithet: str | None
        derivation: str
        warnings: list[str] = []
        needs_review = False
        stem_for_epithet = stem_l

        if category is GrammaticalCategory.ADJECTIVE:
            formation = adjective_formation or DEFAULT_ADJECTIVE_FORMATION.get(
                etype, "second_declension"
            )
            endings = data.rules()["epithet_adjective"][formation]
            if gr.gender is None:
                epithet = None
                derivation = "adjective formation blocked"
                warnings = [
                    f"cannot decline adjective: {gr.reason} "
                    "(supply --gender after expert review, or extend the lexicon)"
                ]
            else:
                ending = endings[gr.gender.value]
                stem_for_epithet, inflected = _de_inflect(stem_l, ending)
                if inflected:
                    warnings.append(inflected)
                    needs_review = True
                epithet = (
                    stem_for_epithet if inflected
                    else join(stem_for_epithet, ending, cv)
                )
                derivation = (
                    f"stem '{stem_l}' + -{ending} (adjective, formation "
                    f"'{formation}', genus {genus_token.capitalize()} is "
                    f"{gr.gender.value}, "
                    + ("lexicon)" if gr.mode == "lookup" else "inferred — verify)")
                )
                if gr.needs_review:
                    warnings.append(f"genus gender: {gr.reason}")

        elif category is GrammaticalCategory.PARTICIPLE:
            epithet = stem_l
            derivation = (
                f"stem '{stem_l}' as participle / common-gender adjective "
                "(single form for m, f and n — see rules.json epithet_indeclinable)"
            )

        elif category is GrammaticalCategory.GENITIVE and etype == "person":
            if person_gender is None:
                epithet = None
                derivation = "person genitive blocked"
                warnings = [
                    "--person-gender (male/female, of the honoured person) is "
                    "required for person-derived genitive epithets"
                ]
            else:
                try:
                    form = person_genitive_form(stem_l, person_gender)
                    stem_for_epithet, inflected = _de_inflect(stem_l, form.ending)
                    if inflected:
                        warnings.append(inflected)
                        needs_review = True
                    epithet = (
                        stem_for_epithet if inflected
                        else attach_ending(stem_for_epithet, form.ending)
                    )
                    derivation = (
                        f"stem '{stem_l}' + -{form.ending} (genitive noun, latinization "
                        f"paradigm '{form.paradigm}', honoured person {person_gender}, "
                        f"{stem_ending_type(stem_l)}-stem; independent of genus gender; "
                        f"{form.reason})"
                    )
                    if not form.verified:
                        warnings.append(
                            f"genitive ending -{form.ending} is unverified: "
                            + form.reason
                        )
                        needs_review = True
                except (GenitiveCellUnavailable, ValueError) as exc:
                    # Only the EXPECTED outcomes (unsigned M0 cell / invalid
                    # input) become a blocked candidate. Anything else is a
                    # real engine defect and must propagate, not masquerade
                    # as "person genitive blocked".
                    epithet = None
                    derivation = "person genitive blocked"
                    warnings = [str(exc)]

        elif category is GrammaticalCategory.GENITIVE:  # thing
            epithet = stem_l
            derivation = (
                f"stem '{stem_l}' as genitive noun; formation depends on the "
                "source noun's declension — verify against etymology"
            )
            warnings = [
                "thing-genitive formation is not fully machine-checkable; "
                "expert review required"
            ]

        else:  # appositive
            epithet = stem_l
            invariant = invariant_epithet_rule(stem_l)
            if invariant is not None:
                derivation = (
                    f"stem '{stem_l}' ends in the invariant epithet "
                    f"-{invariant['ending']} ('{invariant.get('source', '')}'): "
                    "indeclinable, no gender agreement applies"
                )
            else:
                derivation = f"stem '{stem_l}' as noun in apposition (indeclinable)"

        if epithet is None:
            out.append(
                Candidate(
                    name=f"{genus_clean} [?]" if rank_l == "species"
                    else f"{genus_clean} [...] subsp. [?]",
                    epithet=None,
                    rank=rank_l,
                    grammatical_category=category.value,
                    gender=gr.gender.value if gr.gender else None,
                    derivation=derivation,
                    compliant=None,
                    warnings=warnings,
                    needs_review=True,
                )
            )
            continue

        if rank_l == "species":
            name = f"{genus_clean} {epithet}"
        else:
            name = f"{genus_clean} subsp. {epithet}"
        validation = validate_agreement(
            genus_token,
            epithet,
            etype,
            person_gender=person_gender,
            gender_override=gender_override,
            adjective_formation=adjective_formation,
        )
        if validation.grammatical_category and not needs_review:
            category = GrammaticalCategory(validation.grammatical_category)
        compliant = validation.compliant
        if needs_review and compliant is True:
            # never assert compliance on an input we had to repair (B2)
            compliant = None
        out.append(
            Candidate(
                name=name,
                epithet=epithet,
                rank=rank_l,
                grammatical_category=category.value,
                gender=gr.gender.value if gr.gender else None,
                derivation=derivation,
                compliant=compliant,
                warnings=warnings + validation.warnings,
                needs_review=needs_review or validation.needs_review,
            )
        )
    return out


def _generation_categories(etype: str, stem_l: str) -> list[GrammaticalCategory]:
    """Categories to emit for a stem.

    'feature' is the ambiguous type (adjective / participle / appositive). Only
    one analysis is emitted per spelling: an indeclinable stem (a participle in
    -ans/-ens, an attested common-gender adjective, an -cola compound) is never
    declined, and a declinable stem is never offered as an appositive duplicate
    of its own spelling.
    """
    categories = categories_for(etype)
    if GrammaticalCategory.PARTICIPLE not in categories:
        return categories
    if invariant_epithet_rule(stem_l) is not None:
        # an -cola compound: indeclinable noun, never a participle, never declined
        return [GrammaticalCategory.APPOSITIVE]
    if _indeclinable_shaped(stem_l):
        return [GrammaticalCategory.PARTICIPLE]
    return [c for c in categories if c is not GrammaticalCategory.PARTICIPLE]


def _indeclinable_shaped(stem_l: str) -> bool:
    spec = data.rules().get("epithet_indeclinable", {})
    for entry in spec.get("endings", []):
        ending = entry.get("ending", "")
        if ending and stem_l.endswith(ending) and len(stem_l) > len(ending) + 1:
            return True
    for anchor in spec.get("anchors", []):
        if latinize(anchor.get("epithet", "")) == stem_l:
            return True
    return False


def _paradigm_endings() -> list[str]:
    """Every inflectional ending the epithet tables know (longest first).

    Consumed only by _de_inflect(), which tests an INPUT STEM for an ending it
    already carries. The complementary predicate on the OUTPUT side is
    validate._doubled_ending(); it uses a wider vocabulary (person-genitive and
    rank suffixes too) because a finished name can end in those. The two sets
    are intentionally different — see validate._doubled_ending and
    tests/test_ending_predicates.py before "unifying" them.
    """
    endings: set[str] = set()
    for table in data.rules()["epithet_adjective"].values():
        endings.update(e for e in table.values() if isinstance(e, str))
    for entry in data.rules().get("epithet_invariant", {}).values():
        if isinstance(entry, dict) and entry.get("ending"):
            endings.add(entry["ending"])
    return sorted(endings, key=len, reverse=True)


def _known_genus_nominative(stem_l: str) -> str | None:
    """Is the stem a genus dictionary form (which must not be re-inflected)?"""
    capitalized = stem_l.capitalize()
    if capitalized in data.gender_lexicon():
        return capitalized
    if capitalized in _asset()["genera"]:
        return capitalized
    return None


def _de_inflect(stem_l: str, ending: str) -> tuple[str, str | None]:
    """Detect a stem that already carries a Latin inflectional ending (B2).

    Users type dictionary forms: *Bacillus* + -us used to yield 'bacillusus',
    the place suffix -ensis on the stem 'tunicensis' yielded 'tunicensisensis',
    and a genus nominative such as *Streptomyces* was declined twice over. When
    that happens the ending is NOT applied, the stem is passed through as the
    epithet, and the candidate is flagged needs_review — a doubled or bogus
    form must never be handed to the user as a clean candidate.
    """
    if ending and len(stem_l) > len(ending) + 1 and stem_l.endswith(ending):
        return stem_l, _already_inflected_message(f"-{ending}")
    for candidate in _paradigm_endings():
        # Only long, distinctive endings (-ensis, -cola, -philus, …) are used
        # here: short ones would fire on ordinary stems.
        if len(candidate) < 4 or candidate == ending:
            continue
        if len(stem_l) > len(candidate) + 1 and stem_l.endswith(candidate):
            return stem_l, _already_inflected_message(f"-{candidate}")
    genus = _known_genus_nominative(stem_l)
    if genus is not None:
        # The stem is a genus dictionary form (Bacillus, Streptomyces): appending
        # an adjectival ending to a nominative is what produced 'streptomycesus'.
        return stem_l, _already_inflected_message(
            f"(nominative of the genus {genus})"
        )
    return stem_l, None


def _already_inflected_message(ending: str) -> str:
    return (
        f"stem already carries the Latin inflectional ending {ending}; it was "
        "not applied a second time (no doubled candidate). Supply the "
        "uninflected stem if a different form was intended."
    )
