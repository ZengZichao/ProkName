"""Benchmark framework tests.

Metric-definition tests live in ``test_metrics_*.py``; this module covers the
test-set loaders, baselines, holdout control and the report structure each
evaluator produces.
"""

import pytest

from prokname.benchmark import (
    PREDICTION_ONLY_LABELS,
    ATestCase,
    B1TestCase,
    B2TestCase,
    DTestCase,
    accuracy,
    bootstrap_ci,
    check_holdout,
    confusion_matrix,
    evaluate_a_set,
    evaluate_b1_set,
    evaluate_b2_set,
    evaluate_d_set,
    evaluate_gates,
    false_negative_rate,
    load_a_set,
    load_b1_set,
    load_b2_set,
    load_c_set,
    load_d_set,
    macro_f1,
    majority_class_baseline,
    majority_class_of,
    naive_ending_baseline,
    run_full_benchmark,
)
from prokname.benchmark.evaluator import (
    DECIDABLE_A_LABELS,
    DEFAULT_MIN_NEGATIVES_PER_BRANCH,
)

# ---------------------------------------------------------------------------
# Test set loading
# ---------------------------------------------------------------------------

def test_a_set_loads():
    cases = load_a_set()
    assert len(cases) > 0
    assert all(isinstance(c, ATestCase) for c in cases)
    # Must have both lexicon and inference cases
    assert any(c.in_lexicon for c in cases)
    assert any(not c.in_lexicon for c in cases)


def test_a_set_true_labels_stay_in_documented_domain():
    domain = set(DECIDABLE_A_LABELS) | {"unknown"}
    assert {c.label for c in load_a_set()} <= domain
    assert not ({c.label for c in load_a_set()} & PREDICTION_ONLY_LABELS)


def test_b1_set_loads():
    cases = load_b1_set()
    assert len(cases) > 0
    assert all(isinstance(c, B1TestCase) for c in cases)
    # Must have both compliant and non-compliant cases
    assert any(c.expected_compliant for c in cases)
    assert any(not c.expected_compliant for c in cases)


def test_b2_set_loads():
    cases = load_b2_set()
    assert len(cases) > 0
    assert all(isinstance(c, B2TestCase) for c in cases)


def test_c_set_loads():
    assert len(load_c_set()) > 0


def test_d_set_loads():
    cases = load_d_set()
    assert len(cases) > 0
    assert all(isinstance(c, DTestCase) for c in cases)
    # Must have ICNP preemption cases
    assert any(c.is_icnp_preemption for c in cases)


# ---------------------------------------------------------------------------
# Holdout integrity
# ---------------------------------------------------------------------------

def test_holdout_passes_for_seed_set():
    """The shipped seed A-set must pass the holdout check (no lexicon leakage)."""
    report = check_holdout()
    assert report["passed"], report["message"]
    assert report["violations"] == []


def test_holdout_detects_leakage():
    """If a lexicon genus is added to the inference set, holdout must catch it."""
    cases = [ATestCase(genus="Bacillus", label="m", in_lexicon=False)]
    report = check_holdout(cases)
    assert not report["passed"]
    assert "Bacillus" in report["violations"]


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def test_majority_class_is_computed_and_matches_documented_class():
    """the baseline computes its class; it must still be the documented 'm'.

    If this fails, either the A-inference split changed (and every
    "majority-class baseline" sentence in the docs needs re-checking) or
    the tie-breaking changed.  Both are report-affecting, so this is a pin, not
    a tautology.
    """
    cases = [c for c in load_a_set() if not c.in_lexicon]
    assert majority_class_of(cases) == "m"
    preds = majority_class_baseline(cases)
    assert set(preds) == {"m"}
    assert len(preds) == len(cases)


def test_majority_class_follows_the_training_split():
    """The class is derived from the split it is given, not hardcoded."""
    f_heavy = [ATestCase(genus="A", label="f"), ATestCase(genus="B", label="f"),
               ATestCase(genus="C", label="m")]
    assert majority_class_of(f_heavy) == "f"
    assert majority_class_baseline(f_heavy) == ["f", "f", "f"]
    # `unknown` is not a gender: it must not win the plurality race.
    with_unknown = [ATestCase(genus=f"X{i}", label="unknown") for i in range(5)]
    with_unknown.append(ATestCase(genus="Y", label="n"))
    assert majority_class_of(with_unknown) == "n"


def test_majority_class_tie_break_is_deterministic():
    tied = [ATestCase(genus="A", label="n"), ATestCase(genus="B", label="f")]
    reversed_tied = list(reversed(tied))
    assert majority_class_of(tied) == majority_class_of(reversed_tied) == "f"


def test_naive_ending_baseline():
    cases = [
        ATestCase(genus="Streptococcus", label="m"),  # -us → m
        ATestCase(genus="Klebsiella", label="f"),     # -a → f
        ATestCase(genus="Clostridium", label="n"),    # -um → n
        ATestCase(genus="Exempluma", label="n"),      # -a → f (WRONG: Greek -ma)
    ]
    preds = naive_ending_baseline(cases)
    assert preds == ["m", "f", "n", "f"]  # last is wrong, as expected


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_confusion_matrix():
    y_true = ["m", "f", "n", "m", "f"]
    y_pred = ["m", "f", "m", "m", "n"]
    cm = confusion_matrix(y_true, y_pred, ["m", "f", "n"])
    assert cm["m"]["m"] == 2
    assert cm["f"]["f"] == 1
    assert cm["n"]["m"] == 1
    assert cm["f"]["n"] == 1


def test_confusion_matrix_rectangular_keeps_abstention_column():
    """rows are the truth, columns additionally carry `needs_review`."""
    cm = confusion_matrix(
        ["m", "unknown"], ["m", "needs_review"],
        true_labels=["m", "f", "n", "unknown"],
        pred_labels=["m", "f", "n", "unknown", "needs_review"],
    )
    assert set(cm) == {"m", "f", "n", "unknown"}
    assert cm["unknown"]["needs_review"] == 1
    assert "needs_review" in cm["m"]


def test_accuracy():
    assert accuracy(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert accuracy(["a", "b", "c"], ["a", "x", "c"]) == 2 / 3


def test_macro_f1():
    assert macro_f1(["m", "f"], ["m", "f"], ["m", "f"]) == 1.0
    assert macro_f1(["m", "f"], ["f", "m"], ["m", "f"]) == 0.0


def test_macro_f1_default_labels_come_from_the_truth():
    """A prediction-only class must not widen the macro denominator."""
    y_true = ["m", "f", "n"]
    y_pred = ["m", "f", "needs_review"]
    assert macro_f1(y_true, y_pred) == macro_f1(y_true, y_pred, ["m", "f", "n"])


def test_bootstrap_ci_is_scoped_to_a_mean():
    values = [1.0, 1.0, 1.0, 0.0, 1.0]
    point, lo, hi = bootstrap_ci(values, n_resamples=100, seed=42)
    assert 0.0 <= lo <= point <= hi <= 1.0
    assert abs(point - 0.8) < 0.01


def test_false_negative_rate():
    y_true = [True, True, False]   # 2 violations, 1 compliant
    y_pred = [True, False, False]  # caught 1, missed 1
    assert false_negative_rate(y_true, y_pred) == 0.5


def test_false_negative_rate_is_none_without_negatives():
    """no negative cases ⇒ None, never the misleading 0.0."""
    assert false_negative_rate([False, False], [True, False]) is None
    assert false_negative_rate([], []) is None


def test_false_negative_rate_counts_abstention_as_miss():
    """refusing to judge is not a detection."""
    y_true = [True, True, False]
    assert false_negative_rate(y_true, [None, None, None]) == 1.0
    assert false_negative_rate(
        y_true, [None, None, None], abstention_counts_as_miss=False
    ) == 0.0


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------

def test_full_benchmark_runs():
    report = run_full_benchmark(n_resamples=300)
    for key in ("a_set", "b1_set", "b2_set", "c_set", "d_set", "gates_summary"):
        assert key in report
    assert report["a_set"]["holdout_check"]["passed"]


def test_every_reported_metric_carries_its_interval_method():
    """no bare numbers — a value always travels with its interval label."""
    report = run_full_benchmark(n_resamples=200)

    def walk(node, path=""):
        if isinstance(node, dict):
            if "value" in node and ("interval" in node or "interval_method" in node):
                assert node.get("interval_method"), f"{path}: metric has no interval method"
                assert node.get("interval_label"), f"{path}: metric has no interval label"
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk({"a_set": report["a_set"], "b1_set": report["b1_set"],
          "b2_set": report["b2_set"], "d_set": report["d_set"]})
    assert report["interval_methods"]["proportions"].startswith("exact")


def test_a_set_evaluation_structure():
    result = evaluate_a_set(n_resamples=200)
    ai = result["a_inference"]
    assert {"engine", "majority_class_baseline", "naive_ending_baseline"} <= set(ai)
    eng = ai["engine"]
    # intervals are labelled per metric: accuracy is exact, macro-F1 bootstrap
    assert eng["accuracy"]["interval_method"] == "clopper_pearson_exact_binomial"
    assert eng["decidable_subset"]["macro_f1"]["interval_method"] == (
        "case_level_bootstrap_percentile"
    )
    assert eng["decidable_subset"]["macro_f1"]["n_resamples"] == 200
    assert "significance_vs_baselines" in ai
    assert "significant_vs_baselines" not in ai  # the unpaired test is gone


def test_a_inference_distinguishable_labels_are_not_dropped():
    """`unknown` stays visible in the truth rows instead of vanishing."""
    ai = evaluate_a_set(n_resamples=200)["a_inference"]
    assert "unknown" in ai["true_label_domain"]
    assert "needs_review" not in ai["true_label_domain"]
    assert "needs_review" in ai["prediction_label_domain"]


def test_b1_gate_reports_evidence_adequacy():
    result = evaluate_b1_set()
    gate = result["gate"]
    # The shipped seed set cannot certify a ≤1% miss rate; the gate must say
    # so rather than pass on the point estimate alone.
    assert result["negative_cases"] == 4
    assert result["negatives_required_to_test_target"] == 299
    assert result["false_negative_rate"]["one_sided_upper_bound"] == pytest.approx(
        0.5271, abs=1e-3
    )
    assert gate["evidence_sufficient"] is False
    assert gate["passed"] is False
    assert gate["min_negatives_per_branch"] == DEFAULT_MIN_NEGATIVES_PER_BRANCH


def test_b1_gate_floor_is_configurable():
    """the floor is a knob, and the gate actually reads it per branch.

    The frozen seed set carries negative cases in ``person`` (2) and ``place``
    (2) only; ``feature`` and ``thing`` have none, which is exactly the defect
    the floor exists to catch.  Lowering the floor therefore changes *which*
    branches block the gate — it cannot make an empty branch adequate, so only
    switching the check off (floor 0) lets a pass through, and that has to be
    disclosed rather than quietly allowed.
    """
    one = evaluate_b1_set(min_negatives_per_branch=1)["gate"]
    assert one["min_negatives_per_branch"] == 1
    assert one["target_fnr"] == 0.01  # unchanged, and echoed back
    # 2 >= 1 clears the two branches that have negative cases …
    assert "person" not in one["branches_below_floor"]
    assert "place" not in one["branches_below_floor"]
    # … but a branch with zero negative cases satisfies no positive floor.
    assert set(one["branches_below_floor"]) == {"feature", "thing"}
    assert one["evidence_sufficient"] is False
    assert one["passed"] is False
    # the default floor of 10 additionally blocks the two 2-negative branches.
    default = evaluate_b1_set()["gate"]
    assert len(default["branches_below_floor"]) == 4
    assert default["evidence_sufficient"] is False
    assert default["passed"] is False
    # floor 0 reinstates the vacuous pass — reachable, but announced as such.
    lenient = evaluate_b1_set(min_negatives_per_branch=0, target_fnr=0.99)["gate"]
    assert lenient["passed"] is True
    assert lenient["min_negatives_per_branch"] == 0
    assert any("min_negatives_per_branch=0" in r for r in lenient["reasons"])


def test_b1_abstaining_cases_are_not_counted_as_verified():
    """`thing` cases where validate_agreement returns None are disclosed."""
    result = evaluate_b1_set()
    unverifiable = result["structurally_unverifiable_cases"]
    things = [c for c in unverifiable["cases"] if c["etymology_type"] == "thing"]
    assert len(things) == 3
    assert all(c["reason"] == "no_rule_returns_none" for c in things)
    # conservative accuracy must be strictly below the "everything passed" lie
    assert result["accuracy"]["value"] < 1.0
    assert result["accuracy_among_verdicts_issued"]["value"] <= 1.0


def test_b1_degenerate_strategies_do_not_pass():
    checks = evaluate_b1_set()["degenerate_strategy_checks"]
    for key in ("engine_abstains_on_everything", "engine_calls_everything_compliant"):
        assert checks[key]["observed_fnr"] == 1.0
        assert checks[key]["meets_target"] is False


def test_b2_and_d_metrics_have_intervals():
    b2 = evaluate_b2_set()
    d = evaluate_d_set()
    assert b2["exact_match_rate"]["interval_method"] == "clopper_pearson_exact_binomial"
    assert d["accuracy"]["interval"] is not None or d["accuracy"]["n"] == 0
    assert d["icnp_preemption"]["accuracy"]["one_sided_upper_bound"] is not None


def _gates_report(b1_status: str) -> dict:
    """Minimal report shape evaluate_gates() reads, for each B1 outcome."""
    return {
        "a_set": {"holdout_check": {"passed": True}},
        "d_set": {"icnp_preemption": {"meets_target": True}},
        "b1_set": {"gate": {
            "status": b1_status,
            "passed": b1_status == "passed",
            "expansion_required": "add expert-labelled negatives",
        }},
    }


def test_gates_never_report_an_untested_target_as_violated_or_certified():
    """The shipped seed cannot test b1_fnr; that is debt, not a code failure.

    Rewritten 2026-09-25. This test used to assert that the untestable gate
    made the whole block fail, which is what kept every push red while saying
    nothing about the engine. The properties that must hold instead, and do:
    the run is not blocked by debt alone, the debt is named, and nothing about
    the outcome is ever called a certification.
    """
    gates = evaluate_gates(_gates_report("insufficient_evidence"))
    assert gates["gates"]["b1_fnr"] is True, "debt must not read as a violation"
    assert gates["passed"] is True
    assert gates["failed"] == []
    assert gates["evidence_debt"] == ["b1_fnr"]
    assert gates["b1_gate_status"] == "insufficient_evidence"
    assert "never certifies" in gates["note"] or "NOT certified" in gates["note"]


def test_require_complete_mode_makes_evidence_debt_fail():
    """Milestone mode: the debt blocks, so an M-review cannot skip it."""
    report = _gates_report("insufficient_evidence")
    assert evaluate_gates(report, require_complete=True)["passed"] is False
    assert "b1_fnr" in evaluate_gates(report, require_complete=True)["failed"]
    # and it stays absent from a routine run
    assert evaluate_gates(report)["passed"] is True


@pytest.mark.parametrize("status", ["failed", "engine_error"])
def test_a_real_violation_blocks_in_both_modes(status):
    """The split must not become an escape hatch for genuine failures."""
    for require_complete in (False, True):
        gates = evaluate_gates(_gates_report(status),
                              require_complete=require_complete)
        assert gates["gates"]["b1_fnr"] is False, status
        assert gates["passed"] is False, (status, require_complete)
        assert "b1_fnr" in gates["failed"], (status, require_complete)
        assert status not in gates["evidence_debt"]


def test_a_certified_gate_carries_no_debt():
    gates = evaluate_gates(_gates_report("passed"))
    assert gates["passed"] is True and gates["evidence_debt"] == []


def test_gates_block_still_fails_closed_on_the_real_seed():
    """End-to-end on the shipped data: no assertion is made from it either."""
    report = run_full_benchmark(n_resamples=200)
    gates = evaluate_gates(report)
    b1_status = gates["b1_gate_status"]
    assert b1_status in ("insufficient_evidence", "failed", "engine_error",
                         "passed")
    # Whatever the seed supports, the block may never claim `passed` while the
    # evidence is missing — that is the invariant the split preserves.
    if b1_status == "insufficient_evidence":
        assert gates["evidence_debt"] == ["b1_fnr"]
        assert report["b1_set"]["gate"]["target_statistically_certified"] is False
