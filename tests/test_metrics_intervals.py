"""Regression: interval integrity — which interval belongs to which metric.

The bug: ``benchmark/__init__`` claimed "all metrics include bootstrap 95%
confidence intervals", but ``bootstrap_ci`` was fed the per-case 0/1
correctness vector, so every printed interval was an *accuracy* interval and
macro-F1 had none at all.  The CLI then printed
``macro-F1=0.75 acc=0.98 CI=[...]`` side by side.

These tests pin: exact Clopper–Pearson for proportions (cross-checked against
``scipy.stats.beta`` when scipy is importable — it is a *verification aid*, not
a runtime dependency), a genuine bootstrap for macro-F1, and paired tests for
system comparison.
"""

import math

import pytest

from prokname.benchmark import evaluator
from prokname.benchmark.metrics import (
    beta_ppf,
    binomial_lower_bound,
    binomial_upper_bound,
    bootstrap_metric,
    clopper_pearson_interval,
    compare_systems_paired,
    exact_binomial_metric,
    macro_f1_interval,
    mcnemar_exact,
    min_n_for_zero_events,
    regularized_incomplete_beta,
)

# ---------------------------------------------------------------------------
# Beta machinery (pure Python — must match scipy)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,x", [
    (1, 1, 0.3), (1, 4, 0.5), (2, 3, 0.75), (5, 5, 0.5), (12, 47, 0.2),
    (3, 1, 0.9), (1, 59, 0.01), (30, 30, 0.5),
])
def test_incomplete_beta_matches_scipy(a, b, x):
    scipy_stats = pytest.importorskip("scipy.stats")
    assert regularized_incomplete_beta(a, b, x) == pytest.approx(
        float(scipy_stats.beta.cdf(x, a, b)), abs=1e-12
    )


@pytest.mark.parametrize("a,b", [(1, 1), (1, 4), (2, 3), (9, 50), (25, 34)])
def test_beta_ppf_matches_scipy(a, b):
    scipy_stats = pytest.importorskip("scipy.stats")
    for p in (0.025, 0.05, 0.5, 0.95, 0.975):
        assert beta_ppf(a, b, p) == pytest.approx(
            float(scipy_stats.beta.ppf(p, a, b)), abs=1e-10
        )


@pytest.mark.parametrize("n", [1, 2, 4, 5, 17, 26, 59, 100, 299])
def test_clopper_pearson_matches_scipy_everywhere(n):
    scipy_stats = pytest.importorskip("scipy.stats")
    for k in range(n + 1):
        lo, hi = clopper_pearson_interval(k, n)
        exp_lo = 0.0 if k == 0 else float(scipy_stats.beta.ppf(0.025, k, n - k + 1))
        exp_hi = 1.0 if k == n else float(scipy_stats.beta.ppf(0.975, k + 1, n - k))
        assert lo == pytest.approx(exp_lo, abs=1e-10)
        assert hi == pytest.approx(exp_hi, abs=1e-10)


def test_clopper_pearson_has_nominal_coverage_by_construction():
    """Exactness check: the interval inverts the binomial tail, so the
    endpoints reproduce alpha/2 mass on each side."""
    for n, k in [(4, 0), (17, 17), (59, 58), (26, 21)]:
        lo, hi = clopper_pearson_interval(k, n)
        if k > 0:
            assert sum(
                math.comb(n, i) * lo ** i * (1 - lo) ** (n - i) for i in range(k, n + 1)
            ) == pytest.approx(0.025, abs=1e-6)
        if k < n:
            assert sum(
                math.comb(n, i) * hi ** i * (1 - hi) ** (n - i) for i in range(0, k + 1)
            ) == pytest.approx(0.025, abs=1e-6)


# ---------------------------------------------------------------------------
# One-sided bounds and the "how many cases would we need" arithmetic
# ---------------------------------------------------------------------------

def test_zero_of_four_upper_bound_is_the_number_the_review_computed():
    # 0/4 negatives cannot support a ≤1% claim — the bound is ≈0.527.
    assert binomial_upper_bound(0, 4) == pytest.approx(1 - 0.05 ** 0.25, abs=1e-10)
    assert binomial_upper_bound(0, 4) == pytest.approx(0.5271, abs=1e-3)


def test_one_and_two_sided_bounds_relationship():
    # the one-sided 95 % upper equals the two-sided 90 % upper (same alpha split)
    for n, k in [(4, 0), (10, 3), (59, 58)]:
        two = clopper_pearson_interval(k, n, confidence=0.90)[1]
        assert binomial_upper_bound(k, n, confidence=0.95) == pytest.approx(two, abs=1e-10)
        assert binomial_lower_bound(k, n) <= binomial_upper_bound(k, n)


def test_perfect_run_still_has_a_useless_upper_bound():
    assert binomial_upper_bound(17, 17) == 1.0
    assert clopper_pearson_interval(17, 17)[1] == 1.0
    assert clopper_pearson_interval(17, 17)[0] == pytest.approx(0.8049, abs=1e-3)


def test_min_n_for_the_one_percent_target():
    assert min_n_for_zero_events(0.01) == 299
    assert min_n_for_zero_events(0.05) == 59
    assert min_n_for_zero_events(0.10) == 29
    # monotone: a stricter target needs more data
    assert min_n_for_zero_events(0.001) > min_n_for_zero_events(0.01)


def test_interval_rejects_bad_inputs():
    with pytest.raises(ValueError):
        clopper_pearson_interval(3, 2)
    with pytest.raises(ValueError):
        clopper_pearson_interval(1, 0)
    with pytest.raises(ValueError):
        binomial_upper_bound(-1, 5)


# ---------------------------------------------------------------------------
# Metric payloads
# ---------------------------------------------------------------------------

def test_exact_binomial_metric_labels_itself():
    m = exact_binomial_metric(0, 4, name="fnr")
    assert m["interval_method"] == "clopper_pearson_exact_binomial"
    assert "Clopper" in m["interval_label"]
    assert m["one_sided_upper_bound"] == pytest.approx(0.5271, abs=1e-3)
    assert m["value"] == 0.0


def test_exact_binomial_metric_with_empty_denominator_is_not_zero():
    m = exact_binomial_metric(0, 0, name="fnr_feature_branch")
    assert m["value"] is None
    assert m["interval"] is None
    assert m["insufficient_evidence"] is True
    assert "must not be read as 0.0" in m["note"]


def test_min_n_flag_marks_small_samples():
    assert exact_binomial_metric(1, 59, name="x", min_n=20)["insufficient_evidence"] is False
    assert exact_binomial_metric(1, 1, name="x", min_n=20)["insufficient_evidence"] is True


def test_bootstrap_metric_is_a_percentile_interval_over_a_statistic():
    calls = []

    def stat(idx):
        calls.append(len(idx))
        return sum(idx) / len(idx)

    m = bootstrap_metric(0.6, stat, 10, name="custom", n_resamples=250, seed=7)
    assert m["interval_method"] == "case_level_bootstrap_percentile"
    assert m["n_resamples"] == 250
    assert len(calls) == 250
    assert "custom" in m["interval_label"]


# ---------------------------------------------------------------------------
# macro-F1 bootstrap interval
# ---------------------------------------------------------------------------

def test_macro_f1_interval_exists_and_brackets_the_point():
    y_true = ["m", "f", "n", "m", "f", "n", "m", "f", "n", "m"]
    y_pred = ["m", "f", "n", "m", "m", "n", "m", "f", "n", "f"]
    m = macro_f1_interval(y_true, y_pred, n_resamples=500, seed=1)
    assert m["interval_method"] == "case_level_bootstrap_percentile"
    assert m["interval"]["lower"] <= m["value"] <= m["interval"]["upper"]
    assert "NOT the accuracy interval" not in m["interval_label"]  # label is honest
    assert "macro_f1" in m["name"]


def test_macro_f1_interval_is_seed_reproducible():
    y_true = ["m", "f", "n"] * 8
    y_pred = ["m", "f", "n", "m", "f", "n"] * 4
    a = macro_f1_interval(y_true, y_pred, n_resamples=400, seed=99)
    b = macro_f1_interval(y_true, y_pred, n_resamples=400, seed=99)
    assert a["interval"] == b["interval"]


def test_perfect_predictions_have_degenerate_but_correct_interval():
    y = ["m", "f", "n"] * 10
    m = macro_f1_interval(y, y, n_resamples=200)
    assert m["value"] == 1.0
    assert m["interval"] == {"lower": 1.0, "upper": 1.0}


# ---------------------------------------------------------------------------
# Paired comparison (replaces the "intervals don't overlap" rule)
# ---------------------------------------------------------------------------

def test_mcnemar_exact_known_values():
    # 1 discordant pair: p = 2 * P(X<=0 | n=1) = 1.0
    assert mcnemar_exact([1, 0, 0], [0, 0, 0])["p_value"] == 1.0
    res = mcnemar_exact([1] * 10 + [0] * 2, [0] * 10 + [0] * 2)
    assert res["b_a_only_correct"] == 10 and res["c_b_only_correct"] == 0
    assert res["p_value"] == pytest.approx(2 * 0.5 ** 10, abs=1e-9)
    empty = mcnemar_exact([1, 1], [1, 1])
    assert empty["n_discordant"] == 0 and empty["p_value"] == 1.0


def test_mcnemar_against_scipy_binomial():
    scipy_stats = pytest.importorskip("scipy.stats")
    a = [1, 1, 1, 1, 1, 0, 0, 1, 1, 0]
    b = [0, 0, 0, 0, 1, 1, 1, 1, 0, 0]
    res = mcnemar_exact(a, b)
    n_disc = res["n_discordant"]
    k = min(res["b_a_only_correct"], res["c_b_only_correct"])
    expected = min(
        1.0, 2 * float(scipy_stats.binom.cdf(k, n_disc, 0.5))
    )
    assert res["p_value"] == pytest.approx(expected, abs=1e-9)


def test_paired_comparison_reports_paired_quantities():
    y_true = ["m", "f", "n"] * 12
    good = list(y_true)
    bad = ["m"] * len(y_true)
    # name the systems explicitly: the helper's default names are "A"/"B", and the
    # keyed output is derived from them
    out = compare_systems_paired(y_true, good, bad, name_a="good", name_b="bad",
                                 n_resamples=300, seed=3)
    key = "good_vs_bad_accuracy"
    # the helper rounds reported differences to 4 dp on purpose, so the tolerance
    # must be wider than that quantisation (1e-6 compared against 0.6667 can never pass)
    assert out[key]["point_difference"] == pytest.approx(1 - 1 / 3, abs=1e-4)
    assert out[key]["interval_method"] == "paired_case_level_bootstrap"
    assert out[key]["difference_interval"]["lower"] > 0
    assert out[key]["mcnemar_exact"]["c_b_only_correct"] == 0
    assert out["good_vs_bad_macro_f1"]["difference_interval"]["lower"] > 0
    assert out["pairing"] == "same cases, paired resampling"


# ---------------------------------------------------------------------------
# Integration: the report wires the right interval to the right metric
# ---------------------------------------------------------------------------

def test_report_accuracy_and_macro_f1_intervals_are_not_the_same_object():
    ai = evaluator.evaluate_a_set(n_resamples=400)["a_inference"]
    eng = ai["engine"]
    acc = eng["accuracy"]
    f1 = eng["decidable_subset"]["macro_f1"]
    assert acc["interval_method"] != f1["interval_method"]
    assert acc["n"] == eng["n_cases"] == ai["engine"]["n_cases"]
    assert f1["n"] == eng["decidable_subset"]["n_cases"]


def test_report_no_longer_prints_an_unpaired_disjointness_claim():
    ai = evaluator.evaluate_a_set(n_resamples=200)["a_inference"]
    assert "significant_vs_baselines" not in ai
    sig = ai["significance_vs_baselines"]
    for block in sig.values():
        for metric in ("accuracy", "macro_f1"):
            key = next(k for k in block if k.endswith(f"_{metric}"))
            assert block[key]["interval_method"] == "paired_case_level_bootstrap"
        assert "mcnemar_exact" in block[next(k for k in block if k.endswith("_accuracy"))]


def test_every_proportion_in_the_report_is_exact_not_bootstrap():
    """The confusion can only recur if a proportion gets a bootstrap CI."""
    report = {
        "a": evaluator.evaluate_a_set(n_resamples=200),
        "b1": evaluator.evaluate_b1_set(),
        "b2": evaluator.evaluate_b2_set(),
        "d": evaluator.evaluate_d_set(),
    }
    bootstrap_ok = {"macro_f1"}

    def walk(node, path=""):
        if isinstance(node, dict):
            method = node.get("interval_method")
            if method == "case_level_bootstrap_percentile":
                name = node.get("name", path)
                assert any(tag in name for tag in bootstrap_ok), f"{path}: {name}"
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(report)
