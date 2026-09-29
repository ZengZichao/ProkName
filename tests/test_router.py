"""Unit tests: dual-code routing.

Review defects covered:
- The ICNP-priority premise is community practice, not an article of
  either code → the pre-empted route must keep the SeqCode option visible and
  must label the advice with its provenance and as-of date.
- §6 routing row: `source == "unknown"` used to return an empty `viable_paths`
  that callers indexed → a well-defined entry plus a nullable accessor.
"""

import itertools

import pytest

from prokname.routing import RouteSource, route
from prokname.routing.router import (
    ICNP_OCCUPANCY_NOTE,
    PREEMPTION_AS_OF,
    PREEMPTION_PROVENANCE,
    ROUTE_ROLES,
)

ALL_ROUTING_INPUTS = list(itertools.product(
    [s.value for s in RouteSource], (False, True), (None, False, True),
))


def test_unknown_source_asks_first_but_stays_indexable():
    """§6 defect: never hand callers an empty list they index with [0]."""
    r = route("unknown")
    assert any("specify" in w for w in r.warnings)
    assert len(r.viable_paths) == 1
    gate = r.viable_paths[0]
    assert gate.role == "needs-source"
    assert gate.code == "unknown", "must not read as a recommended code path"
    assert any("until the data source is known" in t for t in gate.tradeoffs)
    # the guarded accessor is the documented way to read "maybe nothing"
    assert r.primary_path is gate
    assert r.path_for("ICNP") is None
    assert r.path_for("SeqCode") is None


def test_primary_path_is_none_only_when_there_is_really_no_path():
    """`primary_path` must be a total function (no IndexError possible)."""
    r = route("pure_culture", icnp_occupied=False)
    assert r.primary_path is r.viable_paths[0]


def test_icnp_preemption_keeps_both_codes_visible():
    """the target is occupied under ICNP — guidance, not a hidden path."""
    r = route(RouteSource.MAG, icnp_occupied=True)
    roles = {p.role for p in r.viable_paths}
    assert roles == {"conflict-guidance"}
    codes = [p.code for p in r.viable_paths]
    assert codes == ["ICNP", "SeqCode"], "ICNP guidance first, SeqCode kept"
    assert any("pre-emption" in w for w in r.warnings)
    # the advice may not be phrased as a code requirement
    joined = " ".join(r.warnings)
    assert "not a code requirement" in joined
    assert "dual-listing" in joined
    for path in r.viable_paths:
        assert path.provenance == PREEMPTION_PROVENANCE
    seqcode = r.path_for("SeqCode")
    assert any("legally reachable" in t for t in seqcode.tradeoffs)
    assert any("curator" in t.lower() for t in seqcode.tradeoffs)
    icnp = r.path_for("ICNP")
    assert any("no sequence-only type route" in t for t in icnp.tradeoffs)


def test_preemption_provenance_names_what_was_checked_and_what_was_not():
    """The provenance string must be falsifiable from the repo alone."""
    assert PREEMPTION_AS_OF in PREEMPTION_PROVENANCE
    assert "pending expert sign-off" in PREEMPTION_PROVENANCE
    assert "seqcode-documentation" in PREEMPTION_PROVENANCE
    assert "not an article of" in PREEMPTION_PROVENANCE.lower()
    # The contract asks for the provenance of the ADVICE to be explicit in the user
    # visible string, not only in the module docstring: it is community
    # practice / registration advice, not a code requirement, dated as-of.
    assert "community practice" in PREEMPTION_PROVENANCE
    assert "not a code requirement" in PREEMPTION_PROVENANCE
    assert "recognises ICNP priority" not in PREEMPTION_PROVENANCE
    assert "recognises ICNP priority" not in ICNP_OCCUPANCY_NOTE
    assert "precondition" not in ICNP_OCCUPANCY_NOTE.lower().replace("-", ""), (
        "the occupancy check may not be phrased as a precondition any more"
    )
    assert "not a code requirement" in ICNP_OCCUPANCY_NOTE


def test_unknown_source_still_surfaces_the_preemption_signal():
    """The routing signal contract must survive the rewrite."""
    r = route("unknown", icnp_occupied=True)
    joined = " ".join(r.warnings)
    assert "ICNP pre-emption" in joined
    assert "occupied" in joined
    assert r.path_for("SeqCode") is not None
    assert r.viable_paths[0].role == "needs-source"


def test_pure_culture_yields_both_paths_with_tradeoffs():
    r = route("pure_culture", icnp_occupied=False)
    codes = {p.code: p for p in r.viable_paths}
    assert set(codes) == {"ICNP", "SeqCode"}
    assert codes["ICNP"].role == "default"
    assert codes["SeqCode"].role == "alternative"
    assert any("type strain" in t for t in codes["ICNP"].tradeoffs)
    assert any("also accepts cultured" in t for t in codes["SeqCode"].tradeoffs)


def test_mag_routes_seqcode_only():
    r = route("MAG", icnp_occupied=False)
    assert len(r.viable_paths) == 1
    assert r.viable_paths[0].code == "SeqCode"
    assert r.viable_paths[0].role == "only-viable"
    assert any("completeness" in t for t in r.viable_paths[0].tradeoffs)


def test_candidatus_mentions_roman_prefix_formatting():
    r = route("MAG", candidatus=True, icnp_occupied=False)
    assert any("roman" in t for t in r.viable_paths[0].tradeoffs)


def test_unchecked_occupancy_yields_provisional_paths():
    r = route("MAG", icnp_occupied=None)
    assert any("provisional" in w for w in r.warnings)
    assert r.viable_paths  # still shows the path, with the warning
    assert all(p.provenance == "" for p in r.viable_paths), (
        "only the pre-emption advice carries a provenance string"
    )


def test_viable_paths_serialise_provenance_only_when_present():
    r = route("MAG", icnp_occupied=True)
    payloads = r.as_dict()["viable_paths"]
    assert all("provenance" in p for p in payloads)
    plain = route("MAG", icnp_occupied=False).as_dict()["viable_paths"]
    assert all("provenance" not in p for p in plain), (
        "an unremarkable path must not carry an empty provenance key"
    )


def test_gtdp_boundary_always_noted():
    for src in ("pure_culture", "MAG", "SAG"):
        assert any("GTDB" in n for n in route(src).notes)


# --------------------------------------------------------------------------- #
# Structural invariants over the whole input space (IndexError + guidance loss)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("source,candidatus,occupied", ALL_ROUTING_INPUTS)
def test_no_routing_input_yields_an_indexable_empty_result(source, candidatus, occupied):
    """Every (source x candidatus x occupancy) combination is safe to index.

    the LPSN status label vocabulary"unknown"` returned an empty
    `viable_paths` that callers indexed. Rather than trusting the branch
    structure, this enumerates the full input space and requires a
    well-defined first path from both the raw list and `primary_path`.
    """
    r = route(source, candidatus=candidatus, icnp_occupied=occupied)
    assert r.viable_paths, f"empty viable_paths for {source}/{candidatus}/{occupied}"
    assert r.primary_path is r.viable_paths[0]
    assert r.as_dict()["viable_paths"][0]["code"] == r.viable_paths[0].code
    assert all(p.role for p in r.viable_paths), "a path may not have an empty role"


def test_emitted_roles_stay_inside_the_declared_vocabulary():
    """`presentation/decision.py` styles roles by exact string; drift must fail here."""
    emitted: set[str] = set()
    for source, candidatus, occupied in ALL_ROUTING_INPUTS:
        r = route(source, candidatus=candidatus, icnp_occupied=occupied)
        emitted |= {p.role for p in r.viable_paths}
    assert emitted <= ROUTE_ROLES, f"undocumented role(s): {emitted - ROUTE_ROLES}"
    assert emitted == ROUTE_ROLES, (
        f"declared but never emitted: {ROUTE_ROLES - emitted}"
    )
    # the four roles decision.py maps explicitly must keep their spelling
    assert {"default", "alternative", "only-viable", "conflict-guidance"} <= emitted


def test_only_viable_is_never_simultaneously_the_default_route():
    """MAG/SAG/Candidatus = SeqCode-only is a CONSTRAINT, not a green 'recommended'.

    decision.py renders `only-viable` as a warning colour and reserves
    COLOR_SUCCESS for `default`; the router must not muddy that by emitting
    the two together, or by presenting an ICNP path for a sequence-only type.
    """
    for source, candidatus in (("MAG", False), ("SAG", False), ("MAG", True),
                               ("SAG", True)):
        r = route(source, candidatus=candidatus, icnp_occupied=False)
        roles = {p.role for p in r.viable_paths}
        assert roles == {"only-viable"}
        assert [p.code for p in r.viable_paths] == ["SeqCode"]
        assert all(p.provenance == "" for p in r.viable_paths), (
            "an ordinary path carries no pre-emption provenance"
        )
    # `default` belongs to the unencumbered pure-culture ICNP route only
    assert {p.role for p in route("pure_culture", icnp_occupied=False).viable_paths} == {
        "default", "alternative"
    }


def test_preempted_route_never_sells_a_seqcode_name_as_clean():
    """Guard: the SeqCode option stays visible WITH its warning attached.

    Keeping the path visible is only correct if the dual-listing cost and the
    provenance ride along — otherwise the router would be recommending coining
    a SeqCode name for an ICNP-occupied target, which is the failure mode this
    contract exists to prevent.
    """
    for source in ("pure_culture", "MAG", "SAG"):
        r = route(source, icnp_occupied=True)
        seqcode = r.path_for("SeqCode")
        assert seqcode is not None, "the SeqCode option may not be hidden"
        assert seqcode.role == "conflict-guidance", (
            "a preempted SeqCode entry is guidance, never a recommended path"
        )
        assert seqcode.provenance == PREEMPTION_PROVENANCE
        joined = " ".join(seqcode.tradeoffs)
        assert "dual listing" in joined or "dual-listing" in joined
        assert "legally reachable" in joined
        assert r.viable_paths[0].code == "ICNP", "conflict guidance comes first"
