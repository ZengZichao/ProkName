"""Dual-code routing: viable paths with trade-offs, never a forced single pick.

Implements the routing decision flow:

1. Unknown source      -> ask for the source first. The result still carries
                          exactly one well-defined entry (role
                          ``needs-source``) so that a caller indexing
                          ``viable_paths[0]`` cannot fall off the end; use
                          ``RoutingResult.primary_path`` for a nullable read.
2. ICNP occupancy      -> an ICNP-validly-published name for the target means
                          a *dual-listing conflict*, not a nomenclatural bar:
                          prokname surfaces the ICNP conflict guidance FIRST
                          and keeps the SeqCode option visible, labelled with
                          its provenance. See ICNP_OCCUPANCY_NOTE.
3. pure_culture        -> both ICNP (default positive: IJSEM publication +
                          type-strain deposition) and SeqCode (accepts cultured
                          organisms; Registry + DOI timestamp) are legal;
                          present both with trade-offs.
4. MAG/SAG/Candidatus  -> SeqCode is the only channel (ICNP has no
                          sequence-type route); Candidatus formatting rules
                          apply.

Status of the pre-emption advice: the ICNP-priority
premise is COMMUNITY PRACTICE, not an article of either code. It therefore
may not be encoded as a hard precondition that hides the SeqCode path; the
codes are independent and each computes priority within itself. Expert
sign-off on the wording is pending (see PREEMPTION_PROVENANCE).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

GTDB_NOTE = (
    "GTDB placeholder labels (e.g. JABL01-style) are not names under the ICNP "
    "or SeqCode; prokname neither parses nor maps them."
)

#: As-of date of the sources checked below; refresh when the code texts or the
#: registry guidance are re-read.
PREEMPTION_AS_OF = "2026-09-21"

#: Where the pre-emption advice comes from — and where it demonstrably does
#: NOT come from. Sources: seq-code/documentation @ 10d08dec, read from the
#: vendored mirror and re-verified against that commit on 2026-09-25 —
#: docs/provenance/upstream-references.md#l3--seqcode-curation-and-pre-emption-advice
#:  * guide/curation.md:86-92 and :173-179 — curators confirm the parent genus
#:    "validly published under the SeqCode, ICNP, or ICNafp" and check that no
#:    "earlier correct, preferred, and legitimate genus under the SeqCode, ICNP
#:    or ICNafp" exists: cross-code checking is real CURATORIAL PRACTICE, and
#:    the three codes are listed side by side as independent sources of
#:    validity rather than one subordinating another;
#:  * explanation/open_data.md:24-31 — a SeqCode entry is "validated" (=
#:    validly published) by the SeqCode Registry itself;
#:  * explanation/open_data.md:49-53 — the Registry pulls name data from LPSN
#:    / NCBI / GTDB APIs, i.e. it reconciles the codes at the DATA level;
#:  * the SeqCode CODE TEXT (Articles, incl. the competitor/priority rules) is
#:    NOT part of that snapshot, so no article of either code could be cited
#:    as the authority for "ICNP priority beats SeqCode registration".
#: Conclusion recorded in the output, not hidden in a comment: the advice is
#: to avoid a dual-listing conflict, which is community practice pending
#: expert sign-off — not a requirement of the ICNP or of the SeqCode.
PREEMPTION_PROVENANCE = (
    "provenance: avoid-dual-listing advice, derived from SeqCode curatorial "
    "practice (seqcode-documentation @ 10d08dec: guide/curation.md:86-92, "
    ":173-179; explanation/open_data.md:24-31, :49-53) — NOT an article of "
    "the ICNP or of the SeqCode code text (the latter is not in the vendored "
    "snapshot); not a code requirement but community practice / registration "
    "advice; pending expert sign-off, as-of "
    f"{PREEMPTION_AS_OF}"
)

ICNP_OCCUPANCY_NOTE = (
    "Routing recommendation (not a code requirement): check the target "
    "against LPSN first, because a name that is already validly published "
    "under the ICNP and is then registered under SeqCode creates a "
    "dual-listing conflict for downstream users. SeqCode curators do check "
    "names against the other codes, but each code computes priority within "
    "itself. " + PREEMPTION_PROVENANCE
)

#: The role vocabulary this module may emit, kept in one place because the
#: Studio renders it: `studio/decision.py::_ROLE_KEYS` maps each role to a
#: label + palette token, and any role it does not know fail-safes to a muted
#: "unknown" badge (never to a green "recommended" one). The four routing
#: roles below are all mapped there; ``needs-source`` is deliberately NOT a
#: path recommendation and must keep rendering as a neutral/unknown gate.
#: Changing a spelling here without updating ``decision.py`` breaks the Studio
#: colour contract, which ``tests/studio/test_decision.py`` guards.
ROUTE_ROLES = frozenset({
    "default",          # ICNP, the conventional route for a pure culture
    "alternative",      # SeqCode, legal but less conventional for that source
    "only-viable",      # SeqCode, the sole channel (MAG/SAG/Candidatus) — a
                        # constraint, so it must never read as "recommended"
    "conflict-guidance",  # advisory entry about an existing name, not a route
    "needs-source",     # gate: no path selectable until the source is known
})


class RouteSource(str, Enum):
    PURE_CULTURE = "pure_culture"
    MAG = "MAG"
    SAG = "SAG"
    UNKNOWN = "unknown"


@dataclass
class PathOption:
    code: str  # "ICNP" | "SeqCode" | "unknown" (source not yet specified)
    role: str  # one of ROUTE_ROLES: "default" | "alternative" | "only-viable"
               # | "conflict-guidance" | "needs-source"
    tradeoffs: list[str] = field(default_factory=list)
    provenance: str = ""  # where this recommendation comes from (may be "")

    def as_dict(self) -> dict:
        payload = {"code": self.code, "role": self.role,
                   "tradeoffs": self.tradeoffs}
        if self.provenance:
            payload["provenance"] = self.provenance
        return payload


@dataclass
class RoutingResult:
    source: str
    candidatus: bool
    icnp_occupied: bool | None  # None = not checked yet
    viable_paths: list[PathOption] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def primary_path(self) -> PathOption | None:
        """The first path, or None — never an IndexError.

        Callers that used to write ``result.viable_paths[0]`` should use this
        (review that finding / §6: an unknown source produced an empty list that
        callers indexed).
        """
        return self.viable_paths[0] if self.viable_paths else None

    def path_for(self, code: str) -> PathOption | None:
        """First path offered under `code` ("ICNP" / "SeqCode"), or None."""
        return next((p for p in self.viable_paths if p.code == code), None)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "candidatus": self.candidatus,
            "icnp_occupied": self.icnp_occupied,
            "viable_paths": [p.as_dict() for p in self.viable_paths],
            "warnings": self.warnings,
            "notes": self.notes,
        }


def _conflict_guidance_paths(src: RouteSource) -> list[PathOption]:
    """Guidance entries for a name that LPSN already reports as published.

    Ordered ICNP-first: the ICNP entry says how to handle the existing name,
    the SeqCode entry states that registration there is NOT barred by either
    code but produces a dual-listing conflict. Both carry the same
    provenance string so the advice can never be read as a code requirement.
    """
    paths = [
        PathOption(
            code="ICNP",
            role="conflict-guidance",
            tradeoffs=[
                "handle the existing name (homonymy vs synonymy) under ICNP rules",
                "consult the LPSN entry for the correct name and priority date",
                "recommended: do not introduce a competing name for the same "
                "target while the ICNP name stands",
            ],
            provenance=PREEMPTION_PROVENANCE,
        ),
        PathOption(
            code="SeqCode",
            role="conflict-guidance",
            tradeoffs=[
                "legally reachable: neither code bars a SeqCode name for a "
                "taxon that already has an ICNP name — priority is computed "
                "within each code",
                "practical cost: dual listing of one taxon under two codes "
                "(downstream databases disagree on which name is 'the' name)",
                "SeqCode curation cross-checks names valid under SeqCode, ICNP "
                "and ICNafp, so expect curator push-back "
                "(seqcode-documentation guide/curation.md:86-92)",
            ],
            provenance=PREEMPTION_PROVENANCE,
        ),
    ]
    if src in (RouteSource.MAG, RouteSource.SAG):
        # ICNP cannot host a sequence-only type at all: guidance only.
        paths[0].tradeoffs.append(
            f"ICNP cannot carry this {src.value}: no sequence-only type route "
            "exists, so this entry is guidance about the existing name, not a "
            "path for the new one"
        )
    return paths


def route(
    source: str | RouteSource,
    *,
    candidatus: bool = False,
    icnp_occupied: bool | None = None,
) -> RoutingResult:
    """Choose viable nomenclatural paths for a candidate taxon."""
    src = RouteSource(source)
    result = RoutingResult(
        source=src.value,
        candidatus=candidatus,
        icnp_occupied=icnp_occupied,
        notes=[GTDB_NOTE],
    )

    if src is RouteSource.UNKNOWN:
        result.warnings.append(
            "data source unknown: specify pure_culture, MAG or SAG before routing"
        )
        # An explicit pre-emption finding from the caller is a CONFLICT
        # signal — it must survive the unknown-source early return, not be
        # swallowed by it.
        if icnp_occupied is True:
            result.warnings.append(
                "ICNP pre-emption (advisory): LPSN reports a validly published "
                "name for the target — the name is occupied regardless of the "
                "(still unknown) data source; resolve the conflict before "
                "routing. " + PREEMPTION_PROVENANCE
            )
        result.notes.append(ICNP_OCCUPANCY_NOTE)
        # §6 defect: this branch used to return an EMPTY
        # viable_paths, which callers indexed. A well-defined single entry
        # saying "no path is selectable yet" is returned instead — with code
        # "unknown", so nobody can mistake it for a recommended route.
        result.viable_paths.append(
            PathOption(
                code="unknown",
                role="needs-source",
                tradeoffs=[
                    "no path is selectable until the data source is known: "
                    "MAG/SAG/sequence-only types can be named under SeqCode "
                    "only, whereas a pure culture can use either code",
                    "re-run with an explicit source (and occupancy), e.g. "
                    "prokname route --source pure_culture|MAG|SAG "
                    "--icnp-occupied no|yes",
                ],
                provenance="routing flow, step 1",
            )
        )
        if icnp_occupied is True:
            result.viable_paths.extend(_conflict_guidance_paths(src))
        return result

    if icnp_occupied is True:
        # that finding: this used to hard-return the ICNP guidance ALONE, on a
        # premise that is community practice rather than an article of either
        # code. The SeqCode option stays visible — conflict advice and
        # provenance attached — because the two codes are independent.
        result.warnings.append(
            "ICNP pre-emption (advisory, not a code requirement): LPSN "
            "reports a validly published name for the target. Registering the "
            "same taxon under SeqCode as well would create a dual-listing "
            "conflict for downstream users, so the recommended action is to "
            "settle the existing name first (synonymy / different taxon). "
            + PREEMPTION_PROVENANCE
        )
        result.viable_paths.extend(_conflict_guidance_paths(src))
        return result

    if icnp_occupied is None:
        result.warnings.append(
            "ICNP occupancy not yet checked; paths below are provisional. "
            + ICNP_OCCUPANCY_NOTE
        )

    if src is RouteSource.PURE_CULTURE and not candidatus:
        result.viable_paths.append(
            PathOption(
                code="ICNP",
                role="default",
                tradeoffs=[
                    "field-standard route: effective publication in IJSEM",
                    "requires deposition of a viable type strain in two culture "
                    "collections in different countries",
                    "priority from effective-publication date",
                ],
            )
        )
        result.viable_paths.append(
            PathOption(
                code="SeqCode",
                role="alternative",
                tradeoffs=[
                    "legal alternative: SeqCode also accepts cultured organisms",
                    "genome (sequence) serves as nomenclatural type — no living "
                    "type strain required",
                    "priority from Registry DOI timestamp; faster publication "
                    "cycle outside IJSEM",
                    "community uptake for cultured taxa is still smaller than ICNP",
                ],
            )
        )
        return result

    tradeoffs = [
        "genome sequence (MAG/SAG/isolate) serves as the nomenclatural type",
        "registration + DOI timestamp in the SeqCode Registry",
        "must meet sequence minimum standards (completeness / contamination)",
    ]
    if candidatus:
        tradeoffs.append(
            "Candidatus formatting applies: prefix and its abbreviation 'Ca.' "
            "in roman (non-italic), the name itself italic; SeqCode can "
            "validly publish such names"
        )
        if src is RouteSource.PURE_CULTURE:
            # Semantically contradictory input: Candidatus status is reserved
            # for uncultured prokaryotes, so "pure_culture + candidatus" must
            # not be silently resolved to one reading.
            result.warnings.append(
                "contradictory input: pure_culture and Candidatus are in "
                "tension — Candidatus status is reserved for uncultured "
                "taxa. Interpreted here as 'SeqCode registration with "
                "Candidatus formatting'; if the organism is genuinely "
                "culturable, an ordinary ICNP (or SeqCode) name without the "
                "Candidatus prefix is the appropriate route."
            )
    if src is RouteSource.MAG:
        tradeoffs.append("MAG as type: meet the SeqCode quality thresholds for MAGs")
    if src is RouteSource.SAG:
        tradeoffs.append("SAG as type: meet the SeqCode quality thresholds for SAGs")
    result.viable_paths.append(
        PathOption(code="SeqCode", role="only-viable", tradeoffs=tradeoffs)
    )
    result.notes.append(
        "ICNP has no route for sequence-only types; for cultured isolates the "
        "ICNP path remains open if a type strain is later deposited"
    )
    return result
