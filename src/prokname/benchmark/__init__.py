"""Benchmark framework for prokname.

Provides the evaluation infrastructure for the A/B/C/D test sets defined in
the benchmark design:

- A set: genus gender determination (lookup coverage + inference macro-F1 vs
  the two mandatory baselines)
- B set: agreement validation (B1) + generation exact-match (B2)
- C set: GAN coverage comparison — **protocol only** unless the user supplies
  a documented GAN command spec; see :mod:`prokname.benchmark.gan_compare`
- D set: dual-code routing correctness

Uncertainty intervals (this sentence replaced a false one — that finding
flagged that "all metrics include bootstrap 95% confidence intervals" was not
true of any macro-F1 number printed by the tool):

* every **proportion-style** metric (accuracy, miss rate, exact-match rate,
  coverage, needs_review rate, preemption accuracy) carries an **exact
  Clopper–Pearson binomial interval** plus the **one-sided upper bound** that
  a "rate <= target" claim must be judged against;
* **macro-F1** carries a case-level **paired bootstrap percentile interval**
  with a frozen label set and a fixed seed;
* **system comparisons** use a paired-difference bootstrap and an exact
  McNemar test, not disjointness of two independent intervals;
* every metric dict names its own ``interval_method`` and ``interval_label``,
  and the CLI prints those labels, so an interval can never be attached to
  the wrong metric.

Metrics computed on denominators too small to test their target are still
reported — with ``insufficient_evidence: true`` and the expansion needed —
rather than quietly passed.
"""

from .baselines import (
    engine_predictions,
    majority_class_baseline,
    majority_class_of,
    naive_ending_baseline,
)
from .evaluator import (
    DEFAULT_B1_TARGET_FNR,
    DEFAULT_MIN_ABSTENTION_CASES,
    DEFAULT_MIN_NEGATIVES_PER_BRANCH,
    evaluate_a_set,
    evaluate_b1_set,
    evaluate_b2_set,
    evaluate_c_set,
    evaluate_d_set,
    evaluate_gates,
    run_full_benchmark,
)
from .gan_compare import compliance_referee
from .gan_compare import evaluate_c_set as evaluate_gan_c_set
from .holdout import check_holdout, holdout_violations
from .metrics import (
    PREDICTION_ONLY_LABELS,
    accuracy,
    binomial_lower_bound,
    binomial_upper_bound,
    bootstrap_ci,
    clopper_pearson_interval,
    compare_systems_paired,
    confusion_matrix,
    exact_binomial_metric,
    false_negative_rate,
    macro_f1,
    macro_f1_interval,
    mcnemar_exact,
    min_n_for_zero_events,
    per_class_f1,
)
from .sets import (
    ATestCase,
    B1TestCase,
    B2TestCase,
    CTestCase,
    DTestCase,
    load_a_set,
    load_b1_set,
    load_b2_set,
    load_c_set,
    load_d_set,
)

__all__ = [
    # test case types
    "ATestCase",
    "B1TestCase",
    "B2TestCase",
    "CTestCase",
    "DTestCase",
    # loaders
    "load_a_set",
    "load_b1_set",
    "load_b2_set",
    "load_c_set",
    "load_d_set",
    # baselines
    "majority_class_baseline",
    "majority_class_of",
    "naive_ending_baseline",
    "engine_predictions",
    # metrics — labels
    "PREDICTION_ONLY_LABELS",
    "per_class_f1",
    # metrics — classification
    "accuracy",
    "confusion_matrix",
    "macro_f1",
    "macro_f1_interval",
    "false_negative_rate",
    # metrics — intervals & tests
    "clopper_pearson_interval",
    "binomial_upper_bound",
    "binomial_lower_bound",
    "min_n_for_zero_events",
    "exact_binomial_metric",
    "bootstrap_ci",
    "mcnemar_exact",
    "compare_systems_paired",
    # evaluators
    "evaluate_a_set",
    "evaluate_b1_set",
    "evaluate_b2_set",
    "evaluate_c_set",
    "evaluate_gan_c_set",
    "evaluate_d_set",
    "evaluate_gates",
    "run_full_benchmark",
    "compliance_referee",
    # gate tunables
    "DEFAULT_MIN_NEGATIVES_PER_BRANCH",
    "DEFAULT_B1_TARGET_FNR",
    "DEFAULT_MIN_ABSTENTION_CASES",
    # holdout
    "check_holdout",
    "holdout_violations",
]
