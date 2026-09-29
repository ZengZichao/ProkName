"""Holdout leakage control.

CI-blocking check: the published lexicon (genus_gender.json) must be DISJOINT
from the A-inference test set. If any genus appears in both, the benchmark is
contaminated and the merge must fail.
"""

from __future__ import annotations

from collections.abc import Sequence

from .sets import ATestCase, load_a_set


def holdout_violations(
    a_set: Sequence[ATestCase] | None = None,
) -> list[str]:
    """Return genera that appear in BOTH the lexicon and the A-inference set.

    An empty list means the holdout is intact (no leakage).

    Detection goes through `gender_of` itself so the normalisation used here
    (latinize + capitalize) can never drift from the one the engine applies
    at lookup time.
    """
    from prokname.engine.gender import gender_of

    if a_set is None:
        a_set = load_a_set()

    violations = []
    for case in a_set:
        if case.in_lexicon:
            continue
        # Any lexicon hit for an A-inference genus is leakage, regardless of
        # the determination mode the engine ends up reporting.
        if gender_of(case.genus).mode == "lookup":
            violations.append(case.genus)
    return violations


def check_holdout(
    a_set: Sequence[ATestCase] | None = None,
) -> dict:
    """Check holdout integrity; returns a structured report.

    This function is designed to be used in CI as a blocking gate:
    - exit 0 (passed) if violations is empty
    - exit 1 (failed) if any genus leaks between lexicon and A-inference set
    """
    violations = holdout_violations(a_set)
    return {
        "check": "holdout-integrity",
        "passed": len(violations) == 0,
        "violations": violations,
        "message": (
            "A-inference set is disjoint from the published lexicon ✓"
            if not violations
            else f"HOLDOUT VIOLATION: {len(violations)} genus/genera appear in "
            f"both the lexicon and the A-inference set: {violations}. "
            "Remove them from the lexicon or from the test set before merging."
        ),
    }
