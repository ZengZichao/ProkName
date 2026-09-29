"""Baseline predictors for gender determination.

Two mandatory baselines:

1. Majority-class baseline: predict the plurality gender class for everything.
   The plurality is **computed from a training split**, not hardcoded.
2. Naive ending-rule baseline: -us→m, -a→f, -um→n (no exception table).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from .sets import ATestCase


def majority_class_of(
    cases: Sequence[ATestCase],
    *,
    exclude_unknown: bool = True,
) -> str:
    """Plurality ground-truth class of ``cases`` (the training split).

    Deterministic on ties: the alphabetically first class of the tied set
    wins, so a change in case *order* can never silently flip the baseline.

    ``exclude_unknown`` drops the ``unknown`` label from the race: it is not a
    gender, so a "majority-gender baseline" that predicts ``unknown`` would be
    answering a different question than the A-set asks.
    """
    counts = Counter(
        c.label for c in cases if not (exclude_unknown and c.label == "unknown")
    )
    if not counts:
        # Degenerate input: fall back to the documented prior rather than
        # inventing a class; the caller's report records what was computed.
        return "m"
    best = max(counts.values())
    return sorted(lab for lab, n in counts.items() if n == best)[0]


def majority_class_baseline(
    cases: Sequence[ATestCase],
    train_cases: Sequence[ATestCase] | None = None,
) -> list[str]:
    """Predict the plurality class of the training split for every genus.

    The class used to be the literal ``"m"``.  It is now computed, so the
    name "majority-class baseline" cannot drift away from the implementation
    if the set changes (``tests/test_benchmark.py`` pins the computed value to
    the documented class ``m`` for the shipped A-inference split).

    Label-prior leakage — read this before quoting the comparison:
    when ``train_cases`` is omitted, the plurality is computed from the very
    cases being predicted, because this benchmark has no held-out training
    split.  That is a mild form of test-set leakage and it makes the baseline
    *stronger* (it is allowed to know the test label distribution), never
    weaker — so it does not inflate the engine's margin.  Supply
    ``train_cases`` (e.g. a lexicon-backed split) to remove the leak; note
    that the published lexicon's own plurality is ``f`` (23/58 in
    ``genus_gender.json``), i.e. a leakage-free majority baseline would be
    *harder* for the engine to beat on macro-F1 but easier on accuracy.
    """
    train = cases if train_cases is None else train_cases
    return [majority_class_of(train)] * len(cases)


def naive_ending_baseline(cases: Sequence[ATestCase]) -> list[str]:
    """Naive word-ending rules: -us→m, -a→f, -um→n, no exceptions.

    This is the '三条规则' baseline from the benchmark design.  It deliberately
    has NO exception table — it will get Greek -ma neuters wrong (predicting
    'f' from -a) and -us feminine genera wrong.  It abstains
    (``needs_review``) on endings it has no rule for, which keeps the
    comparison on like-for-like terms with the engine: both sides are allowed
    to refuse.
    """
    predictions = []
    for case in cases:
        genus = case.genus.lower()
        if genus.endswith("um"):
            predictions.append("n")
        elif genus.endswith("a"):
            predictions.append("f")
        elif genus.endswith("us"):
            predictions.append("m")
        else:
            predictions.append("needs_review")
    return predictions


def engine_predictions(cases: Sequence[ATestCase]) -> list[str]:
    """Get the engine's inference-mode predictions for a set of genera."""
    return [pred for pred, _err in engine_predictions_with_errors(cases)]


def engine_predictions_with_errors(
    cases: Sequence[ATestCase],
) -> list[tuple[str, str | None]]:
    """Engine inference-mode predictions, plus the error text where it crashed.

    Uses only the inference path (gender_endings.json heuristics), NOT the
    lookup lexicon — because the A-inference set is disjoint from the lexicon
    (holdout protocol).

    Whatever the engine returns today is what gets scored: ``needs_review`` is
    emitted whenever the engine refuses (``gender is None``), so the benchmark
    never bakes in an expectation about the ending tables.  An engine
    *exception* is also recorded as a refusal, but the error text travels
    alongside it so a crash can never be mistaken for a legitimate abstention
    (it is reported as ``engine_error_cases`` and fails the gates).
    """
    from prokname.engine.gender import gender_of

    out: list[tuple[str, str | None]] = []
    for case in cases:
        try:
            gr = gender_of(case.genus)
        except Exception as exc:  # noqa: BLE001 — never mask a crash silently
            out.append(("needs_review", f"{type(exc).__name__}: {exc}"))
            continue
        if gr.gender is not None:
            out.append((gr.gender.value, None))
        else:
            out.append(("needs_review", None))
    return out
