"""Regression: macro-F1 label handling.

The bug: ``macro_f1(..., labels=["m","f","n","needs_review"])`` averaged in a
class that can never be a true label.  Its per-class F1 is 0 by construction,
so an engine that was 58/58 correct on decidable cases was reported at 0.75.

These tests fail if a prediction-only class can re-enter the macro average
denominator anywhere in the benchmark.
"""

import pytest

from prokname.benchmark import evaluator
from prokname.benchmark.evaluator import A_TRUE_LABEL_DOMAIN, DECIDABLE_A_LABELS
from prokname.benchmark.metrics import (
    PREDICTION_ONLY_LABELS,
    macro_f1,
    per_class_f1,
)

GHOST = "needs_review"


# ---------------------------------------------------------------------------
# Unit level
# ---------------------------------------------------------------------------

def test_abstention_symbol_is_declared_prediction_only():
    assert GHOST in PREDICTION_ONLY_LABELS
    assert GHOST not in A_TRUE_LABEL_DOMAIN


def test_macro_f1_rejects_prediction_only_label():
    """The exact call pattern that produced the 0.75 must now raise."""
    y_true = ["m", "f", "n", "unknown"]
    y_pred = ["m", "f", "n", GHOST]
    with pytest.raises(ValueError, match="can never be a true label"):
        macro_f1(y_true, y_pred, ["m", "f", "n", GHOST])


def test_macro_f1_rejects_zero_support_class():
    with pytest.raises(ValueError, match="no case in the ground truth"):
        macro_f1(["m", "f"], ["m", "f"], ["m", "f", "n"])


def test_zero_support_escape_hatch_is_explicit():
    assert macro_f1(["m", "f"], ["m", "f"], ["m", "f", "n"], allow_zero_support=True) == 2 / 3


def test_phantom_class_deflates_the_average_that_is_the_bug():
    y_true = ["m", "f", "n"] * 4
    y_pred = ["m", "f", "n"] * 4
    correct = macro_f1(y_true, y_pred, DECIDABLE_A_LABELS)
    with_ghost = (correct * 3 + 0.0) / 4  # what averaging 4 classes does
    assert correct == 1.0
    assert with_ghost == pytest.approx(0.75)


def test_abstention_on_a_decidable_case_is_still_penalised():
    """Dropping the ghost class must not make refusing free."""
    y_true = ["m", "f", "n"]
    assert macro_f1(y_true, [GHOST, "f", "n"]) < macro_f1(y_true, y_true)
    per_class = per_class_f1(y_true, [GHOST, "f", "n"], DECIDABLE_A_LABELS)
    assert per_class["m"]["recall"] == 0.0
    assert per_class["m"]["f1"] == 0.0


def test_per_class_f1_default_domain_is_the_truth():
    rows = per_class_f1(["m", "unknown"], ["m", GHOST])
    assert set(rows) == {"m", "unknown"}
    assert GHOST not in rows


# ---------------------------------------------------------------------------
# Integration level: no source path may re-introduce the ghost label
# ---------------------------------------------------------------------------

def test_evaluator_source_has_no_abstention_label_in_a_macro_call():
    """Static guard: ``macro_f1(... "needs_review" ...)`` anywhere is a defect."""
    import inspect
    import re

    src = inspect.getsource(evaluator)
    hits = [
        line.strip()
        for line in src.splitlines()
        if GHOST in line and re.search(r"macro_f1|labels\s*=", line)
        and not line.strip().startswith("#")
    ]
    assert not hits, f"abstention symbol used as a macro-F1 label: {hits}"


def test_report_macro_f1_is_over_true_labels_only():
    """Every macro-F1 label list in the report ⊂ the true-label value domain."""
    report = evaluator.evaluate_a_set(n_resamples=200)
    ai = report["a_inference"]
    true_domain = set(ai["true_label_domain"])
    assert true_domain <= set(A_TRUE_LABEL_DOMAIN)
    for system in ("engine", "majority_class_baseline", "naive_ending_baseline"):
        block = ai[system]
        used = block["labels_used_for_macro_f1"]
        assert set(used["decidable"]) <= true_domain
        assert set(used["refusal_aware"]) == true_domain
        assert GHOST not in used["decidable"]
        assert GHOST not in used["refusal_aware"]
        assert set(used["decidable"]) <= set(DECIDABLE_A_LABELS)
        # the two macro-F1 quantities actually exist and carry intervals
        for key in ("decidable_subset", "refusal_aware_macro_f1"):
            metric = block[key]["macro_f1"] if key == "decidable_subset" else block[key]
            assert metric["interval"]["lower"] <= metric["value"] <= metric["interval"]["upper"]


def test_corrected_decidable_macro_f1_is_not_the_ghost_number():
    """The headline must not be 0.75-for-the-wrong-reason.

    It is allowed to be < 1.0 only if the engine actually mis-gendered a
    decidable case; the report's own per-class table is the evidence, so this
    test checks consistency rather than pinning a favourable value.
    """
    report = evaluator.evaluate_a_set(n_resamples=300)
    eng = report["a_inference"]["engine"]
    dec = eng["decidable_subset"]
    f1s = [row["f1"] for row in dec["per_class_f1"].values()]
    assert dec["macro_f1"]["value"] == pytest.approx(sum(f1s) / len(f1s), abs=5e-4)
    if all(f == 1.0 for f in f1s):
        assert dec["macro_f1"]["value"] == 1.0
    else:
        assert dec["macro_f1"]["value"] != 0.75 or len(f1s) == 3


def test_refusal_stays_visible_in_its_own_fields():
    report = evaluator.evaluate_a_set(n_resamples=200)
    eng = report["a_inference"]["engine"]
    assert eng["needs_review_rate"]["n"] > 0
    assert eng["needs_review_rate"]["interval"] is not None
    assert eng["abstention_evidence"]["insufficient_evidence"] is True
    assert eng["raw_true_domain_macro_f1"]["interval_method"] == (
        "not_applicable_diagnostic_only"
    )
