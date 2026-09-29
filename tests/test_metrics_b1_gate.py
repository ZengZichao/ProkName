"""Regression: the B1 miss-rate gate must fail closed — and must be able
to open once the data can actually test the claim.

Synthetic engines and synthetic case sets are injected (the frozen
``benchmark/data/*.json`` files stay untouched) so these properties are tested
independently of how many negative cases the seed set happens to contain:

1. abstaining on everything cannot satisfy the target (it used to: FNR 0.0);
2. a branch with no negative cases cannot be certified;
3. a missed violation fails, even with a huge sample;
4. with enough negatives and no misses the gate *does* pass — so the
   fail-closed default is a statement about the data, not a broken gate;
5. an engine crash is scored as a refusal and fails the gate.
"""

from __future__ import annotations

import pytest

from prokname.benchmark import evaluator
from prokname.benchmark.evaluator import evaluate_b1_set
from prokname.benchmark.sets import B1TestCase

BRANCHES = ("person", "place", "feature", "thing")
# more than min_n_for_zero_events(0.01) == 299
ENOUGH_NEGATIVES = 300


class _VR:
    """Stand-in for engine.validate.ValidationResult (only .compliant is read)."""

    def __init__(self, compliant):
        self.compliant = compliant
        self.warnings: list[str] = []
        self.notes: list[str] = []


def _synthetic_set(neg_per_branch: int, pos_per_branch: int = 5) -> list[B1TestCase]:
    cases = []
    for branch in BRANCHES:
        for i in range(neg_per_branch):
            cases.append(B1TestCase("Genus", f"{branch}_neg{i}", branch, False))
        for i in range(pos_per_branch):
            cases.append(B1TestCase("Genus", f"{branch}_pos{i}", branch, True))
    return cases


def _install(monkeypatch, cases, verdict_fn):
    """Replace the frozen set and the engine validator with fakes."""
    monkeypatch.setattr(evaluator, "load_b1_set", lambda: cases)
    expected = {c.epithet: c.expected_compliant for c in cases}

    def fake_validate(genus, epithet, etymology_type, **kwargs):
        return _VR(verdict_fn(epithet, etymology_type, expected))

    monkeypatch.setattr(
        "prokname.engine.validate.validate_agreement", fake_validate
    )
    return expected


NEVER_MISSES = lambda epithet, etype, exp: exp[epithet]  # noqa: E731


# ---------------------------------------------------------------------------
# The shipped seed data
# ---------------------------------------------------------------------------

def test_seed_data_cannot_certify_the_target():
    report = evaluate_b1_set()
    gate = report["gate"]
    assert gate["passed"] is False
    assert gate["status"] == "insufficient_evidence"
    assert gate["observed_fnr_within_target"] is True  # the point estimate is fine
    assert gate["target_statistically_certified"] is False  # the sample is not
    # per-branch floor: feature/thing have no negative cases at all
    branches = report["negatives_per_etymology_type"]
    assert branches["feature"]["negative_cases"] == 0
    assert branches["thing"]["negative_cases"] == 0
    assert branches["feature"]["observed_fnr"] is None
    assert branches["feature"]["meets_min_negatives"] is False


# ---------------------------------------------------------------------------
# Injected scenarios
# ---------------------------------------------------------------------------

def test_perfect_engine_still_fails_without_any_negatives(monkeypatch):
    cases = _synthetic_set(neg_per_branch=0)
    _install(monkeypatch, cases, NEVER_MISSES)
    report = evaluate_b1_set()
    assert report["negative_cases"] == 0
    assert report["false_negative_rate"]["value"] is None
    assert report["false_negative_rate"]["interval_method"] == "not_computable_no_cases"
    assert report["gate"]["passed"] is False
    assert report["gate"]["status"] == "insufficient_evidence"
    assert "nothing to test" in " ".join(report["gate"]["reasons"])


def test_abstaining_engine_cannot_pass(monkeypatch):
    """The exploit the review found: all-abstain used to score FNR 0.0."""
    cases = _synthetic_set(neg_per_branch=40)
    _install(monkeypatch, cases, lambda e, t, exp: None)
    report = evaluate_b1_set()
    assert report["false_negative_rate"]["value"] == 1.0
    assert report["verdict_coverage"]["value"] == 0.0
    assert report["accuracy"]["value"] == 0.0
    assert report["gate"]["passed"] is False
    assert report["gate"]["status"] == "failed"
    assert report["degenerate_strategy_checks"]["engine_abstains_on_everything"][
        "observed_fnr"
    ] == 1.0


def test_calling_everything_compliant_cannot_pass(monkeypatch):
    cases = _synthetic_set(neg_per_branch=40)
    _install(monkeypatch, cases, lambda e, t, exp: True)
    report = evaluate_b1_set()
    assert report["false_negative_rate"]["value"] == 1.0
    assert report["gate"]["passed"] is False


def test_a_single_missed_violation_fails_the_gate(monkeypatch):
    cases = _synthetic_set(neg_per_branch=ENOUGH_NEGATIVES)

    def misses_one(epithet, etype, exp):
        # The miss is *compliant=True on a violation*: the engine certifies a
        # name it should have rejected.  (`False` — what this line used to
        # return — is the CORRECT verdict for a negative case, so the scenario
        # produced zero misses and the gate had nothing to fail on.)
        return True if epithet == "person_neg0" else exp[epithet]

    _install(monkeypatch, cases, misses_one)
    report = evaluate_b1_set()
    fnr = report["false_negative_rate"]
    assert fnr["events"] == 1
    assert fnr["n"] == 4 * ENOUGH_NEGATIVES
    # 1/1200 = 0.0008, i.e. BELOW the 1 % target: judged on the rate alone this
    # gate would pass.  That reading is what an audit objects to, so the gate also
    # demands that no violation was missed at all.  (The line this replaces
    # asserted ``fnr["value"] > 0.01``, which no implementation can produce
    # from events=1 and n=1200.)
    assert fnr["value"] == pytest.approx(1 / (4 * ENOUGH_NEGATIVES), abs=1e-4)
    assert fnr["value"] < 0.01
    assert report["gate"]["observed_fnr_within_target"] is True
    assert report["gate"]["no_missed_violations"] is False
    assert report["gate"]["missed_violation_cases"] == 1
    assert report["gate"]["passed"] is False
    assert report["gate"]["status"] == "failed"
    # the failure is localised to the branch that owns the miss
    assert report["negatives_per_etymology_type"]["person"]["missed_negative_cases"] == 1
    assert [c["epithet"] for c in report["missed_violations"]] == ["person_neg0"]


def test_enough_negatives_and_no_miss_opens_the_gate(monkeypatch):
    cases = _synthetic_set(neg_per_branch=ENOUGH_NEGATIVES)
    _install(monkeypatch, cases, NEVER_MISSES)
    report = evaluate_b1_set()
    assert report["negative_cases"] == 4 * ENOUGH_NEGATIVES
    assert report["false_negative_rate"]["value"] == 0.0
    assert report["false_negative_rate"]["one_sided_upper_bound"] <= 0.01
    gate = report["gate"]
    assert gate["evidence_sufficient"] is True
    assert gate["target_statistically_certified"] is True
    assert gate["passed"] is True
    assert gate["status"] == "passed"


def test_one_empty_branch_blocks_certification(monkeypatch):
    """0/1200 pooled is meaningless if a branch contributes no negatives."""
    cases = [
        c for c in _synthetic_set(neg_per_branch=ENOUGH_NEGATIVES)
        if not (c.etymology_type == "thing" and not c.expected_compliant)
    ]
    _install(monkeypatch, cases, NEVER_MISSES)
    report = evaluate_b1_set()
    assert report["negatives_per_etymology_type"]["thing"]["negative_cases"] == 0
    assert report["gate"]["evidence_sufficient"] is False
    assert report["gate"]["passed"] is False


def test_engine_crash_is_scored_as_refusal_and_fails(monkeypatch):
    cases = _synthetic_set(neg_per_branch=ENOUGH_NEGATIVES)
    monkeypatch.setattr(evaluator, "load_b1_set", lambda: cases)
    expected = {c.epithet: c.expected_compliant for c in cases}

    def boom(genus, epithet, etymology_type, **kwargs):
        if epithet == "person_neg5":
            raise RuntimeError("engine exploded")
        return _VR(expected[epithet])

    monkeypatch.setattr("prokname.engine.validate.validate_agreement", boom)
    report = evaluate_b1_set()
    assert len(report["engine_error_cases"]) == 1
    assert report["engine_error_cases"][0]["error"].startswith("RuntimeError")
    assert report["gate"]["no_engine_errors"] is False
    assert report["gate"]["status"] == "engine_error"
    assert report["gate"]["passed"] is False


def test_abstention_on_a_positive_case_is_disclosed_not_verified(monkeypatch):
    """The `thing` situation: expected compliant, engine returns None."""
    cases = [B1TestCase("Genus", "x_pos0", "thing", True)]
    _install(monkeypatch, cases, lambda e, t, exp: None)
    report = evaluate_b1_set()
    assert report["accuracy"]["value"] == 0.0
    assert report["accuracy_among_verdicts_issued"]["value"] is None
    unverifiable = report["structurally_unverifiable_cases"]
    assert unverifiable["count"] == 1
    assert unverifiable["by_etymology_type"] == {"thing": 1}
    assert unverifiable["cases"][0]["reason"] == "no_rule_returns_none"


# ---------------------------------------------------------------------------
# Configurability
# ---------------------------------------------------------------------------

def test_target_and_floor_are_configurable_with_disclosure(monkeypatch):
    cases = _synthetic_set(neg_per_branch=40)
    _install(monkeypatch, cases, NEVER_MISSES)
    # 0/160 negatives has a one-sided 95 % bound of 1-0.05**(1/160) ≈ 0.0184,
    # so a 2 % target IS certifiable here while a 1 % target is not.
    loose = evaluate_b1_set(min_negatives_per_branch=40, target_fnr=0.02)
    assert loose["gate"]["passed"] is True
    assert loose["gate"]["target_fnr"] == 0.02
    strict = evaluate_b1_set(min_negatives_per_branch=40, target_fnr=0.01)
    assert strict["gate"]["passed"] is False
    assert strict["gate"]["target_statistically_certified"] is False


def test_zero_floor_reinstates_the_vacuous_pass_but_is_loud(monkeypatch):
    cases = _synthetic_set(neg_per_branch=0)
    _install(monkeypatch, cases, NEVER_MISSES)
    report = evaluate_b1_set(min_negatives_per_branch=0, target_fnr=0.5)
    assert report["gate"]["min_negatives_per_branch"] == 0
    # the target is still untestable because there are no negatives at all
    assert report["gate"]["passed"] is False
    assert report["false_negative_rate"]["value"] is None
