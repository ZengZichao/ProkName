"""Metric calculations with *honest* uncertainty intervals.

Design rules — every one of them
exists because a number in this package was once over-read):

1. **Exact intervals for proportions.**  Every metric that is a ratio of
   counted cases (accuracy, false-negative rate, exact-match rate,
   preemption accuracy, needs_review rate, coverage) gets an **exact
   Clopper–Pearson binomial interval**, computed in pure Python from the
   inverse regularised incomplete beta function.  A bootstrap percentile
   interval on a binomial proportion is asymptotically correct but can be
   empty/nonsense at small ``n`` and is never *better* than the exact one,
   so we do not use one there.

2. **Bootstrap only where the quantity really is a bootstrap quantity.**
   macro-F1 is a non-linear functional of the whole contingency table, so it
   has no closed-form interval; it gets a **case-level paired bootstrap**
   with a *frozen* label set (see :func:`macro_f1_interval`).

3. **Comparisons are paired.**  Engine and baselines are scored on the *same*
   cases, so significance comes from a paired-difference bootstrap and an
   exact McNemar test — never from "two independent intervals do not
   overlap" (that is a different, far more conservative test than the one this
   module implements).

4. **Every reported metric carries its interval *and* the name of the method
   that produced it**, so a reader cannot attach the wrong interval to the
   wrong metric, and the CLI prints the interval label verbatim.

5. **A metric that cannot be computed says so.**  ``false_negative_rate``
   returns ``None`` when the negative set is empty instead of the old
   ``0.0``, because ``0.0`` read as "perfect" and made a vacuous pass out of
   a gate that had nothing to test.

Dependency policy: the package declares exactly two runtime dependencies
(typer, rich), so the statistics below are implemented with :mod:`math` only.
``tests/test_metrics_intervals.py`` cross-checks the implementation against
``scipy.stats.beta`` **when scipy happens to be importable** and skips
otherwise; scipy is a dev-time verification aid, never an import of
prokname's own.

the benchmark design metric list:
- macro-F1 (avoids large-class dominance)
- accuracy
- 95% confidence intervals with an explicit, per-metric method label
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence

# ---------------------------------------------------------------------------
# Label domains
# ---------------------------------------------------------------------------

#: Symbols the *predictor* may emit to say "I refuse to decide".  They are not
#: part of any ground-truth value domain, so they must never enter the label
#: list of a macro average: a class that can never be a true label has
#: ``tp = fn = 0``, hence recall = 0 and F1 = 0 **by construction**, and it
#: silently deflates the macro average by widening its denominator.
PREDICTION_ONLY_LABELS: frozenset[str] = frozenset({"needs_review"})

#: Default confidence level for every interval reported by this module.
DEFAULT_CONFIDENCE = 0.95

#: Default resample count for the (few) genuine bootstrap quantities.
DEFAULT_BOOTSTRAP_RESAMPLES = 2000

#: Seed of the bootstrap resamples.  Fixed so that ``bench --full --json``
#: written to disk is byte-reproducible.
DEFAULT_BOOTSTRAP_SEED = 42


# ---------------------------------------------------------------------------
# Exact binomial (Clopper–Pearson) interval — pure Python
# ---------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz).

    Numerical Recipes §6.4; ``betacf`` is the piece that ``I_x(a,b)`` divides
    by ``B(a,b)``.
    """
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def _beta_log_norm(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """``I_x(a, b)`` — the CDF of a Beta(a, b) variable evaluated at x."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.log(x) * a + math.log1p(-x) * b - _beta_log_norm(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.log1p(-x) * b + math.log(x) * a - _beta_log_norm(a, b)
    ) * _betacf(b, a, 1.0 - x) / b


def beta_ppf(a: float, b: float, p: float) -> float:
    """Quantile function of Beta(a, b) by bisection on :func:`I_x(a,b)`.

    80 bisection steps bracket ``[0, 1]`` to ~1e-24, far below double
    precision, so the result is accurate to the last representable bit for
    the ``a, b >= 1`` regime that Clopper–Pearson needs.  Verified against
    ``scipy.stats.beta.ppf`` in ``tests/test_metrics_intervals.py``.
    """
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if regularized_incomplete_beta(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def clopper_pearson_interval(
    successes: int,
    n: int,
    confidence: float = DEFAULT_CONFIDENCE,
) -> tuple[float, float]:
    """Two-sided exact binomial (Clopper–Pearson) confidence interval.

    ``lower = B^{-1}(alpha/2; k, n-k+1)`` and
    ``upper = B^{-1}(1-alpha/2; k+1, n-k)``, with the usual ``k = 0`` /
    ``k = n`` degenerate branches.  Exact coverage, i.e. conservative — which
    is what we want when the point estimate is 0 or 1 on a handful of cases.
    """
    if n <= 0:
        raise ValueError(f"clopper_pearson_interval needs n > 0, got n={n}")
    if not 0 <= successes <= n:
        raise ValueError(f"need 0 <= successes <= n, got {successes} of {n}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    alpha = 1.0 - confidence
    lower = 0.0 if successes == 0 else beta_ppf(successes, n - successes + 1, alpha / 2)
    upper = 1.0 if successes == n else beta_ppf(successes + 1, n - successes, 1 - alpha / 2)
    return lower, upper


def binomial_upper_bound(
    events: int,
    n: int,
    confidence: float = DEFAULT_CONFIDENCE,
) -> float:
    """One-sided upper confidence bound for a binomial rate.

    This is the number that must be quoted next to a small-sample "≤ 1 %"
    claim: with 0 observed events in 4 negatives it is
    ``1 - 0.05**(1/4) ≈ 0.527``, i.e. the data are compatible with a false
    negative rate of up to ~53 %, not 1 %.
    """
    if n <= 0:
        raise ValueError(f"binomial_upper_bound needs n > 0, got n={n}")
    if not 0 <= events <= n:
        raise ValueError(f"need 0 <= events <= n, got {events} of {n}")
    if events == n:
        return 1.0
    return beta_ppf(events + 1, n - events, confidence)


def binomial_lower_bound(
    events: int,
    n: int,
    confidence: float = DEFAULT_CONFIDENCE,
) -> float:
    """One-sided lower confidence bound for a binomial rate.

    ``B^{-1}(1-confidence; k, n-k+1)`` — the mirror image of
    :func:`binomial_upper_bound`, used for "the system abstains at least this
    often"-style claims.
    """
    if n <= 0:
        raise ValueError(f"binomial_lower_bound needs n > 0, got n={n}")
    if not 0 <= events <= n:
        raise ValueError(f"need 0 <= events <= n, got {events} of {n}")
    if events == 0:
        return 0.0
    return beta_ppf(events, n - events + 1, 1.0 - confidence)


def min_n_for_zero_events(target: float, confidence: float = DEFAULT_CONFIDENCE) -> int:
    """Smallest ``n`` for which 0 observed events still certifies ``rate <= target``.

    Derivation: with ``k = 0`` the one-sided bound is ``1 - (1-confidence)**(1/n)``,
    so ``n >= log(1-confidence) / log(1-target)``.  For the B1 target of 1 %
    at 95 % confidence this is **299 negative cases** — the quantity of expert
    annotation the benchmark must grow by before "FNR ≤ 1 %" becomes a testable
    statement at all.
    """
    if not 0.0 < target < 1.0:
        raise ValueError(f"target must be in (0, 1), got {target}")
    n = math.log(1.0 - confidence) / math.log(1.0 - target)
    return max(1, math.ceil(n))


# ---------------------------------------------------------------------------
# Metric payloads: value + interval + interval method, always together
# ---------------------------------------------------------------------------

def exact_binomial_metric(
    events: int,
    n: int,
    *,
    name: str,
    event_meaning: str = "",
    confidence: float = DEFAULT_CONFIDENCE,
    min_n: int | None = None,
    note: str = "",
) -> dict:
    """A counted proportion with its exact Clopper–Pearson interval.

    Carries the two-sided interval *and* the one-sided bound, because the
    one-sided bound is the quantity a "rate ≤ target" claim must be judged
    against; printing only the point estimate is what let a 4-case set read
    as "≤1 % achieved".

    ``min_n``: the smallest denominator at which this metric can distinguish
    the behaviour it names from its degenerate alternative.  Below it the
    metric is still reported (honestly) but flagged
    ``insufficient_evidence: True``.
    """
    if n == 0:
        return {
            "name": name,
            "value": None,
            "events": int(events),
            "n": 0,
            "confidence_level": confidence,
            "interval": None,
            "interval_method": "not_computable_no_cases",
            "interval_label": "not computable (no cases in the denominator)",
            "insufficient_evidence": True,
            "note": note
            or "denominator is empty: this metric says nothing and must "
               "not be read as 0.0",
        }
    lower, upper = clopper_pearson_interval(events, n, confidence)
    one_sided_upper = binomial_upper_bound(events, n, confidence)
    metric: dict = {
        "name": name,
        "value": round(events / n, 4),
        "events": int(events),
        "n": int(n),
        "confidence_level": confidence,
        "interval": {"lower": round(lower, 4), "upper": round(upper, 4)},
        "interval_method": "clopper_pearson_exact_binomial",
        "interval_label": (
            f"exact Clopper–Pearson binomial CI on {events}/{n}, two-sided "
            f"{confidence:.0%} — not a bootstrap interval"
        ),
        "one_sided_upper_bound": round(one_sided_upper, 4),
        "one_sided_upper_bound_label": (
            f"one-sided {confidence:.0%} upper bound = {one_sided_upper:.4f} "
            "(judge a 'rate <= target' claim against this, not the point estimate)"
        ),
    }
    if min_n is not None:
        metric["min_denominator_for_evidence"] = min_n
        metric["insufficient_evidence"] = n < min_n
        if n < min_n:
            metric["insufficient_evidence_note"] = (
                f"{name}: only {n} case(s) in the denominator, below the "
                f"{min_n} needed to tell the intended behaviour apart from "
                "the degenerate one; the interval is wide and the metric "
                "must not be quoted as a performance claim."
            )
    if event_meaning:
        metric["event_meaning"] = event_meaning
    if note:
        metric["note"] = note
    return metric


def bootstrap_metric(
    point: float,
    stat: Callable[[Sequence[int]], float],
    n: int,
    *,
    name: str,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    note: str = "",
) -> dict:
    """A non-proportion metric with a genuine case-level bootstrap interval.

    ``stat(indices)`` recomputes the metric on a resample of the ``n`` cases,
    so the interval propagates case-level dependence (this is how macro-F1
    gets a CI — by resampling *cases*, not by averaging per-case 0/1 values,
    which is the mistake this guards against.
    """
    if n <= 0:
        return {
            "name": name,
            "value": round(point, 4),
            "n": 0,
            "confidence_level": confidence,
            "interval": None,
            "interval_method": "not_computable_no_cases",
            "interval_label": "not computable (no cases)",
            "note": note,
        }
    rng = random.Random(seed)
    reps = sorted(
        stat([rng.randrange(n) for _ in range(n)]) for _ in range(n_resamples)
    )
    alpha = 1.0 - confidence
    lower = reps[min(int(math.floor(alpha / 2 * n_resamples)), n_resamples - 1)]
    upper = reps[min(int(math.ceil((1 - alpha / 2) * n_resamples)) - 1, n_resamples - 1)]
    interval_label = (
        f"bootstrap 95% percentile CI ({n_resamples} case-level resamples) "
        f"— this interval belongs to {name!r} only"
    )
    return {
        "name": name,
        "value": round(point, 4),
        "n": int(n),
        "n_resamples": n_resamples,
        "confidence_level": confidence,
        "interval": {"lower": round(lower, 4), "upper": round(upper, 4)},
        "interval_method": "case_level_bootstrap_percentile",
        "interval_label": interval_label,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Classification metrics
# ---------------------------------------------------------------------------

def _check_labels_are_true_labels(
    labels: Sequence[str],
    y_true: Sequence[str],
    *,
    allow_zero_support: bool = False,
) -> None:
    """Refuse a label that cannot be a true label."""
    true_domain = set(y_true)
    abstention_offenders = [lab for lab in labels if lab in PREDICTION_ONLY_LABELS]
    if abstention_offenders:
        raise ValueError(
            "macro-F1 label list contains prediction-only/abstention "
            f"class(es) {sorted(set(abstention_offenders))}, which can never "
            "be a true label. Such a class has F1 = 0 by construction and "
            "deflates the macro average for reasons unrelated to model "
            "quality (that finding — this is the bug that reported 0.75 "
            "for an engine that was 58/58 correct). Score abstention with "
            "refusal_aware_* / needs_review_rate instead."
        )
    if not allow_zero_support:
        zero = [lab for lab in labels if lab not in true_domain]
        if zero:
            raise ValueError(
                f"macro-F1 label(s) {sorted(zero)} have no case in the ground "
                f"truth of this sample (true labels: {sorted(true_domain)}). "
                "Averaging a class with no support lowers the macro average "
                "for a reason unrelated to model quality. Pass "
                "allow_zero_support=True if the label set is intentionally "
                "frozen (e.g. inside a bootstrap resample)."
            )


def per_class_f1(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    labels: Sequence[str] | None = None,
    *,
    allow_zero_support: bool = False,
) -> dict[str, dict[str, float]]:
    """Per-class tp/fp/fn/support/precision/recall/f1 (full disclosure).

    ``labels`` defaults to the **true-label value domain** — never the union
    with the predictions.
    """
    if labels is None:
        labels = sorted(set(y_true))
    else:
        _check_labels_are_true_labels(
            labels, y_true, allow_zero_support=allow_zero_support
        )
    out: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        out[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "support": tp + fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    return out


def _macro_f1_fast(
    matrix: list[list[int]],
    labels: list[str],
) -> float:
    """Macro-F1 straight from a square contingency matrix."""
    f1s = []
    for i, _lab in enumerate(labels):
        tp = matrix[i][i]
        row = sum(matrix[i])          # support (true positives + misses)
        col = sum(r[i] for r in matrix)
        fp = col - tp
        fn = row - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1s.append(
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
    return sum(f1s) / len(f1s) if f1s else 0.0


def macro_f1(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    labels: Sequence[str] | None = None,
    *,
    allow_zero_support: bool = False,
) -> float:
    """Macro-averaged F1: unweighted mean of per-class F1 over the *true* labels.

    ``labels=None`` ⇒ the true-label value domain (classes present in
    ``y_true``).  Classes that only ever appear as *predictions* are rejected
    (see :func:`_check_labels_are_true_labels`): they can never be a true
    label, their F1 is identically 0, and averaging them in was the bug
    that reported 0.75 for an engine that was 58/58 correct on decidable
    cases.  An abstention on a decidable case is still fully penalised — it
    shows up as a false negative for the true class, lowering that class's
    recall.
    """
    if not y_true:
        return 0.0
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"length mismatch: y_true={len(y_true)}, y_pred={len(y_pred)}"
        )
    if labels is None:
        labels = sorted(set(y_true))
    else:
        _check_labels_are_true_labels(
            labels, y_true, allow_zero_support=allow_zero_support
        )
    index = {lab: i for i, lab in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for t, p in zip(y_true, y_pred):
        ti = index.get(t)
        pi = index.get(p)
        if ti is not None and pi is not None:
            matrix[ti][pi] += 1
    return _macro_f1_fast(matrix, list(labels))


def macro_f1_interval(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    *,
    name: str = "macro_f1",
    labels: Sequence[str] | None = None,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    note: str = "",
) -> dict:
    """macro-F1 with a case-level paired bootstrap interval.

    The label set is frozen to the full-sample true-label domain before
    resampling so that every replicate averages over the same classes;
    otherwise a resample that happens to miss a rare class would silently
    divide by a different denominator.
    """
    if labels is None:
        labels = sorted(set(y_true))
    else:
        _check_labels_are_true_labels(labels, y_true)
    point = macro_f1(y_true, y_pred, labels)
    yt, yp = list(y_true), list(y_pred)

    def stat(indices: Sequence[int]) -> float:
        idx = list(indices)
        matrix = [[0] * len(labels) for _ in labels]
        index = {lab: i for i, lab in enumerate(labels)}
        for i in idx:
            ti = index.get(yt[i])
            pi = index.get(yp[i])
            if ti is not None and pi is not None:
                matrix[ti][pi] += 1
        return _macro_f1_fast(matrix, list(labels))

    return bootstrap_metric(
        point, stat, len(yt),
        name=name, n_resamples=n_resamples, confidence=confidence, seed=seed,
        note=note
        or f"bootstrap of macro-F1 over classes {sorted(labels)}; "
           "NOT an accuracy interval",
    )


def confusion_matrix(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    labels: Sequence[str] | None = None,
    *,
    true_labels: Sequence[str] | None = None,
    pred_labels: Sequence[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Build a confusion matrix as nested dicts: ``matrix[true][pred]``.

    ``labels`` is the minimum label set for the square form; any label
    observed in the data but missing from it is added, so no (true, pred) pair
    is silently dropped.

    ``true_labels``/``pred_labels`` request the **rectangular** form instead,
    which is what the A-set report uses: rows are the ground-truth value
    domain, columns additionally carry the abstention symbol, so "the engine
    refused on the only undecidable case" stays visible instead of becoming a
    phantom row with no support.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"length mismatch: y_true={len(y_true)}, y_pred={len(y_pred)}"
        )
    observed_true = set(y_true)
    observed_pred = set(y_pred)
    if true_labels is not None or pred_labels is not None:
        rows = sorted(observed_true | set(true_labels or ()))
        cols = sorted(observed_pred | set(pred_labels or ()))
    else:
        all_labels = sorted(observed_true | observed_pred | set(labels or ()))
        rows = cols = all_labels
    matrix = {t: {p: 0 for p in cols} for t in rows}
    for t, p in zip(y_true, y_pred):
        if t in matrix and p in matrix[t]:
            matrix[t][p] += 1
    return matrix


def accuracy(y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    """Simple accuracy = correct / total."""
    if not y_true:
        return 0.0
    correct = sum(t == p for t, p in zip(y_true, y_pred))
    return correct / len(y_true)


def accuracy_events(y_true: Sequence[str], y_pred: Sequence[str]) -> int:
    """Number of correctly scored cases (the numerator of ``accuracy``)."""
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"length mismatch: y_true={len(y_true)}, y_pred={len(y_pred)}"
        )
    return sum(1 for t, p in zip(y_true, y_pred) if t == p)


def bootstrap_ci(
    values: Sequence[float],
    n_resamples: int = 1000,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> tuple[float, float, float]:
    """Bootstrap CI for the **mean of a per-case vector**.

    Kept for API compatibility, and honestly scoped: the mean of a 0/1
    correctness vector *is* an accuracy, so the interval this returns is an
    accuracy interval and nothing else.  Proportion metrics now go through
    :func:`exact_binomial_metric` and macro-F1 through
    :func:`macro_f1_interval`; do not use this to dress up a non-mean metric
    (that is exactly what the label audit flagged).
    """
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    n = len(values)
    point = sum(values) / n
    boot_means = sorted(
        sum(values[rng.randint(0, n - 1)] for _ in range(n)) / n
        for _ in range(n_resamples)
    )
    alpha = 1.0 - confidence
    lower_idx = int(alpha / 2 * n_resamples)
    upper_idx = min(int((1 - alpha / 2) * n_resamples), n_resamples - 1)
    return point, boot_means[lower_idx], boot_means[upper_idx]


# ---------------------------------------------------------------------------
# Miss rate (B1) — abstention can no longer satisfy it
# ---------------------------------------------------------------------------

def false_negative_rate(
    y_true: Sequence[bool],
    y_pred: Sequence[bool | None],
    *,
    abstention_counts_as_miss: bool = True,
) -> float | None:
    """Proportion of true violations that the system failed to flag.

    ``y_true[i] is True`` means "this name IS a violation (negative case)";
    ``y_pred[i]`` is ``True`` (violation flagged), ``False`` (judged
    compliant) or ``None`` (the engine refused to decide).

    Two changes relative to the first version, both from that finding:

    * **An empty negative set returns ``None``, not ``0.0``.**  ``0.0`` was
      read as "0 % miss rate, gate passed" on branches that contain no
      negative case at all — a vacuous pass.
    * **Abstention counts as a miss** by default.  The previous evaluator
      mapped ``compliant is None`` to "predicted violation", which made the
      headline miss rate reachable by refusing to judge anything (all-abstain
      scored a perfect 0.0).  Pass ``abstention_counts_as_miss=False`` for the
      complementary "rate among verdicts actually issued" view; that view is
      reported *with* its verdict coverage so it cannot be over-read either.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"length mismatch: y_true={len(y_true)}, y_pred={len(y_pred)}"
        )
    violations = sum(1 for t in y_true if t)
    if violations == 0:
        return None
    false_neg = 0
    for t, p in zip(y_true, y_pred):
        if not t:
            continue
        if p is None:
            if abstention_counts_as_miss:
                false_neg += 1
            continue
        if not p:
            false_neg += 1
    return false_neg / violations


# ---------------------------------------------------------------------------
# Paired system comparison
# ---------------------------------------------------------------------------

def mcnemar_exact(
    correct_a: Sequence[float],
    correct_b: Sequence[float],
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict:
    """Exact two-sided McNemar test on two paired 0/1 correctness vectors.

    ``b`` = cases A gets right and B wrong, ``c`` = the reverse.  Under the
    null the discordant pairs are fair coin flips, so the p-value is exact
    (no chi-square approximation), computed with integer binomials.
    """
    if len(correct_a) != len(correct_b):
        raise ValueError("vectors must be the same length")
    b = sum(1 for a, c in zip(correct_a, correct_b) if a > c)
    c = sum(1 for a, c in zip(correct_a, correct_b) if a < c)
    n_disc = b + c
    if n_disc == 0:
        return {
            "test": "mcnemar_exact",
            "b_a_only_correct": b,
            "c_b_only_correct": c,
            "n_discordant": 0,
            "p_value": 1.0,
            "note": "no discordant pairs: the two systems make identical "
                    "decisions on these cases, so no difference is testable",
        }
    k = min(b, c)
    tail = sum(math.comb(n_disc, i) for i in range(k + 1)) / 2.0 ** n_disc
    p = min(1.0, 2.0 * tail)
    return {
        "test": "mcnemar_exact",
        "b_a_only_correct": b,
        "c_b_only_correct": c,
        "n_discordant": n_disc,
        # 12 decimals, not 6: a small exact p-value (2*0.5**10 = 0.001953125)
        # must survive the data layer intact — rounding here used to destroy
        # the last digits of the very quantity the report quotes.
        "p_value": round(p, 12),
        "note": "exact binomial test on the discordant pairs of two systems "
                "scored on the SAME cases (paired)",
    }


def paired_bootstrap_difference(
    stat_a: Callable[[Sequence[int]], float],
    stat_b: Callable[[Sequence[int]], float],
    n: int,
    *,
    name: str,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict:
    """Paired-difference bootstrap: CI of ``stat_a - stat_b`` + one-sided p.

    Resampling the *case indices* and evaluating both systems on each
    replicate keeps the pairing, which is what the earlier
    "two independent intervals do not overlap" rule threw away.
    """
    if n <= 0:
        return {
            "name": name,
            "difference": None,
            "interval": None,
            "interval_method": "not_computable_no_cases",
        }
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        diffs.append(stat_a(idx) - stat_b(idx))
    ordered = sorted(diffs)
    alpha = 1.0 - confidence
    lower = ordered[min(int(math.floor(alpha / 2 * n_resamples)), n_resamples - 1)]
    upper = ordered[
        min(int(math.ceil((1 - alpha / 2) * n_resamples)) - 1, n_resamples - 1)
    ]
    # one-sided bootstrap p for H0: A <= B (smaller = A really is ahead)
    le_zero = sum(1 for d in diffs if d <= 0)
    p_value = (le_zero + 1) / (n_resamples + 1)
    return {
        "name": name,
        "difference_interval": {"lower": round(lower, 4), "upper": round(upper, 4)},
        "n": int(n),
        "n_resamples": n_resamples,
        "confidence_level": confidence,
        "interval_method": "paired_case_level_bootstrap",
        "one_sided_p_a_gt_b": round(p_value, 6),
        "note": "A minus B on each of the resampled case sets; a CI entirely "
                "above 0 means A beats B on this metric at the stated level.",
    }


def compare_systems_paired(
    y_true: Sequence[str],
    preds_a: Sequence[str],
    preds_b: Sequence[str],
    *,
    labels: Sequence[str] | None = None,
    name_a: str = "A",
    name_b: str = "B",
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict:
    """Paired accuracy + macro-F1 comparison of two systems on one case set."""
    if labels is None:
        labels = sorted(set(y_true))
    else:
        _check_labels_are_true_labels(labels, y_true)
    yt, pa, pb = list(y_true), list(preds_a), list(preds_b)
    n = len(yt)

    def acc_stat(preds):
        def stat(indices: Sequence[int]) -> float:
            idx = list(indices)
            return sum(1 for i in idx if preds[i] == yt[i]) / len(idx)
        return stat

    def f1_stat(preds):
        def stat(indices: Sequence[int]) -> float:
            idx = list(indices)
            # allow_zero_support: the label set is deliberately frozen across
            # resamples so replicates stay comparable; a rare class can be
            # absent from a given resample's ground truth without that being
            # the phantom-row pathology.
            return macro_f1(
                [yt[i] for i in idx], [preds[i] for i in idx], labels,
                allow_zero_support=True,
            )
        return stat

    acc_a = accuracy(yt, pa)
    acc_b = accuracy(yt, pb)
    f1_a = macro_f1(yt, pa, labels)
    f1_b = macro_f1(yt, pb, labels)
    return {
        "pairing": "same cases, paired resampling",
        f"{name_a}_vs_{name_b}_accuracy": {
            **paired_bootstrap_difference(
                acc_stat(pa), acc_stat(pb), n,
                name=f"accuracy({name_a}) - accuracy({name_b})",
                n_resamples=n_resamples, confidence=confidence, seed=seed,
            ),
            "point_difference": round(acc_a - acc_b, 4),
            "mcnemar_exact": mcnemar_exact(
                [1.0 if a == t else 0.0 for t, a in zip(yt, pa)],
                [1.0 if b == t else 0.0 for t, b in zip(yt, pb)],
                confidence,
            ),
        },
        f"{name_a}_vs_{name_b}_macro_f1": {
            **paired_bootstrap_difference(
                f1_stat(pa), f1_stat(pb), n,
                name=f"macro_f1({name_a}) - macro_f1({name_b})",
                n_resamples=n_resamples, confidence=confidence, seed=seed,
            ),
            "point_difference": round(f1_a - f1_b, 4),
        },
    }
