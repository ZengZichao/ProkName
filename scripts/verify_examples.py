"""M0 deliverable: example real-name verification script.

Verifies that every example name documented in this repository and the
data assets corresponds to a real prokaryotic name, with the expected gender /
rank / grammatical category. Prevents the "Bacillia" type of error (a plausible
but non-existent name entering the authoritative rule tables).

Three guards keep this script capable of failing; each covers a way it could
  * every species-level case now declares an EXPECTED tri-state compliance
    verdict (True / False / None) — the old 'person' branch printed ✓ whenever
    the engine abstained;
  * the higher-rank cases are no longer counted twice (once hand-written, once
    from rules.json), and a duplicate (name, rank) judgement now fails loudly;
  * the rank-suffix branch also checks the GENERATOR: for every name in
    rules.json rank_suffix_examples, generate(<type genus>, 'feature', <rank>)
    must reproduce it — the data asset and the code can no longer
    disagree in silence.

Usage:
    python scripts/verify_examples.py                # offline: check internal consistency
    python scripts/verify_examples.py --online      # also query LPSN for real-name existence
    python scripts/verify_examples.py --json         # machine-readable report

Exit codes:
    0  all examples verified (or offline-pass with warnings)
    1  one or more examples failed internal consistency
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make src importable without installation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from prokname.engine.categories import categories_for  # noqa: E402
from prokname.engine.data import rules  # noqa: E402
from prokname.engine.gender import gender_of  # noqa: E402
from prokname.engine.generate import generate  # noqa: E402
from prokname.engine.validate import validate_agreement  # noqa: E402


class _Unset:
    """Sentinel: a case without a declared expectation is a script defect."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unset>"


UNSET = _Unset()


@dataclass
class ExampleCase:
    """A single example to verify."""
    name: str           # full name, e.g. "Shigella boydii"
    genus: str          # genus name, e.g. "Shigella"
    epithet: str        # specific epithet, e.g. "boydii"
    expected_gender: str  # "m" | "f" | "n"
    etymology_type: str   # "person" | "place" | "thing" | "feature"
    grammatical_category: str = ""  # expected category if known
    person_gender: str | None = None
    adjective_formation: str | None = None
    rank: str = "species"
    source: str = "engineering-plan"
    note: str = ""
    # The verdict the engine MUST produce (True / False / None). None here
    # means "the honest answer is needs-review"; UNSET means the case was never
    # given an expectation, which fails the run.
    expected_compliant: bool | None | _Unset = UNSET
    # B1: for suprageneric examples, the type genus whose genitive stem the
    # generator has to reduce, and the name it has to produce.
    type_genus: str | None = None
    expected_suffix: str | None = None
    conserved: bool = False


# All example names from the rule-asset sections, and the bench
# seed. Each is anchored to a real published name with its expected attributes.
EXAMPLES: list[ExampleCase] = [
    # §2.4 Category A: true adjectives
    ExampleCase("Bacillus velezensis", "Bacillus", "velezensis", "m", "place",
                grammatical_category="adjective", expected_compliant=True,
                note="§2.4 place adjective, m (3rd-declension place form)"),
    ExampleCase("Klebsiella michiganensis", "Klebsiella", "michiganensis", "f", "place",
                grammatical_category="adjective", expected_compliant=True,
                note="§2.4 place adjective, f"),
    ExampleCase("Rhizobium mongolense", "Rhizobium", "mongolense", "n", "place",
                grammatical_category="adjective", expected_compliant=True,
                note="§2.4 place adjective, n"),
    ExampleCase("Thermus aquaticus", "Thermus", "aquaticus", "m", "place",
                grammatical_category="adjective", expected_compliant=True,
                note="M2: a place adjective that is NOT formed with -ensis"),
    ExampleCase("Bacillus subtilis", "Bacillus", "subtilis", "m", "feature",
                adjective_formation="third_declension", expected_compliant=True,
                note="§2.4 third declension adjective"),
    ExampleCase("Vibrio vulgaris", "Vibrio", "vulgaris", "m", "feature",
                adjective_formation="third_declension", expected_compliant=True,
                note="§2.4 third declension adjective"),
    ExampleCase("Xanthomonas campestris", "Xanthomonas", "campestris", "f", "feature",
                adjective_formation="third_declension", expected_compliant=True,
                note="replaces 'Clostridium tertium', which is 2nd declension"),
    ExampleCase("Clostridium tertium", "Clostridium", "tertium", "n", "feature",
                adjective_formation="third_declension", expected_compliant=None,
                note="control case: -um is NOT 3rd declension; must be needs-review"),
    ExampleCase("Thermus thermophilus", "Thermus", "thermophilus", "m", "feature",
                adjective_formation="loving", expected_compliant=True,
                note="§2.4 loving paradigm"),
    ExampleCase("Streptomyces autotrophicus", "Streptomyces", "autotrophicus", "m",
                "feature", adjective_formation="nourishing", expected_compliant=True,
                note="§2.4 nourishing paradigm, m"),
    ExampleCase("Pseudonocardia autotrophica", "Pseudonocardia", "autotrophica", "f",
                "feature", adjective_formation="nourishing", expected_compliant=True,
                note="§2.4 nourishing paradigm, f"),
    # §2.4 Category A2: participles / common-gender adjectives (4th category, M2)
    ExampleCase("Mycobacterium tuberculosis", "Mycobacterium", "tuberculosis", "n",
                "feature", grammatical_category="participle", expected_compliant=True,
                note="M2: common gender; must not be forced to -e nor called 'n. in app.'"),
    ExampleCase("Streptococcus pyogenes", "Streptococcus", "pyogenes", "m",
                "feature", grammatical_category="participle", expected_compliant=True,
                note="M2: Greek participle, one form for m/f/n"),
    ExampleCase("Clostridium perfringens", "Clostridium", "perfringens", "n",
                "feature", grammatical_category="participle", expected_compliant=True,
                note="M2: present participle -ens"),
    ExampleCase("Bacillus halodurans", "Bacillus", "halodurans", "m",
                "feature", grammatical_category="participle", expected_compliant=True,
                note="M2: present participle -ans"),
    # §2.4 Category B: genitive nouns (person) — one case per latinization
    # paradigm, since the ending is NOT decided by the honoured person's sex
    ExampleCase("Shigella boydii", "Shigella", "boydii", "f", "person",
                grammatical_category="genitive", person_gender="male",
                expected_compliant=True,
                note="§2.4 counter-example: f genus + male person → boydii, not boydiae"),
    ExampleCase("Lactobacillus delbrueckii", "Lactobacillus", "delbrueckii", "m",
                "person", grammatical_category="genitive", person_gender="male",
                expected_compliant=True,
                note="§2.4 2nd-declension (-ius) genitive (Delbrück → delbrueckii)"),
    ExampleCase("Borrelia burgdorferi", "Borrelia", "burgdorferi", "f", "person",
                grammatical_category="genitive", person_gender="male",
                expected_compliant=True,
                note="B3: 3rd-declension -er surname → -i (the 2x2 table made 'burgdorferii')"),
    ExampleCase("Bartonella henselae", "Bartonella", "henselae", "f", "person",
                grammatical_category="genitive", person_gender="male",
                expected_compliant=True,
                note="B3: 1st-declension -a latinization → -ae although the person is male"),
    ExampleCase("Mycobacterium gordonae", "Mycobacterium", "gordonae", "n", "person",
                grammatical_category="genitive", person_gender="male",
                expected_compliant=True,
                note="B3: the 2x2 table produced 'gordoniae'"),
    ExampleCase("Oenococcus kitaharae", "Oenococcus", "kitaharae", "m", "person",
                grammatical_category="genitive", person_gender="male",
                expected_compliant=True,
                note="B3(d): the old script printed ✓ here while the engine could "
                     "not handle -ae vowel stems at all"),
    ExampleCase("Escherichia smithiae", "Escherichia", "smithiae", "f", "person",
                grammatical_category="genitive", person_gender="female",
                expected_compliant=None,
                note="B3(c): -iae comes from an UNVERIFIED paradigm → needs-review, "
                     "never a ✓ (expert-pending)"),
    # §2.4 Category B: genitive nouns (thing)
    ExampleCase("Vibrio cholerae", "Vibrio", "cholerae", "m", "thing",
                grammatical_category="genitive", expected_compliant=None,
                note="thing genitive (cholera → cholerae): formation is not "
                     "machine-checkable (expert-pending)"),
    # §2.4 Category C: appositive / invariant
    ExampleCase("Escherichia coli", "Escherichia", "coli", "f", "thing",
                grammatical_category="genitive", expected_compliant=None,
                note="LPSN derives 'coli' from the genitive of colon, so it is "
                     "annotated like 'cholerae' here (expert-pending), and never as "
                     "an adjective"),
    ExampleCase("Bacillus sedamicola", "Bacillus", "sedamicola", "m", "feature",
                grammatical_category="appositive", expected_compliant=None,
                note="invariant -cola compound ('inhabitant of sediment'); the "
                     "rule is unverified so nothing is asserted (expert-pending)"),
    # control spellings that must never be stamped compliant
    ExampleCase("Bacillus qqqzz", "Bacillus", "qqqzz", "m", "feature",
                expected_compliant=None,
                note="control experiment: a nonsense epithet must not be "
                     "classified as a noun in apposition"),
    ExampleCase("Bacillus bacillus", "Bacillus", "bacillus", "m", "feature",
                expected_compliant=False,
                note="B2: ICNP Rule 20 note — the epithet may not repeat the genus"),
    ExampleCase("Bacillus bacillusus", "Bacillus", "bacillusus", "m", "feature",
                expected_compliant=False,
                note="B2: latinized repeat of the genus name"),
    ExampleCase("Bacillus tunicensisensis", "Bacillus", "tunicensisensis", "m", "place",
                expected_compliant=None,
                note="B2: doubled inflection (-ensis + -ensis) → needs review"),
    # §2.3 subspecies trinomial
    ExampleCase("Bacillus subtilis subsp. spizizenii", "Bacillus", "spizizenii",
                "m", "person", rank="subspecies", person_gender="male",
                grammatical_category="genitive", expected_compliant=True,
                note="§2.3 subspecies trinomial"),
    # §2.3 conserved class name (deliberately NOT in rank_suffix_examples)
    ExampleCase("Bacilli", "Bacilli", "", "m", "feature",
                rank="class", conserved=True, expected_suffix="ia",
                type_genus="Bacillus", source="rules.json conserved_names",
                note="§2.3 conserved class name — the generator must produce "
                     "'Bacilli', never 'Bacillia'"),
]

# Higher-rank examples are read from rules.json ("rank_suffix_examples") so
# the data asset stays the single source of truth for them. Each of them is
# checked TWO ways: the suffix is present, AND the generator reproduces the
# name from the declared type genus.
def higher_rank_examples() -> list[ExampleCase]:
    examples = rules().get("rank_suffix_examples", {})
    type_genera = rules().get("rank_suffix_example_type_genera", {})
    conserved = set(rules().get("conserved_names", []))
    cases: list[ExampleCase] = []
    for rank, names in sorted(examples.items()):
        suffix = rules()["rank_suffix"].get(rank)
        for name in names:
            type_genus = type_genera.get(name)
            cases.append(ExampleCase(
                name=name, genus=name, epithet="", expected_gender="unknown",
                etymology_type="feature", rank=rank,
                source="rules.json rank_suffix_examples",
                note="§14.1 rank suffix example (type genus "
                     f"{type_genus or 'UNDECLARED'})",
                type_genus=type_genus,
                expected_suffix=suffix,
                conserved=name in conserved,
            ))
    return cases


@dataclass
class VerificationResult:
    case: ExampleCase
    passed: bool
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    expert_pending: list[str] = field(default_factory=list)
    lpsn_verified: bool | None = None  # None = not checked (offline)


def verify_internal(case: ExampleCase) -> VerificationResult:
    """Verify an example against the engine's own data assets and logic."""
    result = VerificationResult(case=case, passed=True)

    # 1. Gender: genus_gender.json lookup must match the expected gender
    gr = gender_of(case.genus)
    if gr.mode == "lookup":
        if gr.gender and gr.gender.value == case.expected_gender:
            result.checks.append(
                f"gender lookup: {case.genus} → {gr.gender.value} ✓"
            )
        else:
            result.passed = False
            result.failures.append(
                f"gender mismatch: expected {case.expected_gender}, "
                f"got {gr.gender.value if gr.gender else None} "
                f"(mode={gr.mode})"
            )
    else:
        # For higher-rank names, gender is not in the lexicon (expected)
        if case.rank != "species":
            result.checks.append(
                f"gender: {case.genus} not in lexicon (rank={case.rank}, "
                "expected for higher-rank names) ✓"
            )
        else:
            result.checks.append(
                f"gender: {case.genus} resolved via {gr.mode} "
                f"({gr.gender.value if gr.gender else 'unknown'})"
            )

    # 2. Grammatical category (for species-level examples)
    if case.rank == "species" and case.epithet:
        cats = categories_for(case.etymology_type)
        cat_values = [c.value for c in cats]
        if case.grammatical_category:
            vr = _validate(case)
            if vr.grammatical_category != case.grammatical_category:
                result.passed = False
                result.failures.append(
                    f"category mismatch: engine says "
                    f"{vr.grammatical_category!r}, example is annotated "
                    f"{case.grammatical_category!r} (categories offered for "
                    f"{case.etymology_type}: {cat_values})"
                )
            else:
                result.checks.append(
                    f"category: {case.etymology_type} → "
                    f"{vr.grammatical_category} (expected "
                    f"{case.grammatical_category}) ✓"
                )

    # 3. Validation: the epithet must produce the DECLARED tri-state verdict
    if case.rank in ("species", "subspecies") and case.epithet:
        if isinstance(case.expected_compliant, _Unset):
            result.passed = False
            result.failures.append(
                "no expected compliance verdict declared (every case must "
                "state True / False / None, so that the script can fail)"
            )
        else:
            vr = _validate(case)
            if vr.compliant != case.expected_compliant:
                result.passed = False
                result.failures.append(
                    f"validation: {case.genus} {case.epithet} → "
                    f"compliant={vr.compliant}, expected "
                    f"compliant={case.expected_compliant}; "
                    f"category={vr.grammatical_category}, "
                    f"warnings={vr.warnings}"
                )
            else:
                label = {True: "compliant", False: "NON-COMPLIANT",
                         None: "needs-review"}[vr.compliant]
                result.checks.append(
                    f"validation: {case.genus} {case.epithet} → {label} "
                    f"(as declared) {'✓' if vr.compliant is not None else '(expert-pending)'}"
                )
                if vr.compliant is None:
                    result.expert_pending.append(
                        f"{case.name}: compliant=None — {case.note}"
                    )
        if vr.compliant is True and vr.warnings:
            result.checks.append(
                f"note: asserted compliant with warnings: {vr.warnings}"
            )

    # 4. Rank suffix + generator reproduction (B1) for higher ranks
    if case.rank != "species" and not case.epithet:
        suffix = case.expected_suffix or rules()["rank_suffix"].get(case.rank)
        lowered = case.genus.lower()
        if suffix is None:
            result.checks.append(f"rank: {case.rank} has no mandatory suffix ✓")
        elif lowered.endswith(suffix):
            result.checks.append(
                f"rank suffix: {case.genus} ends with -{suffix} "
                f"({case.rank}) ✓"
            )
        elif case.conserved:
            detail = rules().get("conserved_names_detail", {}).get(case.genus, {})
            if not detail.get("verified"):
                result.passed = False
                result.failures.append(
                    f"rank suffix: {case.genus} is exempted as a conserved name "
                    f"but conserved_names_detail has no verified entry for it "
                    f"(expected -{suffix} for {case.rank})"
                )
            else:
                result.checks.append(
                    f"rank suffix: {case.genus} is a conserved {case.rank} name "
                    f"(does not end in -{suffix}) — documented exception "
                    f"[{detail.get('kind')}: {detail.get('source')}] ✓"
                )
        else:
            result.passed = False
            result.failures.append(
                f"rank suffix: {case.genus} does not end with "
                f"-{suffix} ({case.rank})"
            )

        if case.type_genus is None:
            result.passed = False
            result.failures.append(
                f"B1 check skipped: no type genus declared for {case.name} in "
                "rules.json rank_suffix_example_type_genera — a suprageneric "
                "name is meaningless without its type genus"
            )
        else:
            candidates = generate(case.type_genus, "feature", case.rank)
            names = [c.name for c in candidates]
            if names != [case.name]:
                result.passed = False
                result.failures.append(
                    f"B1: generate({case.type_genus!r}, 'feature', "
                    f"{case.rank!r}) → {names}, expected exactly "
                    f"['{case.name}'] (the data asset and the generator disagree)"
                )
            else:
                cand = candidates[0]
                if cand.compliant is False:
                    result.passed = False
                    result.failures.append(
                        f"B1: generator reproduced {case.name} but judged it "
                        f"non-compliant: {cand.warnings}"
                    )
                else:
                    result.checks.append(
                        f"generator: {case.type_genus} → {case.name} "
                        f"(compliant={cand.compliant}, "
                        f"needs_review={cand.needs_review}) ✓"
                    )
                    if cand.needs_review:
                        result.expert_pending.append(
                            f"{case.name}: stem/suffix not fully attested — "
                            f"{cand.derivation}"
                        )

    return result


def _validate(case: ExampleCase):
    return validate_agreement(
        case.genus, case.epithet, case.etymology_type,
        person_gender=case.person_gender,
        adjective_formation=case.adjective_formation,
    )


def verify_higher_rank_examples() -> list[VerificationResult]:
    """Verify the rank_suffix_examples from rules.json (suffix + generator)."""
    return [verify_internal(case) for case in higher_rank_examples()]


def verify_generation_anchors() -> list[VerificationResult]:
    """Names the generator must produce (and must never produce)."""
    anchors = [
        # (stem, etymology_type, rank, kwargs, expected name, forbidden name)
        ("Clostridium", "feature", "order", {}, "Clostridiales", None),
        ("Bacillus", "feature", "class", {}, "Bacilli", "Bacillia"),
        ("Bacill", "feature", "class", {}, "Bacilli", "Bacillia"),
        ("Streptomyces", "feature", "family", {}, "Streptomycetaceae", None),
        ("Burgdorfer", "person", "species",
         {"genus": "Borrelia", "person_gender": "male"}, "Borrelia burgdorferi",
         "Borrelia burgdorferii"),
        ("Hensel", "person", "species",
         {"genus": "Bartonella", "person_gender": "male"}, "Bartonella henselae",
         "Bartonella henselii"),
        ("Gordon", "person", "species",
         {"genus": "Mycobacterium", "person_gender": "male"}, "Mycobacterium gordonae",
         "Mycobacterium gordoniae"),
        # B2: an already-inflected stem must not be inflected a second time
        ("Bacillus", "feature", "species", {"genus": "Bacillus"}, None,
         "Bacillus bacillusus"),
        ("tunicensis", "place", "species", {"genus": "Bacillus"}, None,
         "Bacillus tunicensisensis"),
    ]
    results: list[VerificationResult] = []
    for stem, etype, rank, kwargs, expected, forbidden in anchors:
        case = ExampleCase(
            name=f"gen({stem},{etype},{rank})", genus=str(kwargs.get("genus", stem)),
            epithet="", expected_gender="unknown", etymology_type=etype, rank=rank,
            source="generation-anchor",
            note=f"generator anchor: {stem} → {expected or 'no doubled form'}",
        )
        result = VerificationResult(case=case, passed=True)
        names = [c.name for c in generate(stem, etype, rank, **kwargs)]
        if expected is not None and expected not in names:
            result.passed = False
            result.failures.append(f"expected {expected!r} among {names}")
        elif expected is not None:
            result.checks.append(f"generator: {stem} → {expected} ✓")
        if forbidden is not None and forbidden in names:
            result.passed = False
            result.failures.append(f"forbidden form produced: {forbidden!r} in {names}")
        elif forbidden is not None:
            result.checks.append(f"generator: {forbidden} never produced ✓")
        results.append(result)
    return results


def _duplicate_cases(results: list[VerificationResult]) -> list[VerificationResult]:
    """a (name, rank) judged twice inflated '30/30' with fewer real cases."""
    seen: dict[tuple[str, str], int] = {}
    for r in results:
        key = (r.case.name, r.case.rank)
        seen[key] = seen.get(key, 0) + 1
    out = []
    for (name, rank), count in sorted(seen.items()):
        if count > 1:
            out.append(VerificationResult(
                case=ExampleCase(
                    name=name, genus=name, epithet="", expected_gender="unknown",
                    etymology_type="feature", rank=rank, source="duplicate-guard",
                    note="no example may be judged twice",
                ),
                passed=False,
                failures=[f"{name!r} ({rank}) is counted {count} times — the pass "
                          "total overstates the number of distinct judgements"],
            ))
    return out


def run_verification(online: bool = False) -> dict:
    """Run all example verifications and return a structured report."""
    results = [verify_internal(case) for case in EXAMPLES]
    results.extend(verify_higher_rank_examples())
    results.extend(verify_generation_anchors())
    results.extend(_duplicate_cases(results))

    # LPSN online verification (optional, requires credentials)
    if online:
        try:
            from prokname.dedup.lpsn import check as lpsn_check
            for vr in results:
                if vr.case.rank in ("species", "subspecies") and vr.case.epithet:
                    src = lpsn_check(vr.case.name, allow_network=True)
                    if src.status == "not_found":
                        vr.passed = False
                        vr.failures.append(
                            f"LPSN: name not found ({src.detail})"
                        )
                        vr.lpsn_verified = False
                    elif src.status.startswith("found"):
                        vr.checks.append(f"LPSN: found ({src.detail})")
                        vr.lpsn_verified = True
                    else:
                        vr.checks.append(f"LPSN: {src.status} ({src.detail})")
                        vr.lpsn_verified = None
        except Exception as exc:
            for vr in results:
                vr.checks.append(f"LPSN online check skipped: {exc}")

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    pending = sorted({
        item for r in results for item in r.expert_pending
    })

    return {
        "suite": "m0-example-verification",
        "online": online,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "distinct_judgements": len({(r.case.name, r.case.rank) for r in results}),
        "expert_pending": pending,
        "results": [
            {
                "name": r.case.name,
                "genus": r.case.genus,
                "epithet": r.case.epithet,
                "rank": r.case.rank,
                "expected_gender": r.case.expected_gender,
                "etymology_type": r.case.etymology_type,
                "expected_compliant": (
                    "unset" if isinstance(r.case.expected_compliant, _Unset)
                    else r.case.expected_compliant
                ),
                "expected_compliant_declared": not isinstance(
                    r.case.expected_compliant, _Unset
                ),
                "note": r.case.note,
                "passed": r.passed,
                "checks": r.checks,
                "failures": r.failures,
                "expert_pending": r.expert_pending,
                "lpsn_verified": r.lpsn_verified,
            }
            for r in results
        ],
    }


def main() -> None:
    # Console code pages are not a safe assumption for redirected output;
    # see prokname.diagnostics.ensure_reportable_output.
    from prokname.diagnostics import ensure_reportable_output
    ensure_reportable_output()
    parser = argparse.ArgumentParser(
        description="M0 example real-name verification"
    )
    parser.add_argument("--online", action="store_true",
                        help="Also query LPSN for real-name existence")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output")
    args = parser.parse_args()

    report = run_verification(online=args.online)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        for r in report["results"]:
            status = "✓" if r["passed"] else "✗"
            print(f"{status} {r['name']}")
            for c in r["checks"]:
                print(f"    {c}")
            for f in r["failures"]:
                print(f"    FAIL: {f}")
        print(
            f"\n{report['passed']}/{report['total']} passed"
            f" ({report['distinct_judgements']} distinct judgements)"
        )
        if report["failed"] > 0:
            print(f"{report['failed']} FAILED")
        if report["expert_pending"]:
            print(f"\nExpert-pending ({len(report['expert_pending'])}) — the engine "
                  "deliberately refuses to assert compliance; these need M0 sign-off:")
            for item in report["expert_pending"]:
                print(f"    · {item}")

    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
