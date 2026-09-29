"""Benchmark evaluator: run A/B/C/D evaluations and produce results.json.

This module ties together the test sets, baselines, engine, and metrics to
produce the report structure described in the benchmark design.

Statistical integrity rules enforced here (each one is a fix for a reviewed
mis-read of a number):

* **** — macro-F1 is always averaged over the *true-label value domain*.
  ``needs_review`` is a prediction-side abstention symbol, never a ground
  truth, and putting it in the label list scored an engine that was 58/58
  correct on decidable cases as 0.75.  Abstention stays visible through
  ``refusal_aware_*`` and ``needs_review_rate``, which are separate, clearly
  named quantities — no metric silently mixes the two.
* **** — the B1 miss-rate gate fails *closed*: a degenerate
  "abstain on everything" strategy cannot satisfy it, empty negative sets no
  longer count as 0 %, every ``etymology_type`` branch must carry a minimum
  number of negative cases, and the exact one-sided Clopper–Pearson upper
  bound is printed next to the observed rate so a 4-negative sample cannot be
  read as "≤1 % achieved".
* **** — every proportion-style metric carries an exact binomial interval;
  macro-F1 carries a case-level paired bootstrap interval; system comparison
  uses a paired-difference bootstrap plus an exact McNemar test instead of
  "two independent intervals do not overlap".  Each metric dict names its own
  ``interval_method``/``interval_label``.
* **** — abstention behaviour is reported with a small-n disclosure
  (``insufficient_evidence``), because the shipped A-set contains exactly one
  case where abstention is the specified answer.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from .. import __version__
from .baselines import (
    engine_predictions_with_errors,
    majority_class_baseline,
    majority_class_of,
    naive_ending_baseline,
)
from .holdout import check_holdout
from .metrics import (
    DEFAULT_BOOTSTRAP_RESAMPLES,
    DEFAULT_BOOTSTRAP_SEED,
    PREDICTION_ONLY_LABELS,
    accuracy_events,
    binomial_upper_bound,
    clopper_pearson_interval,
    compare_systems_paired,
    confusion_matrix,
    exact_binomial_metric,
    false_negative_rate,
    macro_f1,
    macro_f1_interval,
    min_n_for_zero_events,
    per_class_f1,
)
from .sets import ATestCase, load_a_set, load_b1_set, load_b2_set, load_d_set

# ---------------------------------------------------------------------------
# Tunables (documented, overridable from the CLI)
# ---------------------------------------------------------------------------

#: Ground-truth value domain of ``ATestCase.label`` (see sets.py).  Anything
#: outside it can only ever come from the predictor, so it must never be used
#: as a macro-F1 label.
A_TRUE_LABEL_DOMAIN = ("m", "f", "n", "unknown")

#: The three gender classes a determination is actually possible for.
DECIDABLE_A_LABELS = ("m", "f", "n")

#: B1: minimum number of negative (violation) cases each ``etymology_type``
#: branch must contain before the miss-rate gate is allowed to pass.  Chosen
#: so that a single miss moves the rate by ≤10 % — i.e. so the branch can at
#: least register an error of the size the gate cares about.  The shipped seed
#: set has 2 negatives in `person`, 2 in `place` and **0 in `feature` and
#: `thing`**, so with this default the gate fails closed.  Override per run
#: with ``bench --b1-min-negatives N`` (``0`` reinstates the vacuous pass and
#: is itself reported as a disclosure).
DEFAULT_MIN_NEGATIVES_PER_BRANCH = 10

#: B1 miss-rate target ("FNR ≤ 1 %"), the value documented in USAGE §6 and echoed
#: back in the report's own ``thresholds`` block.  Kept as a named
#: tunable because the gate is only honest if a relaxed threshold is *echoed*
#: rather than silently accepted: ``gate.target_fnr`` records what was asked
#: for, and ``bench --b1-target-fnr`` sets it per run.
DEFAULT_B1_TARGET_FNR = 0.01

#: A-set: number of cases with a true ``unknown`` label needed before
#: "the engine abstains correctly" is evidence rather than an anecdote.  With
#: one case, correct abstention and "never abstains" differ by a single 0/1
#: observation.  Override with ``bench --min-abstention-cases N``.
DEFAULT_MIN_ABSTENTION_CASES = 20

#: B2/D: disclosure threshold for "this proportion is based on a handful of
#: cases" — the value is still reported, just flagged.
DEFAULT_MIN_PROPORTION_CASES = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _refusal_aware_predictions(preds: Sequence[str]) -> list[str]:
    """Map an abstention onto ``unknown``, the truth it was supposed to answer.

    The engine's specified behaviour for a genus with no applicable rule is
    ``needs_review``; the ground truth for such a case is the label
    ``unknown``.  Scoring those two as a match is what makes an
    abstention-aware view possible *without* dropping abstentions from the
    metric.  An abstention on a *decidable* case is still punished twice — it
    costs the true gender class a false negative and hands the ``unknown``
    class a false positive — so this mapping cannot be gamed by abstaining
    more.
    """
    return ["unknown" if p in PREDICTION_ONLY_LABELS else p for p in preds]


def _score_a_system(
    *,
    name: str,
    true_labels: list[str],
    preds: list[str],
    n_resamples: int,
    seed: int,
    min_abstention_cases: int,
) -> dict:
    """One comparable block per system (engine and both baselines)."""
    true_domain = sorted(set(true_labels))
    for lab in true_domain:
        assert lab in A_TRUE_LABEL_DOMAIN, (
            f"{name}: unexpected true label {lab!r} — the A-set value domain "
            "is fixed by sets.ATestCase.label; a new label needs a spec update, "
            "not a silent metric change"
        )
    n = len(true_labels)
    correct = accuracy_events(true_labels, preds)

    decidable_idx = [i for i, t in enumerate(true_labels) if t in DECIDABLE_A_LABELS]
    dec_true = [true_labels[i] for i in decidable_idx]
    dec_pred = [preds[i] for i in decidable_idx]
    dec_labels = sorted(set(dec_true))

    refusal_correct = [
        1 if (p == t or (t == "unknown" and p in PREDICTION_ONLY_LABELS)) else 0
        for p, t in zip(preds, true_labels)
    ]
    mapped_preds = _refusal_aware_predictions(preds)
    abstentions = [i for i, p in enumerate(preds) if p in PREDICTION_ONLY_LABELS]
    true_unknown_idx = [i for i, t in enumerate(true_labels) if t == "unknown"]

    block: dict = {
        "system": name,
        "n_cases": n,
        "labels_used_for_macro_f1": {
            "decidable": dec_labels,
            "refusal_aware": true_domain,
            "rule": "macro-F1 averages over classes present in the ground "
                    "truth only; the abstention symbol is never a label "
                    " — see metrics.macro_f1",
        },
        "accuracy": exact_binomial_metric(
            correct, n,
            name=f"{name}.accuracy",
            event_meaning="case scored correct under literal label equality "
                          "(an abstention on a true `unknown` case counts as wrong)",
        ),
        "refusal_aware_accuracy": exact_binomial_metric(
            sum(refusal_correct), n,
            name=f"{name}.refusal_aware_accuracy",
            event_meaning="correct, counting 'true unknown + predicted "
                          "needs_review' as the specified behaviour",
            note="this is the metric that answers 'did the engine do the right "
                 "thing', accuracy answers 'did it print the right string'",
        ),
        "needs_review_rate": exact_binomial_metric(
            len(abstentions), n,
            name=f"{name}.needs_review_rate",
            event_meaning="engine refused to assign a gender",
            min_n=min_abstention_cases,
        ),
        "decidable_subset": {
            "n_cases": len(decidable_idx),
            "note": "cases whose ground truth is a gender (m/f/n); the "
                    "three-way classification task proper",
            "accuracy": exact_binomial_metric(
                accuracy_events(dec_true, dec_pred), len(dec_true),
                name=f"{name}.decidable_accuracy",
            ),
            "macro_f1": macro_f1_interval(
                dec_true, dec_pred, labels=dec_labels,
                name=f"{name}.decidable_macro_f1",
                n_resamples=n_resamples, seed=seed,
                note="HEADLINE A-set metric. Bootstrap over cases, NOT the "
                     "accuracy interval; abstentions on decidable cases are "
                     "penalised through recall.",
            ),
            "per_class_f1": per_class_f1(dec_true, dec_pred, dec_labels),
        },
        "refusal_aware_macro_f1": macro_f1_interval(
            true_labels, mapped_preds, labels=true_domain,
            name=f"{name}.refusal_aware_macro_f1",
            n_resamples=n_resamples, seed=seed,
            note=f"macro-F1 over the full true-label domain {true_domain} "
                 "after mapping predicted abstention onto `unknown` (the "
                 "specified answer there). Penalises guessing where the "
                 "engine should refuse AND refusing where it can decide.",
        ),
        "raw_true_domain_macro_f1": {
            "value": round(macro_f1(true_labels, preds, true_domain), 4),
            "labels": true_domain,
            "interval": None,
            "interval_method": "not_applicable_diagnostic_only",
            "interval_label": "no interval: diagnostic value only, see note",
            "note": "scored with literal label equality over the whole true "
                    f"domain {true_domain}. The `unknown` class is never an "
                    "output symbol of the engine, so any refusal there is a "
                    "string mismatch and drags this number down for a reason "
                    "that is *not* a wrong gender call. Reported for "
                    "transparency; quote refusal_aware_macro_f1 / "
                    "decidable_macro_f1 instead.",
        },
    }
    # The abstention evidence base, spelled out rather than buried.
    # (The genera behind the counts are attached by `evaluate_a_set`, which
    # knows the case objects.)
    block["abstention_evidence"] = {
        "true_unknown_cases": len(true_unknown_idx),
        "true_unknown_genera": [],
        "insufficient_evidence": len(true_unknown_idx) < min_abstention_cases,
        "min_cases_for_evidence": min_abstention_cases,
        "note": (
            "abstention metrics rest on this many cases; with fewer than "
            f"{min_abstention_cases} they cannot distinguish 'correctly "
            "abstains' from 'never abstains'"
        ),
    }
    return block


# ---------------------------------------------------------------------------
# A-set
# ---------------------------------------------------------------------------

def evaluate_a_set(
    *,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    min_abstention_cases: int = DEFAULT_MIN_ABSTENTION_CASES,
) -> dict:
    """Evaluate the A-set: gender determination.

    Reports:
    - A-lookup: lexicon coverage (a data-asset metric, kept out of the
      accuracy narrative)
    - A-inference: decidable macro-F1 (headline) with a paired case-level
      bootstrap interval, refusal-aware variants, needs_review rate with its
      exact interval, and both mandatory baselines
    - paired significance tests against the baselines
    """
    cases = load_a_set()

    lookup_cases: list[ATestCase] = [c for c in cases if c.in_lexicon]
    inference_cases: list[ATestCase] = [c for c in cases if not c.in_lexicon]
    inference_labels = [c.label for c in inference_cases]

    engine_pred_pairs = engine_predictions_with_errors(inference_cases)
    engine_preds = [p for p, _ in engine_pred_pairs]
    engine_errors = [
        {"genus": c.genus, "true_label": c.label, "error": err}
        for c, (_p, err) in zip(inference_cases, engine_pred_pairs) if err
    ]
    # The plurality class is *computed*, not hardcoded.  No train/test
    # split exists for this benchmark, so the default training split is the
    # evaluation split itself — a mild label-prior leak that is disclosed in
    # the report rather than hidden (see baselines.majority_class_baseline).
    majority_class = majority_class_of(inference_cases)
    maj_preds = majority_class_baseline(inference_cases)
    naive_preds = naive_ending_baseline(inference_cases)

    scored = {
        "engine": dict(
            name="engine", true_labels=inference_labels, preds=engine_preds,
        ),
        "majority_class_baseline": dict(
            name="majority_class_baseline", true_labels=inference_labels,
            preds=maj_preds,
        ),
        "naive_ending_baseline": dict(
            name="naive_ending_baseline", true_labels=inference_labels,
            preds=naive_preds,
        ),
    }
    blocks: dict[str, dict] = {}
    unknown_genera = [c.genus for c in inference_cases if c.label == "unknown"]
    synthetic_genera = [c.genus for c in inference_cases if c.source != "lpsn"]
    for key, kwargs in scored.items():
        block = _score_a_system(
            n_resamples=n_resamples, seed=seed,
            min_abstention_cases=min_abstention_cases, **kwargs
        )
        block["abstention_evidence"]["true_unknown_genera"] = unknown_genera
        block["abstention_evidence"]["synthetic_genera_in_a_inference"] = synthetic_genera
        if key == "majority_class_baseline":
            block["majority_class"] = majority_class
            block["majority_class_provenance"] = {
                "computed_from": "the labels of the cases passed in (no "
                                 "held-out training split exists for this "
                                 "benchmark)",
                "label_prior_leakage": "yes — mild, documented. The baseline "
                                       "therefore gets to "
                                       "know the test label distribution, "
                                       "which makes it a *stronger* baseline "
                                       "than a genuinely trained one, not a "
                                       "straw man.",
                "alternatives": "the published lexicon's plurality gender is "
                                "`f` (23/58 in genus_gender.json), so a "
                                "lexicon-derived majority baseline would score "
                                "lower than the one used here.",
            }
        blocks[key] = block

    # Paired significance tests: engine vs each baseline on the same 59 cases.
    significance = {}
    for base_key in ("majority_class_baseline", "naive_ending_baseline"):
        significance[f"engine_vs_{base_key}"] = compare_systems_paired(
            inference_labels, engine_preds, scored[base_key]["preds"],
            labels=sorted(set(inference_labels)),
            name_a="engine", name_b=base_key,
            n_resamples=n_resamples, seed=seed,
        )

    return {
        "suite": "A-set (gender determination)",
        "total_cases": len(cases),
        "lookup_cases": len(lookup_cases),
        "inference_cases": len(inference_cases),
        "a_lookup": {
            "coverage": exact_binomial_metric(
                len(lookup_cases), len(cases),
                name="a_lookup_coverage",
                event_meaning="genus present in the published gender lexicon",
            ),
            "note": "data-asset metric; not part of the accuracy narrative",
        },
        "a_inference": {
            "true_label_domain": sorted(set(inference_labels)),
            "prediction_label_domain": sorted(set(engine_preds)),
            "confusion_matrix": confusion_matrix(
                inference_labels, engine_preds,
                true_labels=A_TRUE_LABEL_DOMAIN,
                # the abstention symbol is a *prediction-side* column only; it
                # is spelled here through metrics.PREDICTION_ONLY_LABELS so it
                # can never be mistaken for (or reused as) a macro-F1 label.
                pred_labels=(*A_TRUE_LABEL_DOMAIN, *sorted(PREDICTION_ONLY_LABELS)),
            ),
            "n_resamples": n_resamples,
            "bootstrap_seed": seed,
            "engine_error_cases": engine_errors,
            "engine_error_note": (
                "cases where the engine raised instead of answering. They are "
                "scored as refusals (a crash is not a determination) and are "
                "listed here so a crash can never masquerade as an honest "
                "abstention."
            ),
            **blocks,
            "significance_vs_baselines": significance,
            "superseded_tests": {
                "ci_disjoint_rule": "removed — comparing two independent "
                                    "intervals from paired predictions is not "
                                    "the test that was claimed; see "
                                    "significance_vs_baselines",
            },
        },
        "holdout_check": check_holdout(cases),
    }


# ---------------------------------------------------------------------------
# B1-set
# ---------------------------------------------------------------------------

def evaluate_b1_set(
    *,
    min_negatives_per_branch: int = DEFAULT_MIN_NEGATIVES_PER_BRANCH,
    target_fnr: float = DEFAULT_B1_TARGET_FNR,
    confidence: float = 0.95,
) -> dict:
    """Evaluate the B1-set: agreement validation.

    Main metric: false negative rate (漏报率 — violations judged compliant).
    Target: ≤1 % (``target_fnr``, configurable — the value is echoed back in
    ``gate.target_fnr`` so a relaxed threshold can never be read as the
    documented one).

    The target is *not* declared met unless the data can actually test it
.  Four independent conditions must hold:

    1. the set contains negative cases at all — with zero of them the miss
       rate is *undefined*, not 0 %, and the gate has nothing to test,
    2. every ``etymology_type`` branch carries at least
       ``min_negatives_per_branch`` negative cases (so no branch is vacuous),
    3. **no violation is missed**: an abstention or a "compliant" verdict on a
       violation fails the gate outright, whatever the sample size.  A pure
       rate comparison would let one miss hide in a large sample (1/1200 is
       0.08 % and would "pass" a ≤1 % reading), which is the reading an audit would reject
       objected to,
    4. the one-sided Clopper–Pearson upper bound of the observed rate is
       ≤ target, i.e. the sample really is compatible with the claim.

    With the shipped 26-case seed set, condition 2 fails for ``feature`` and
    ``thing`` (0 negatives each) and condition 4 fails overall
    (0/4 negatives ⇒ upper bound ≈ 0.527 ≫ 0.01), so the gate reports
    ``insufficient_evidence`` and exits non-zero.  Making the claim testable
    needs ≥ **299 negative cases** per branch at this target/confidence
    (``min_n_for_zero_events``), i.e. expert annotation, not code.
    """
    from prokname.engine.validate import validate_agreement

    cases = load_b1_set()
    y_true_violations: list[bool] = []
    y_pred_violations: list[bool | None] = []
    decided: list[bool] = []          # engine issued a True/False verdict
    correct_conservative: list[int] = []
    per_branch: dict[str, dict] = {}
    unverifiable: list[dict] = []
    missed_cases: list[dict] = []
    engine_errors: list[dict] = []
    details: list[dict] = []

    for case in cases:
        error: str | None = None
        try:
            vr = validate_agreement(
                case.genus, case.epithet, case.etymology_type,
                person_gender=case.person_gender,
                adjective_formation=case.adjective_formation,
            )
            pred_compliant = vr.compliant  # None = engine refused to decide
        except Exception as exc:  # noqa: BLE001 — a crash is reported, not hidden
            error = f"{type(exc).__name__}: {exc}"
            pred_compliant = None
        if error:
            engine_errors.append({
                "genus": case.genus, "epithet": case.epithet,
                "etymology_type": case.etymology_type, "error": error,
            })
        is_violation = not case.expected_compliant
        y_true_violations.append(is_violation)
        y_pred_violations.append(None if pred_compliant is None else not pred_compliant)
        issued = pred_compliant is not None
        decided.append(issued)
        # `pred_compliant is None` used to be auto-counted as correct for the
        # three `thing` cases because validate_agreement() structurally
        # returns None there; that credited the engine with verification it
        # never performed.  An abstention is now scored incorrect for
        # accuracy purposes and disclosed separately.
        correct_conservative.append(
            1 if (issued and pred_compliant == case.expected_compliant) else 0
        )

        branch = per_branch.setdefault(case.etymology_type, {
            "cases": 0, "negative_cases": 0, "missed": 0,
            "abstained_negative_cases": 0, "decided_negative_cases": 0,
        })
        branch["cases"] += 1
        if is_violation:
            branch["negative_cases"] += 1
            if not issued:
                # abstention = the violation was not flagged
                branch["abstained_negative_cases"] += 1
                branch["missed"] += 1
                missed_cases.append({
                    "genus": case.genus, "epithet": case.epithet,
                    "etymology_type": case.etymology_type,
                    "how": "engine_error" if error else "abstained_no_verdict",
                })
            else:
                branch["decided_negative_cases"] += 1
                if not y_pred_violations[-1]:
                    branch["missed"] += 1
                    missed_cases.append({
                        "genus": case.genus, "epithet": case.epithet,
                        "etymology_type": case.etymology_type,
                        "how": "judged_compliant",
                    })
        if not issued:
            unverifiable.append({
                "genus": case.genus, "epithet": case.epithet,
                "etymology_type": case.etymology_type,
                "expected_compliant": case.expected_compliant,
                "reason": "engine_error" if error else "no_rule_returns_none",
                "error": error,
            })
        details.append({
            "genus": case.genus, "epithet": case.epithet,
            "etymology_type": case.etymology_type,
            "expected_compliant": case.expected_compliant,
            "predicted_compliant": pred_compliant,
            "verdict_issued": issued,
            "engine_error": error,
            "correct": bool(correct_conservative[-1]),
        })

    n_total = len(cases)
    n_decided = sum(decided)
    negatives = sum(y_true_violations)
    # primary miss rate: abstention counts as a miss, so it cannot be gamed
    missed_conservative = sum(
        1 for t, p in zip(y_true_violations, y_pred_violations)
        if t and (p is None or p is False)
    )
    verdict_negatives = sum(
        1 for t, d in zip(y_true_violations, decided) if t and d
    )
    missed_verdict_only = sum(
        1 for t, d, p in zip(y_true_violations, decided, y_pred_violations)
        if t and d and p is False
    )
    fnr_conservative = false_negative_rate(y_true_violations, y_pred_violations)
    # The conditional ("among verdicts issued") view, recomputed independently
    # of the metric dict below: if the two ever disagree, the report would be
    # internally inconsistent, and that is caught here rather than published.
    fnr_verdict_only = false_negative_rate(
        y_true_violations,
        [p if d else None for p, d in zip(y_pred_violations, decided)],
        abstention_counts_as_miss=False,
    )
    if verdict_negatives:
        assert fnr_verdict_only is not None and abs(
            fnr_verdict_only - missed_verdict_only / verdict_negatives
        ) < 1e-9, (
            "B1 conditional miss rate disagrees with its own numerator and "
            "denominator"
        )
    required_n = min_n_for_zero_events(target_fnr, confidence)

    fnr_metric = exact_binomial_metric(
        missed_conservative, negatives,
        name="b1_false_negative_rate",
        event_meaning="true violation NOT flagged by the engine — a violation "
                      "judged compliant, or an abstention (a refusal does not "
                      "protect a user from a bad name, so it counts as a miss)",
        confidence=confidence,
        note="abstentions count as misses here; see "
             "false_negative_rate_among_verdicts for the conditional view",
    )
    fnr_verdict_metric = exact_binomial_metric(
        missed_verdict_only, verdict_negatives,
        name="b1_false_negative_rate_among_verdicts",
        event_meaning="violation judged compliant among negatives where a "
                      "verdict was actually issued",
        confidence=confidence,
        min_n=min_negatives_per_branch,
        note="conditional on the engine having decided — the denominator "
             "shrinks as the engine abstains, so it alone cannot certify the "
             "gate",
    )

    # per-branch disclosure + the fail-closed floor
    branches_report = {}
    branches_below_floor = []
    for etype, br in sorted(per_branch.items()):
        neg = br["negative_cases"]
        upper = binomial_upper_bound(br["missed"], neg, confidence) if neg else None
        branches_report[etype] = {
            "cases": br["cases"],
            "negative_cases": neg,
            "missed_negative_cases": br["missed"],
            "abstained_negative_cases": br["abstained_negative_cases"],
            "decided_negative_cases": br["decided_negative_cases"],
            "observed_fnr": None if neg == 0 else round(br["missed"] / neg, 4),
            "one_sided_upper_bound_95": None if upper is None else round(upper, 4),
            "min_negatives_required": min_negatives_per_branch,
            "meets_min_negatives": neg >= min_negatives_per_branch,
            "gate_is_binding": neg > 0,
        }
        if neg < min_negatives_per_branch:
            branches_below_floor.append(etype)

    # Degenerate-strategy counterfactuals: prove the gate cannot be satisfied
    # by doing nothing (the exact property that finding asked for).
    def _gate_view(missed: int, negatives_: int) -> dict:
        if negatives_ == 0:
            return {"observed_fnr": None, "one_sided_upper_bound_95": None,
                    "meets_target": False, "statistically_certified": False}
        ub = binomial_upper_bound(missed, negatives_, confidence)
        return {
            "observed_fnr": round(missed / negatives_, 4),
            "one_sided_upper_bound_95": round(ub, 4),
            "meets_target": missed / negatives_ <= target_fnr,
            "statistically_certified": ub <= target_fnr,
        }

    worst = _gate_view(negatives, negatives)
    best = _gate_view(0, negatives)
    counterfactuals = {
        "note": "miss rate under a degenerate strategy on THIS set, scored "
                "with the same definition as the primary metric (an "
                "abstention counts as a miss), so a strategy that never "
                "decides cannot print 0.0",
        "engine_abstains_on_everything": {
            **worst, "explanation": "every violation goes un-flagged, so the "
                                    "rate is 1.0 and the gate fails. Under the "
                                    "pre-fix definition — `None` mapped to "
                                    "'predicted violation' — this same "
                                    "strategy scored FNR 0.0 and passed.",
        },
        "engine_calls_everything_compliant": {
            **worst, "explanation": "the optimistic failure mode: rate 1.0, "
                                    "gate fails.",
        },
        "engine_flags_every_violation": {
            **best, "explanation": "what the shipped seed set achieves on its "
                                   f"{negatives} negatives — note that even "
                                   "this perfect record cannot certify the "
                                   "1% target at this sample size",
        },
    }

    observed_rate_ok = (
        fnr_conservative is not None and fnr_conservative <= target_fnr
    )
    # The target used to be read as "the *rate* is ≤ 1 %", which a large
    # sample can satisfy while still shipping a name nobody flagged.  On a
    # validation tool a missed violation is a hard failure: abstentions count
    # as misses (above) and one miss fails the gate whatever ``n`` is.
    no_missed_violations = missed_conservative == 0
    # An empty negative set makes the rate *undefined*; treating it as 0 % is
    # the vacuous pass the review named, so it can never be a pass here.
    nothing_to_test = negatives == 0
    upper_bound = fnr_metric.get("one_sided_upper_bound")
    certified = upper_bound is not None and upper_bound <= target_fnr
    evidence_ok = not branches_below_floor and not nothing_to_test
    no_crashes = not engine_errors
    behaviour_ok = observed_rate_ok and no_missed_violations and not nothing_to_test

    reasons = []
    if engine_errors:
        reasons.append(
            f"validate_agreement() raised on {len(engine_errors)} case(s) "
            f"({', '.join(sorted({e['error'].split(':')[0] for e in engine_errors}))}) "
            "— a crash is scored as a refusal, never as a pass"
        )
    if nothing_to_test:
        reasons.append(
            "the set contains 0 negative (violation) case(s), so the gate has "
            "nothing to test: the miss rate is undefined, NOT 0 % — this is "
            "the vacuous pass that finding was raised about"
        )
    if min_negatives_per_branch == 0:
        reasons.append(
            "DISCLOSURE: min_negatives_per_branch=0 — the per-branch negative "
            "floor has been switched off, so a branch with no negative case "
            "can no longer block this gate.  Any pass obtained under a zero "
            "floor is vacuous by construction and must not be quoted "
            "(bench --b1-min-negatives 0)."
        )
    elif branches_below_floor:
        reasons.append(
            f"etymology_type branch(es) with < {min_negatives_per_branch} "
            f"negative case(s): "
            + ", ".join(
                f"{e}={per_branch[e]['negative_cases']}" for e in branches_below_floor
            )
            + " — the gate has nothing to test there"
        )
    if negatives and negatives < required_n:
        reasons.append(
            f"{negatives} negative case(s) total, but certifying "
            f"'FNR <= {target_fnr:.0%}' at {confidence:.0%} confidence needs "
            f">= {required_n} (with 0 misses the exact upper bound here is "
            f"{upper_bound:.4f})"
        )
    if missed_conservative:
        reasons.append(
            f"{missed_conservative} of {negatives} violation(s) missed "
            f"({', '.join(sorted(e['epithet'] for e in missed_cases))}) — a "
            "missed violation fails this gate on its own, regardless of how "
            "small the resulting rate is"
        )
    elif negatives == 0:
        reasons.append(
            "no violation was missed because none was present — the gate "
            "cannot distinguish 'the engine flags violations' from 'the "
            "engine has never seen one'"
        )
    if fnr_conservative is not None and not observed_rate_ok:
        reasons.append(
            f"observed miss rate {fnr_conservative:.4f} exceeds target {target_fnr:.4f}"
        )

    if not no_crashes:
        status = "engine_error"
    elif nothing_to_test:
        # undefined rate ⇒ no evidence, and *never* a failure dressed up as a
        # pass or a pass dressed up as a failure of the engine
        status = "insufficient_evidence"
    elif not behaviour_ok:
        status = "failed"
    elif not (certified and evidence_ok):
        status = "insufficient_evidence"
    else:
        status = "passed"

    gate = {
        "gate": "b1_fnr",
        "passed": status == "passed",
        "status": status,
        "observed_fnr_within_target": bool(observed_rate_ok),
        "missed_violation_cases": int(missed_conservative),
        "no_missed_violations": bool(no_missed_violations),
        "hard_zero_miss_requirement": True,
        "target_statistically_certified": bool(certified),
        "evidence_sufficient": bool(evidence_ok),
        "branches_below_floor": list(branches_below_floor),
        "no_engine_errors": no_crashes,
        "reasons": reasons,
        "expansion_required": (
            f"add expert-labelled B1 negative cases until every "
            f"etymology_type branch has >= {max(min_negatives_per_branch, required_n)}"
            f" negatives (>= {required_n} for a {target_fnr:.0%} claim at "
            f"{confidence:.0%} confidence, `min_n_for_zero_events`); the set "
            "sizes stay frozen this round, so no labels were invented"
        ),
        "min_negatives_per_branch": min_negatives_per_branch,
        "target_fnr": target_fnr,
        "confidence_level": confidence,
        "threshold_provenance": (
            f"floor={min_negatives_per_branch} (default "
            f"{DEFAULT_MIN_NEGATIVES_PER_BRANCH}), target_fnr={target_fnr} "
            f"(default {DEFAULT_B1_TARGET_FNR}) — a relaxed threshold is "
            "reported here so it cannot be read as the documented one"
        ),
    }

    return {
        "suite": "B1-set (agreement validation)",
        "total_cases": n_total,
        "negative_cases": negatives,
        "positive_cases": n_total - negatives,
        "missed_violations": missed_cases,
        "verdict_coverage": exact_binomial_metric(
            n_decided, n_total,
            name="b1_verdict_coverage",
            event_meaning="cases where validate_agreement() returned a "
                          "True/False verdict instead of None",
        ),
        "structurally_unverifiable_cases": {
            "count": len(unverifiable),
            "cases": unverifiable,
            "by_etymology_type": {
                t: sum(1 for c in unverifiable if c["etymology_type"] == t)
                for t in sorted({c["etymology_type"] for c in unverifiable})
            },
            "abstained_on_expected_compliant": sum(
                1 for c in unverifiable if c["expected_compliant"]
            ),
            "abstained_on_violation": sum(
                1 for c in unverifiable if not c["expected_compliant"]
            ),
            "note": "validate_agreement() returns None for these by design "
                    "(the engine has no rule for `thing` genitives), so they "
                    "verify nothing. They are excluded from "
                    "accuracy_among_verdicts_issued, counted as incorrect in "
                    "the conservative accuracy, and reported here — they are "
                    "no longer silently added to the pass count. An "
                    "abstention on a *positive* (expected-compliant) case is "
                    "disclosed here as unverified; it is not evidence that the "
                    "name agrees, and it is counted as a miss when the case "
                    "was a violation.",
        },
        "accuracy": exact_binomial_metric(
            sum(correct_conservative), n_total,
            name="b1_accuracy_conservative",
            event_meaning="verdict issued AND it matched the expert label",
            note="conservative: an abstention is not a correct answer",
        ),
        "accuracy_among_verdicts_issued": exact_binomial_metric(
            sum(c for c, d in zip(correct_conservative, decided) if d), n_decided,
            name="b1_accuracy_among_verdicts_issued",
            event_meaning="verdict matched the expert label",
        ),
        "false_negative_rate": fnr_metric,
        "false_negative_rate_among_verdicts": fnr_verdict_metric,
        "negatives_per_etymology_type": branches_report,
        "engine_error_cases": engine_errors,
        "target_fnr": target_fnr,
        "negatives_required_to_test_target": required_n,
        "confidence_interval_method": "clopper_pearson_exact_binomial",
        "gate": gate,
        "degenerate_strategy_checks": counterfactuals,
        "results": details,
    }


# ---------------------------------------------------------------------------
# B2-set
# ---------------------------------------------------------------------------

def evaluate_b2_set() -> dict:
    """Evaluate the B2-set: generation exact-match.

    Main metric: exact-match rate (engine's generated epithet equals the
    published epithet).  Secondary: top-3 hit rate (published epithet among
    the first three candidates).
    """
    from prokname.engine.generate import generate

    cases = load_b2_set()
    exact_matches = 0
    top3_matches = 0
    results = []
    engine_errors: list[dict] = []

    for case in cases:
        try:
            candidates = generate(
                case.stem, case.etymology_type, "species",
                genus=case.genus,
                person_gender=case.person_gender,
                adjective_formation=case.adjective_formation,
            )
            generated_epithets = [c.epithet for c in candidates if c.epithet]
            error = None
        except Exception as exc:  # noqa: BLE001 — report, never silently skip
            generated_epithets = []
            error = f"{type(exc).__name__}: {exc}"
            engine_errors.append({
                "genus": case.genus, "stem": case.stem,
                "expected": case.expected_epithet, "error": error,
            })
        exact = case.expected_epithet in generated_epithets
        # top-3 = the published epithet among the FIRST THREE candidates.
        # (`exact or ...` used to credit any position, making top-3 a superset
        # of exact-match that carried no independent signal.)
        top3 = case.expected_epithet in generated_epithets[:3]
        exact_matches += int(exact)
        top3_matches += int(top3)
        results.append({
            "genus": case.genus,
            "stem": case.stem,
            "expected": case.expected_epithet,
            "generated": generated_epithets,
            "exact_match": exact,
            "engine_error": error,
        })

    total = len(cases)
    lower3, upper3 = (
        clopper_pearson_interval(top3_matches, total) if total else (None, None)
    )
    return {
        "suite": "B2-set (generation exact-match)",
        "total_cases": total,
        "exact_match_rate": exact_binomial_metric(
            exact_matches, total,
            name="b2_exact_match_rate",
            event_meaning="published epithet is produced by the generator",
            min_n=DEFAULT_MIN_PROPORTION_CASES,
        ),
        "top3_hit_rate": exact_binomial_metric(
            top3_matches, total,
            name="b2_top3_hit_rate",
            event_meaning="published epithet among the first 3 candidates",
            min_n=DEFAULT_MIN_PROPORTION_CASES,
            note=f"raw two-sided CI [{lower3:.4f}, {upper3:.4f}]" if total else "",
        ),
        "engine_error_cases": engine_errors,
        "results": results,
    }


# ---------------------------------------------------------------------------
# D-set
# ---------------------------------------------------------------------------

def evaluate_d_set() -> dict:
    """Evaluate the D-set: routing correctness.

    Special: the ICNP-preemption sub-class must be 100 % correct.  That is a
    hard per-case constraint (a wrong routing is wrong for everyone), so it
    stays an all-or-nothing gate — but its sample size is disclosed, because
    "3/3 correct" bounds the error rate only at ~0.63.
    """
    from prokname.routing import route

    cases = load_d_set()
    correct = 0
    preemption_correct = 0
    preemption_total = 0
    results = []
    engine_errors: list[dict] = []

    for case in cases:
        try:
            result = route(
                case.source_label,
                candidatus=case.candidatus,
                icnp_occupied=case.icnp_occupied,
            )
            actual_codes = [p.code for p in result.viable_paths]
            actual_roles = [p.role for p in result.viable_paths]
            error = None
        except Exception as exc:  # noqa: BLE001 — a crash is a wrong route
            actual_codes, actual_roles = [], []
            error = f"{type(exc).__name__}: {exc}"
            engine_errors.append({
                "source": case.source_label, "candidatus": case.candidatus,
                "icnp_occupied": case.icnp_occupied, "error": error,
            })
        is_correct = (
            set(actual_codes) == set(case.expected_codes)
            and set(actual_roles) == set(case.expected_roles)
        )
        correct += int(is_correct)
        if case.is_icnp_preemption:
            preemption_total += 1
            preemption_correct += int(is_correct)
        results.append({
            "source": case.source_label,
            "candidatus": case.candidatus,
            "icnp_occupied": case.icnp_occupied,
            "expected_codes": case.expected_codes,
            "actual_codes": actual_codes,
            "is_icnp_preemption": case.is_icnp_preemption,
            "engine_error": error,
            "correct": is_correct,
        })

    total = len(cases)
    preemption_metric = exact_binomial_metric(
        preemption_correct, preemption_total,
        name="d_icnp_preemption_accuracy",
        event_meaning="routing case in the ICNP-preemption sub-class correct",
        min_n=DEFAULT_MIN_PROPORTION_CASES,
        note="target is 100% (hard per-case constraint); the one-sided upper "
             "bound shows how much error 3/3 still tolerates",
    )
    meets = (
        preemption_total > 0
        and preemption_correct == preemption_total
        and not engine_errors
    )
    return {
        "suite": "D-set (dual-code routing)",
        "total_cases": total,
        "engine_error_cases": engine_errors,
        "accuracy": exact_binomial_metric(
            correct, total,
            name="d_accuracy",
            event_meaning="viable path codes AND roles match the expected set",
            min_n=DEFAULT_MIN_PROPORTION_CASES,
        ),
        "icnp_preemption": {
            "total": preemption_total,
            "correct": preemption_correct,
            "accuracy": preemption_metric,
            "target": 1.0,
            "meets_target": meets,
            "no_engine_errors": not engine_errors,
            "gate_semantics": "hard constraint: any wrong routing (or any "
                              "routing that raises) fails the gate; it is NOT "
                              "a sampled rate claim",
        },
        "results": results,
    }


# ---------------------------------------------------------------------------
# C-set
# ---------------------------------------------------------------------------

def evaluate_c_set(
    *,
    repetitions: int = 3,
    base_seed: int = DEFAULT_BOOTSTRAP_SEED,
    skip_gan: bool = False,
    gan_command: Sequence[str] | str | None = None,
) -> dict:
    """Evaluate the C-set: GAN comparison.

    Without a user-supplied command spec the suite reports the *protocol only*
    and produces no GAN-side numbers: the CLI flags of the competing
    tool are not documented anywhere in this repository and guessing them is
    not a basis for a comparison.
    """
    from .gan_compare import evaluate_c_set as _evaluate_c_set

    return _evaluate_c_set(
        repetitions=repetitions, base_seed=base_seed, skip_gan=skip_gan,
        gan_command=gan_command,
    )


# ---------------------------------------------------------------------------
# Gates + full run
# ---------------------------------------------------------------------------

def evaluate_gates(report: dict, *, require_complete: bool = False) -> dict:
    """Derive the CI gate block from a full benchmark report.

    Lives here (not in the CLI) so the gate logic is unit-testable without
    typer, and so no caller can re-implement it more leniently by accident.

    `require_complete=True` is milestone mode: evidence debt fails the run too.
    The default separates "the engine violated the target" (always blocking)
    from "the shipped set cannot test the target" (reported, never certified).
    """
    b1_gate = report["b1_set"]["gate"]
    b1_status = str(b1_gate.get("status", "failed"))
    # A gate the shipped data cannot test is NOT a gate the engine failed, and
    # conflating the two made every push red while saying nothing about the
    # code. `require_complete` is the milestone mode: at an M-review the debt
    # must fail, on a routine push a real violation must fail and the debt must
    # be loud but non-blocking. Nothing here ever turns `insufficient_evidence`
    # into a pass: it stays reported, stays non-certifying, and no claim resting
    # on that target can be made from this run either way.
    b1_violated = b1_status in ("failed", "engine_error")
    evidence_debt = [name for name, status in (("b1_fnr", b1_status),)
                     if status == "insufficient_evidence"]
    gates = {
        "holdout": report["a_set"]["holdout_check"]["passed"],
        "b1_fnr": not b1_violated,
        "d_preemption": report["d_set"]["icnp_preemption"]["meets_target"],
    }
    failed = [k for k, ok in gates.items() if not ok]
    if require_complete:
        failed += [g for g in evidence_debt if g not in failed]
    return {
        "gates": gates,
        "passed": not failed,
        "failed": failed,
        "b1_gate_status": b1_status,
        "evidence_debt": evidence_debt,
        "require_complete": bool(require_complete),
        "b1_gate_detail": b1_gate,
        "note": (
            "gate statuses: `failed` / `engine_error` = the engine violated the "
            "target, blocks the run. `insufficient_evidence` = the shipped set "
            "cannot test the target, so it is NOT certified and NOT a pass; it "
            "blocks only with require_complete=True (milestone mode). "
            "The benchmark never certifies a target it cannot test."
        ),
    }


def run_full_benchmark(
    *,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    min_negatives_per_branch: int = DEFAULT_MIN_NEGATIVES_PER_BRANCH,
    target_fnr: float = DEFAULT_B1_TARGET_FNR,
    min_abstention_cases: int = DEFAULT_MIN_ABSTENTION_CASES,
    repetitions: int = 3,
    skip_gan: bool = True,
    gan_command: Sequence[str] | str | None = None,
    require_complete: bool = False,
) -> dict:
    """Run all benchmark suites and return the comprehensive results structure.

    Deterministic apart from the top-level ``run_at`` stamp: the bootstrap
    resamples use a fixed seed (``seed``), the engine and the routing table
    are deterministic, and the C-set's wall-clock timings are deliberately
    excluded from the report.

    ``require_complete`` is milestone mode for the exit-code gates (see
    :func:`evaluate_gates`). It is resolved *here*, inside the report, so the
    ``gates_summary`` a reader quotes and the exit code CI sees are the same
    single evaluation — the CLI used to call evaluate_gates() a second time,
    which let a report written with one verdict drive an exit code computed
    under different rules.
    """
    report = {
        "benchmark_version": "0.2",
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_by": {
            "prokname_version": __version__,
            "python": sys.version.split()[0],
            "bootstrap_resamples": n_resamples,
            "bootstrap_seed": seed,
            "determinism": "everything except `run_at` is byte-reproducible "
                           "for a given code+data state",
        },
        "thresholds": {
            "b1_min_negatives_per_branch": min_negatives_per_branch,
            "b1_target_fnr": target_fnr,
            "a_min_abstention_cases": min_abstention_cases,
            "defaults": {
                "b1_min_negatives_per_branch": DEFAULT_MIN_NEGATIVES_PER_BRANCH,
                "b1_target_fnr": DEFAULT_B1_TARGET_FNR,
                "a_min_abstention_cases": DEFAULT_MIN_ABSTENTION_CASES,
            },
            "note": "recorded so a run with a relaxed gate can never be "
                    "published as the documented one",
        },
        "interval_methods": {
            "proportions": "exact Clopper–Pearson binomial (two-sided interval "
                           "+ one-sided upper bound)",
            "macro_f1": f"case-level paired bootstrap percentile "
                        f"({n_resamples} resamples, seed {seed})",
            "system_comparison": "paired-difference bootstrap + exact McNemar",
        },
        "note": (
            "v0.1 seed benchmark for CI and development. Paper-grade sets are "
            "LPSN-derived, holdout-controlled M1 deliverables (benchmark "
            "draft v0.2). Metrics here are indicative, not publication-ready: "
            "several are computed on denominators too small to support the "
            "targets named in ``thresholds`` — see each metric's "
            "`insufficient_evidence` / `one_sided_upper_bound` fields."
        ),
        "a_set": evaluate_a_set(
            n_resamples=n_resamples, seed=seed,
            min_abstention_cases=min_abstention_cases,
        ),
        "b1_set": evaluate_b1_set(
            min_negatives_per_branch=min_negatives_per_branch,
            target_fnr=target_fnr,
        ),
        "b2_set": evaluate_b2_set(),
        "c_set": evaluate_c_set(
            repetitions=repetitions, base_seed=seed, skip_gan=skip_gan,
            gan_command=gan_command,
        ),
        "d_set": evaluate_d_set(),
    }
    report["gates_summary"] = evaluate_gates(report,
                                             require_complete=require_complete)
    return report
