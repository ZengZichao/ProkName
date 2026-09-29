"""D-set data <-> router contract.

`src/prokname/benchmark/data/d_set.json` states, per case, which path *codes*
and *roles* `route()` is expected to produce. Two independent things can drift
and the shipped metric hides both of them:

1. ``evaluator.evaluate_d_set()`` compares the expectations with ``set()``, so
   it cannot see the order of the paths, nor which role belongs to which code;
   ``["ICNP", "SeqCode"] + ["conflict-guidance", "conflict-guidance"]`` and a
   hypothetical ``["SeqCode", "ICNP"] + ["conflict-guidance", "default"]``
   score identically there.
2. because the same scorer compares the data against the router, weakening an
   expectation is indistinguishable from fixing a route: a change that dropped
   the ICNP conflict guidance (or hid the SeqCode option again, the pre-existing
   behaviour) would turn the metric green just as easily by editing the JSON.

So this module asserts the *stronger* contract the data now records — the
ordered, positionally-paired (code, role) list each case expects — and pins the
one property that must survive any future re-interpretation of the review: an
ICNP-occupied case keeps demanding a ``conflict-guidance`` entry. That
assertion is checked against the JSON text, not derived from the router, so it
cannot be satisfied by editing the data to match a router that stopped warning.
"""

from __future__ import annotations

import pytest

from prokname.benchmark import evaluator
from prokname.benchmark.sets import DTestCase, load_d_set
from prokname.routing import route
from prokname.routing.router import PREEMPTION_PROVENANCE, ROUTE_ROLES

#: The only path codes ``router.PathOption.code`` may carry: the two codes, plus
#: the explicit "no code selectable yet" marker of the unknown-source gate.
ROUTE_CODES = frozenset({"ICNP", "SeqCode", "unknown"})

CASES: list[DTestCase] = load_d_set()


def _case_id(case: DTestCase) -> str:
    return (
        f"{case.source_label}|candidatus={int(case.candidatus)}"
        f"|icnp_occupied={int(case.icnp_occupied)}"
        f"|preemption={int(case.is_icnp_preemption)}"
    )


def _expected_pairs(case: DTestCase) -> list[tuple[str, str]]:
    """The positional (code, role) contract the case declares."""
    return list(zip(case.expected_codes, case.expected_roles))


def _actual_pairs(case: DTestCase) -> list[tuple[str, str]]:
    result = route(
        case.source_label,
        candidatus=case.candidatus,
        icnp_occupied=case.icnp_occupied,
    )
    return [(p.code, p.role) for p in result.viable_paths]


# --- the data is well-formed on its own -------------------------------------

def test_d_set_declares_preemption_cases():
    """Without the hard-gate sub-class the 100 % constraint tests nothing."""
    preemption = [c for c in CASES if c.is_icnp_preemption]
    assert len(preemption) >= 3, (
        "the ICNP-preemption sub-class is the all-or-nothing gate; with fewer "
        f"than 3 cases ({[_case_id(c) for c in preemption]}) it is decoration"
    )
    assert all(c.icnp_occupied for c in preemption)


@pytest.mark.parametrize("case", CASES, ids=[_case_id(c) for c in CASES])
def test_d_set_expectation_is_well_formed(case: DTestCase):
    """Codes/roles come from the router's own vocabulary and pair up.

    An empty expectation would re-introduce the pre-existing encoding ("no path at
    all is selectable") for the unknown-source gate, which review §6 rejected:
    `route()` answers with exactly one `needs-source` entry so that a caller
    indexing `viable_paths[0]` cannot fall off the end.
    """
    assert case.expected_codes, _case_id(case)
    assert len(case.expected_codes) == len(case.expected_roles), (
        "expected_codes/expected_roles are positional; a length mismatch makes "
        "the pairing undefined and the case un-annotatable"
    )
    assert set(case.expected_codes) <= ROUTE_CODES, case.expected_codes
    assert set(case.expected_roles) <= ROUTE_ROLES, case.expected_roles


# --- the data and the router agree ------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=[_case_id(c) for c in CASES])
def test_d_set_expectation_is_satisfiable_by_the_router(case: DTestCase):
    """`route()` produces exactly the ordered (code, role) pairs declared.

    Stronger than the benchmark metric on purpose: the ICNP guidance must be
    FIRST (that is what makes it the recommendation) and the SeqCode entry must
    sit at the position the case names it at, so neither side can be dropped,
    reordered, or re-roled without this test failing.
    """
    assert _expected_pairs(case) == _actual_pairs(case), (
        f"{_case_id(case)}: data says {_expected_pairs(case)}, "
        f"router says {_actual_pairs(case)}"
    )


def test_d_set_covers_every_router_branch():
    """No routing role may exist in the code without being benchmarked.

    `only-viable` / `default` + `alternative` / `conflict-guidance` /
    `needs-source` each have to appear in at least one case, otherwise a whole
    branch of §4.4 could regress while every D case still passes.
    """
    expected_roles = {r for c in CASES for r in c.expected_roles}
    assert expected_roles == set(ROUTE_ROLES), (
        f"unbenchmarked roles: {set(ROUTE_ROLES) - expected_roles}; "
        f"roles the data expects but the router never emits: "
        f"{expected_roles - set(ROUTE_ROLES)}"
    )


# --- the invariant: the conflict warning may not be dropped -------------

@pytest.mark.parametrize(
    "case", [c for c in CASES if c.icnp_occupied],
    ids=[_case_id(c) for c in CASES if c.icnp_occupied],
)
def test_icnp_occupied_case_still_demands_conflict_guidance(case: DTestCase):
    """A pre-emption finding must keep producing guidance, in the data and code.

    Deliberately asserted twice, from two directions:

    * against the JSON: the expectation names `conflict-guidance` and BOTH
      codes, so nobody can make `d_icnp_preemption_accuracy` green by editing
      the data down to a single ICNP path (the old bug) or to an empty list;
    * against the router: an advisory warning naming the dual-listing conflict
      and carrying the provenance string must actually be emitted.
    """
    assert "conflict-guidance" in case.expected_roles, (
        f"{_case_id(case)}: the expectation no longer requires the conflict "
        "guidance path — that is the regression this test exists to catch"
    )
    assert {"ICNP", "SeqCode"} <= set(case.expected_codes), (
        f"{_case_id(case)}: ICNP-occupied material must expose the ICNP "
        "guidance AND the visible SeqCode option"
    )
    assert case.expected_codes.index("ICNP") < case.expected_codes.index("SeqCode"), (
        f"{_case_id(case)}: guidance order changed — the ICNP entry leads so "
        "the advice stays first, the SeqCode path stays merely visible"
    )

    result = route(
        case.source_label,
        candidatus=case.candidatus,
        icnp_occupied=case.icnp_occupied,
    )
    guidance = [p for p in result.viable_paths if p.role == "conflict-guidance"]
    assert {p.code for p in guidance} == {"ICNP", "SeqCode"}
    assert all(p.provenance == PREEMPTION_PROVENANCE for p in guidance), (
        "conflict guidance without its provenance reads as a code requirement, "
        "which is exactly what the router must not do"
    )
    joined = " ".join(result.warnings)
    assert PREEMPTION_PROVENANCE in joined, (
        f"{_case_id(case)}: no advisory warning was emitted for occupied "
        "material — the metric can still be green while the user is left "
        "without the dual-listing warning"
    )
    assert "pre-emption" in joined and "dual-listing" in joined


def test_preemption_hard_gate_reports_the_data_not_a_weakened_one():
    """End-to-end: the shipped scorer finds every case satisfied.

    This is the drift check between the three layers (JSON, router, metric).
    `meets_target` is the CI gate `evaluate_gates()` reads, so a future change
    that silently emptied a path list — on either side — lands here too.
    """
    report = evaluator.evaluate_d_set()
    assert not report["engine_error_cases"], report["engine_error_cases"]
    wrong = [r for r in report["results"] if not r["correct"]]
    assert not wrong, wrong
    preemption = report["icnp_preemption"]
    assert preemption["total"] == sum(1 for c in CASES if c.is_icnp_preemption)
    assert preemption["correct"] == preemption["total"]
    assert preemption["meets_target"] is True
    # 3/3 is NOT evidence of a 100 % rate; the disclosure must stay visible.
    assert preemption["accuracy"]["insufficient_evidence"] is True
