"""Built-in regression seed (`prokname bench`).

This is the engine's always-available smoke/regression set: real LPSN-anchored
cases including the counter-examples that motivated the v1.3 rule fixes. It is
NOT the paper-grade benchmark (A/B/C/D sets are LPSN-derived, holdout-controlled
M1 deliverables); it exists so every code change is
checked against the rules it claims to implement.
"""

from __future__ import annotations

from .gender import gender_of
from .generate import generate
from .validate import validate_agreement

VALIDATE_CASES = [
    # the counter-example that anchors the genitive-noun fix
    {"genus": "Shigella", "epithet": "boydii", "etymology_type": "person",
     "person_gender": "male", "expect": True},
    {"genus": "Lactobacillus", "epithet": "delbrueckii", "etymology_type": "person",
     "person_gender": "male", "expect": True},
    # negative: feminine-looking ending on a male-person genitive is wrong
    # REGARDLESS of genus gender
    {"genus": "Shigella", "epithet": "boydiae", "etymology_type": "person",
     "person_gender": "male", "expect": False},
    # one case per latinization paradigm — the 2x2 gender table got
    # three of these wrong
    {"genus": "Borrelia", "epithet": "burgdorferi", "etymology_type": "person",
     "person_gender": "male", "expect": True},
    {"genus": "Bartonella", "epithet": "henselae", "etymology_type": "person",
     "person_gender": "male", "expect": True},
    {"genus": "Mycobacterium", "epithet": "gordonae", "etymology_type": "person",
     "person_gender": "male", "expect": True},
    {"genus": "Coxiella", "epithet": "burnetii", "etymology_type": "person",
     "person_gender": "male", "expect": True},
    {"genus": "Rickettsia", "epithet": "rickettsii", "etymology_type": "person",
     "person_gender": "male", "expect": True},
    {"genus": "Lactobacillus", "epithet": "werkmanii", "etymology_type": "person",
     "person_gender": "male", "expect": True},
    # -i / -ii variant tolerance — reachable only now that 'i' is a
    # populated ending; the attested form still wins over the tolerance
    {"genus": "Borrelia", "epithet": "burgdorferii", "etymology_type": "person",
     "person_gender": "male", "expect": False},
    {"genus": "Escherichia", "epithet": "smithi", "etymology_type": "person",
     "person_gender": "male", "expect": True},
    {"genus": "Escherichia", "epithet": "smithiae", "etymology_type": "person",
     "person_gender": "female", "expect": None},
    # place adjectives
    {"genus": "Klebsiella", "epithet": "michiganensis", "etymology_type": "place",
     "expect": True},
    {"genus": "Rhizobium", "epithet": "mongolense", "etymology_type": "place",
     "expect": True},
    {"genus": "Rhizobium", "epithet": "mongolensis", "etymology_type": "place",
     "expect": False},
    # real place epithets that are NOT formed with -ensis
    {"genus": "Thermus", "epithet": "aquaticus", "etymology_type": "place",
     "expect": True},
    {"genus": "Lactobacillus", "epithet": "helveticus", "etymology_type": "place",
     "expect": True},
    # second / third declension adjectives
    {"genus": "Bacillus", "epithet": "subtilis",
     "etymology_type": "feature", "adjective_formation": "third_declension",
     "expect": True},
    # A genuine 3rd-declension adjective on a NEUTER genus (-e), replacing
    # the self-contradictory 'Clostridium tertium' case that passed only because
    # an unanalysable ending was mis-read as a noun in apposition.
    {"genus": "Clostridium", "epithet": "perfringens",
     "etymology_type": "feature", "expect": True,
     "expect_category": "participle"},
    # controls that MUST be able to fail: the old seed could not fail for any
    # spelling, these two pin that the assertion now has teeth.
    {"genus": "Clostridium", "epithet": "qqqzz",
     "etymology_type": "feature", "expect": None},
    {"genus": "Clostridium", "epithet": "tertium",
     "etymology_type": "feature", "adjective_formation": "third_declension",
     "expect": None},
    # a real 3rd-declension adjective on a neuter genus: the -e form
    {"genus": "Clostridium", "epithet": "clostridioforme",
     "etymology_type": "feature", "adjective_formation": "third_declension",
     "expect": True, "expect_category": "adjective"},
    {"genus": "Thermus", "epithet": "thermophilus",
     "etymology_type": "feature", "adjective_formation": "loving",
     "expect": True},
    # nourishing paradigm anchored to real names
    {"genus": "Streptomyces", "epithet": "autotrophicus",
     "etymology_type": "feature", "adjective_formation": "nourishing",
     "expect": True},
    {"genus": "Pseudonocardia", "epithet": "autotrophica",
     "etymology_type": "feature", "adjective_formation": "nourishing",
     "expect": True},
    # thing genitive: documented needs-review semantics
    {"genus": "Vibrio", "epithet": "cholerae", "etymology_type": "thing",
     "expect": None},
    # B2: epithet may not repeat / re-latinize the genus name (ICNP Rule 20 note)
    {"genus": "Bacillus", "epithet": "bacillus", "etymology_type": "feature",
     "expect": False},
    {"genus": "Bacillus", "epithet": "bacillusus", "etymology_type": "feature",
     "expect": False},
    {"genus": "Bacillus", "epithet": "tunicensisensis", "etymology_type": "place",
     "expect": None},
]

GENERATE_CASES = [
    {"stem": "Boyd", "etymology_type": "person", "rank": "species",
     "genus": "Shigella", "person_gender": "male",
     "expect_name": "Shigella boydii"},
    {"stem": "Beijing", "etymology_type": "place", "rank": "species",
     "genus": "Rhizobium",
     "expect_name": "Rhizobium beijingense"},  # neuter → -ense
    {"stem": "Beijing", "etymology_type": "place", "rank": "species",
     "genus": "Klebsiella",
     "expect_name": "Klebsiella beijingensis"},
    {"stem": "bacteri", "etymology_type": "feature", "rank": "phylum",
     "expect_name": "Bacteriota"},
    # B1: suprageneric names are built on the type genus' GENITIVE STEM.
    # The old seed asserted 'Beijingaceae', which is doubly wrong (no stem
    # reduction, and a family cannot be typified by a place name at all).
    {"stem": "Bacillus", "etymology_type": "feature", "rank": "family",
     "expect_name": "Bacillaceae", "expect_compliant": True},
    {"stem": "Bacillus", "etymology_type": "feature", "rank": "order",
     "expect_name": "Bacillales", "expect_compliant": True},
    {"stem": "Clostridium", "etymology_type": "feature", "rank": "order",
     "expect_name": "Clostridiales", "expect_compliant": True},
    {"stem": "Streptomyces", "etymology_type": "feature", "rank": "family",
     "expect_name": "Streptomycetaceae", "expect_compliant": True},
    {"stem": "Pseudomonas", "etymology_type": "feature", "rank": "phylum",
     "expect_name": "Pseudomonadota", "expect_compliant": True},
    {"stem": "Clostridium", "etymology_type": "feature", "rank": "class",
     "expect_name": "Clostridia", "expect_compliant": True},
    {"stem": "Bacillus", "etymology_type": "feature", "rank": "class",
     "expect_name": "Bacilli", "expect_compliant": True,
     "forbid_name": "Bacillia"},
    {"stem": "Beijing", "etymology_type": "place", "rank": "family",
     "expect_name": "Beijingaceae", "expect_compliant": None},
    # B2: an already-inflected stem is not inflected a second time
    {"stem": "Bacillus", "etymology_type": "feature", "rank": "species",
     "genus": "Bacillus", "forbid_name": "Bacillus bacillusus"},
    {"stem": "tunicensis", "etymology_type": "place", "rank": "species",
     "genus": "Bacillus", "expect_name": "Bacillus tunicensis",
     "expect_compliant": None},
]

GENDER_CASES = [
    # (genus, expected gender, expected needs_review)
    ("Escherichia", "f", False),
    ("Bacillus", "m", False),
    ("Rhizobium", "n", False),
    ("Treponema", "n", False),          # Greek -ma neuter, lexicon hit
    ("Exempluma", "n", True),           # synthetic: -ma exception via inference
    ("Closteridium", "n", True),        # synthetic: -um via inference
    ("Frankomonas", "f", True),         # synthetic: -monas morpheme via inference
    ("Zzz", None, True),                # nothing known: refuse to guess
]


def run_bench() -> dict:
    """Run the built-in regression set; returns a machine-readable report."""
    results: list[dict] = []
    passed = 0
    total = 0

    for case in VALIDATE_CASES:
        total += 1
        got = validate_agreement(
            case["genus"], case["epithet"], case["etymology_type"],
            person_gender=case.get("person_gender"),
            adjective_formation=case.get("adjective_formation"),
        )
        ok = got.compliant == case["expect"]
        if ok and "expect_category" in case:
            ok = got.grammatical_category == case["expect_category"]
        passed += ok
        results.append({
            "kind": "validate", "case": {
                k: v for k, v in case.items()
                if k not in ("expect", "expect_category")
            },
            "expected_compliant": case["expect"], "got_compliant": got.compliant,
            "expected_category": case.get("expect_category"),
            "got_category": got.grammatical_category,
            "passed": ok,
            "warnings": got.warnings,
        })

    for case in GENERATE_CASES:
        total += 1
        candidates = generate(
            case["stem"], case["etymology_type"], case["rank"],
            genus=case.get("genus"), person_gender=case.get("person_gender"),
        )
        names = [c.name for c in candidates]
        expected = case.get("expect_name")
        ok = expected is None or expected in names
        if ok and "forbid_name" in case:
            ok = case["forbid_name"] not in names
        if ok and "expect_compliant" in case:
            matching = [
                c for c in candidates
                if c.name == (expected or c.name)
            ]
            ok = any(c.compliant == case["expect_compliant"] for c in matching)
        passed += ok
        results.append({
            "kind": "generate", "case": {
                k: v for k, v in case.items()
                if k not in ("expect_name", "expect_compliant", "forbid_name")
            },
            "expected_name": expected, "got_names": names,
            "expected_compliant": case.get("expect_compliant"),
            "forbidden_name": case.get("forbid_name"),
            "passed": ok,
        })

    for genus, expect_gender, expect_review in GENDER_CASES:
        total += 1
        gr = gender_of(genus)
        ok = (
            (gr.gender.value if gr.gender else None) == expect_gender
            and gr.needs_review == expect_review
        )
        passed += ok
        results.append({
            "kind": "gender", "case": {"genus": genus},
            "expected": {"gender": expect_gender, "needs_review": expect_review},
            "got": gr.as_dict(),
            "passed": ok,
        })

    return {
        "suite": "builtin-regression-seed",
        "note": (
            "engine smoke/regression seed only; paper-grade A/B/C/D benchmark "
            "sets are LPSN-derived, holdout-controlled M1 deliverables "
            ""
        ),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "results": results,
    }
