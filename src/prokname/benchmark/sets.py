"""Test set data structures and loaders.

Each test case carries full provenance: source, lpsn_id, label,
grammatical_category, annotator, annotated_at, license — as required by the
the benchmark design section 2.2.

The seed test sets shipped here are v0.1 skeletons for development and CI;
paper-grade sets are LPSN-derived, holdout-controlled M1 deliverables built
via the official API export scripts .
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

DATA_PACKAGE = "prokname.benchmark.data"


@dataclass
class ATestCase:
    """A-set: genus gender determination case."""
    genus: str
    label: str  # "m" | "f" | "n" | "unknown"
    grammatical_category: str = "gender"
    source: str = "lpsn"
    lpsn_id: str | None = None
    annotator: str = "seed"
    annotated_at: str = "2026-08-16"
    license: str = "CC BY-SA 4.0"
    in_lexicon: bool = False  # True if this genus is in genus_gender.json
    note: str = ""


@dataclass
class B1TestCase:
    """B1-set: agreement validation case (compliant or non-compliant)."""
    genus: str
    epithet: str
    etymology_type: str
    expected_compliant: bool  # True=compliant, False=non-compliant (neg)
    grammatical_category: str = ""
    person_gender: str | None = None
    adjective_formation: str | None = None
    source: str = "lpsn"
    lpsn_id: str | None = None
    annotator: str = "seed"
    annotated_at: str = "2026-08-16"
    license: str = "CC BY-SA 4.0"
    note: str = ""


@dataclass
class B2TestCase:
    """B2-set: generation exact-match case."""
    genus: str
    stem: str
    etymology_type: str
    expected_epithet: str
    person_gender: str | None = None
    adjective_formation: str | None = None
    grammatical_category: str = ""
    genus_in_lexicon: bool = False
    source: str = "lpsn"
    lpsn_id: str | None = None
    annotator: str = "seed"
    annotated_at: str = "2026-08-16"
    license: str = "CC BY-SA 4.0"
    note: str = ""


@dataclass
class CTestCase:
    """C-set: GAN comparison case (etymology → genus candidates)."""
    stem: str
    etymology_type: str
    source_language: str = "transliterated"
    grammatical_category: str = ""
    source: str = "benchmark-seed"
    lpsn_id: str | None = None
    annotator: str = "seed"
    annotated_at: str = "2026-08-16"
    license: str = "CC0"


@dataclass
class DTestCase:
    """D-set: routing correctness case."""
    source_label: str  # "pure_culture" | "MAG" | "SAG" | "unknown"
    candidatus: bool
    icnp_occupied: bool  # True/False
    expected_codes: list[str]  # acceptable path codes
    expected_roles: list[str]  # acceptable roles
    is_icnp_preemption: bool = False  # ICNP preemption sub-class
    grammatical_category: str = "routing"
    source: str = "benchmark-seed"
    lpsn_id: str | None = None
    annotator: str = "seed"
    annotated_at: str = "2026-08-16"
    license: str = "CC0"
    note: str = ""


# ---- Loaders ---------------------------------------------------------------

def _load_json(filename: str) -> dict:
    """Load a benchmark data file from the package data directory."""
    # No silent fallback locations: data lives in the package, and a missing
    # file must surface as an honest FileNotFoundError, never as a "worked
    # from an empty side directory" illusion (tests/testdata is empty and
    # deliberately unused).
    ref = resources.files(DATA_PACKAGE).joinpath(filename)
    try:
        return json.loads(ref.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"benchmark data file not found in package data: {filename} "
            f"(expected at {ref})"
        ) from exc


def load_a_set() -> list[ATestCase]:
    """Load the A-set (gender determination) test cases."""
    data = _load_json("a_set.json")
    return [
        ATestCase(
            genus=item["genus"],
            label=item["label"],
            grammatical_category=item.get("grammatical_category", "gender"),
            source=item.get("source", "lpsn"),
            lpsn_id=item.get("lpsn_id"),
            annotator=item.get("annotator", "seed"),
            annotated_at=item.get("annotated_at", "2026-08-16"),
            license=item.get("license", "CC BY-SA 4.0"),
            in_lexicon=item.get("in_lexicon", False),
            note=item.get("note", ""),
        )
        for item in data["cases"]
    ]


def load_b1_set() -> list[B1TestCase]:
    """Load the B1-set (agreement validation) test cases."""
    data = _load_json("b1_set.json")
    return [
        B1TestCase(
            genus=item["genus"],
            epithet=item["epithet"],
            etymology_type=item["etymology_type"],
            expected_compliant=item["expected_compliant"],
            grammatical_category=item.get("grammatical_category", ""),
            person_gender=item.get("person_gender"),
            adjective_formation=item.get("adjective_formation"),
            source=item.get("source", "lpsn"),
            lpsn_id=item.get("lpsn_id"),
            annotator=item.get("annotator", "seed"),
            annotated_at=item.get("annotated_at", "2026-08-16"),
            license=item.get("license", "CC BY-SA 4.0"),
            note=item.get("note", ""),
        )
        for item in data["cases"]
    ]


def load_b2_set() -> list[B2TestCase]:
    """Load the B2-set (generation exact-match) test cases."""
    data = _load_json("b2_set.json")
    return [
        B2TestCase(
            genus=item["genus"],
            stem=item["stem"],
            etymology_type=item["etymology_type"],
            expected_epithet=item["expected_epithet"],
            person_gender=item.get("person_gender"),
            adjective_formation=item.get("adjective_formation"),
            grammatical_category=item.get("grammatical_category", ""),
            genus_in_lexicon=item.get("genus_in_lexicon", False),
            source=item.get("source", "lpsn"),
            lpsn_id=item.get("lpsn_id"),
            annotator=item.get("annotator", "seed"),
            annotated_at=item.get("annotated_at", "2026-08-16"),
            license=item.get("license", "CC BY-SA 4.0"),
            note=item.get("note", ""),
        )
        for item in data["cases"]
    ]


def load_c_set() -> list[CTestCase]:
    """Load the C-set (GAN comparison) test cases."""
    data = _load_json("c_set.json")
    return [
        CTestCase(
            stem=item["stem"],
            etymology_type=item["etymology_type"],
            source_language=item.get("source_language", "transliterated"),
            grammatical_category=item.get("grammatical_category", ""),
            source=item.get("source", "benchmark-seed"),
            lpsn_id=item.get("lpsn_id"),
            annotator=item.get("annotator", "seed"),
            annotated_at=item.get("annotated_at", "2026-08-16"),
            license=item.get("license", "CC0"),
        )
        for item in data["cases"]
    ]


def load_d_set() -> list[DTestCase]:
    """Load the D-set (routing correctness) test cases."""
    data = _load_json("d_set.json")
    return [
        DTestCase(
            source_label=item["source_label"],
            candidatus=item.get("candidatus", False),
            icnp_occupied=item["icnp_occupied"],
            expected_codes=item["expected_codes"],
            expected_roles=item["expected_roles"],
            is_icnp_preemption=item.get("is_icnp_preemption", False),
            grammatical_category=item.get("grammatical_category", "routing"),
            source=item.get("source", "benchmark-seed"),
            lpsn_id=item.get("lpsn_id"),
            annotator=item.get("annotator", "seed"),
            annotated_at=item.get("annotated_at", "2026-08-16"),
            license=item.get("license", "CC0"),
            note=item.get("note", ""),
        )
        for item in data["cases"]
    ]
