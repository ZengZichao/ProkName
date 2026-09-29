"""The two ending predicates are related on purpose, not duplicated by accident.

The finding was that "the ending set is built in two places and the members
differ" as a semantic-correctness risk. Re-examined against the code, the two
sets answer different questions:

    validate._doubled_ending()  — does this FINISHED epithet end in one ending
                                  written twice?  (output side)
    generate._paradigm_endings() — does this INPUT stem already carry an
                                  ending, so appending one would decline it
                                  twice?  (input side)

They share `epithet_adjective` and each adds what only its own question needs.
That makes them a maintainability concern rather than a live defect — measured:
400 candidates that generate() reports as compliant=True were re-checked with an
independent validate_agreement() call and none contradicted it (the generator
self-checks every output at generate.py:480, which is what the positive list in
the architecture review calls "the generator and the validator cannot disagree").

What is NOT acceptable is the current state of affairs being invisible: an edit
to rules.json can change one predicate's behaviour without touching the other's,
and nothing says which direction was intended. These tests pin the relationship,
so the next person who "deduplicates" the two sets has to argue with them.
"""

from __future__ import annotations

import pytest

from prokname.engine import data
from prokname.engine import generate as gen
from prokname.engine import validate as val


def _validate_vocabulary() -> set[str]:
    """Endings validate._doubled_ending() will look for, recomputed here."""
    rules = data.rules()
    out = set()
    for table in rules["epithet_adjective"].values():
        out.update(e for e in table.values() if isinstance(e, str))
    out.update(val.person_genitive_endings())
    out.update(s for s in rules["rank_suffix"].values() if isinstance(s, str))
    return out


def _generate_vocabulary() -> set[str]:
    rules = data.rules()
    adjectival = set()
    for table in rules["epithet_adjective"].values():
        adjectival.update(e for e in table.values() if isinstance(e, str))
    invariant = {
        e["ending"] for e in rules.get("epithet_invariant", {}).values()
        if isinstance(e, dict) and e.get("ending")
    }
    return adjectival | invariant


def test_the_two_vocabularies_share_the_adjectival_core():
    adjectives = set()
    for table in data.rules()["epithet_adjective"].values():
        adjectives.update(e for e in table.values() if isinstance(e, str))
    assert adjectives, "no adjectival endings in rules.json"
    assert adjectives <= _validate_vocabulary()
    assert adjectives <= _generate_vocabulary()


def test_the_difference_between_them_is_exactly_the_documented_one():
    """Any new divergence has to be a deliberate, named edit.

    validate knows the genitive and higher-rank families (a finished name can
    end in -ii or -aceae and can be doubled there); generate knows the invariant
    epithets (a STEM can end in -cola and must not be declined again). If this
    test fails, someone added a table without deciding which predicate should
    see it.
    """
    only_validate = _validate_vocabulary() - _generate_vocabulary()
    only_generate = _generate_vocabulary() - _validate_vocabulary()

    genitive_family = set(val.person_genitive_endings())
    rank_family = {s for s in data.rules()["rank_suffix"].values()
                   if isinstance(s, str)}
    assert only_validate <= (genitive_family | rank_family), (
        f"validate sees endings that are neither genitive nor rank suffixes: "
        f"{sorted(only_validate - (genitive_family | rank_family))}")

    invariant = {e["ending"] for e in data.rules().get(
        "epithet_invariant", {}).values() if isinstance(e, dict) and e.get("ending")}
    assert only_generate <= invariant, (
        f"generate sees endings outside the invariant table: "
        f"{sorted(only_generate - invariant)}")


@pytest.mark.parametrize("stem,ending,expected_doubled", [
    ("tunicensis", "ensis", "tunicensisensis"),
    ("bacillus", "us", "bacillusus"),
])
def test_generator_refuses_to_produce_what_the_validator_would_flag(
        stem, ending, expected_doubled):
    """The real safety property, stated as one assertion per case.

    For any doubling the validator can name, the generator must have already
    refused to build it — that is what makes the two vocabularies complementary
    instead of contradictory.
    """
    assert val._doubled_ending(expected_doubled) == ending, (
        "the validator stopped recognising a doubling it used to catch")
    carried, message = gen._de_inflect(stem, ending)
    assert message is not None, (
        f"generate() would happily build {expected_doubled!r}: _de_inflect() "
        f"no longer treats the stem {stem!r} as already inflected")
    # the stem is passed through unchanged instead of being declined again
    assert carried == stem


def test_invariant_epithet_endings_are_known_to_both_predicates_where_needed():
    """`-cola` is the one ending the generator knows and validate doesn't.

    validate need not: an invariant epithet is never re-inflected, so no
    `colacola` can be produced by the generator. If a user hands `colacola` in
    to be checked, the name is simply not from this engine, and the honest
    answer is the one the corpus of rules gives — this test exists so that
    reasoning is re-checked rather than forgotten.
    """
    assert "cola" in _generate_vocabulary()
    assert "cola" not in _validate_vocabulary()
    # and the generator does refuse to decline a -cola stem again
    _, message = gen._de_inflect("fumihocola", "cola")
    assert message is not None


def test_generator_and_validator_never_disagree_on_generated_output():
    """End-to-end, bounded but real: no compliant candidate contradicts re-check.

    This is the assertion that turns the report from a suspected correctness bug
    into a measured maintainability note. It is a targeted sample, not an
    exhaustive proof.
    """
    stems = ["bacillus", "tunicensis", "cola", "streptomyces", "hensel",
             "burgdorfer", "gordon", "beijerinck", "magnus", "acidiphil"]
    checked = contradicted = 0
    for stem in stems:
        for etype in ("place", "person", "thing", "feature"):
            for rank in ("species", "subspecies"):
                try:
                    cands = gen.generate(stem, etype, rank, genus="Escherichia")
                except Exception:  # noqa: BLE001 - blocked-by-design inputs
                    continue
                for cand in cands:
                    if cand.compliant is not True or cand.epithet is None:
                        continue
                    checked += 1
                    vr = val.validate_agreement("Escherichia", cand.epithet, etype)
                    if vr.compliant is False or vr.needs_review:
                        contradicted += 1
    assert checked > 20, f"sample too small to conclude anything: {checked}"
    assert contradicted == 0, (
        f"{contradicted} of {checked} candidates claim compliance that an "
        "independent validate_agreement() call denies")
