"""Unified result model for the dedup orchestration layer.

Status vocabulary (single source of truth; adapters produce these, the
orchestrator classifies on them — see dedup/orchestrator.py):

  not_found          the source was reachable and has no record for the name.
  found_valid        authority tier: the name is the correct, validly published
                     one (LPSN status table: category ``occupied_valid``).
  found_occupied     authority tier: the exact string is occupied but is not the
                     correct name (synonym / later homonym). Conflict.
  found_other        a record for the exact string exists but does NOT occupy
                     the name (variant, "in preparation", "non-validly published
                     name", NCBI "authority" citation rows, ...). WARNING tier —
                     it must never be adjudicated as a conflict (review defect
                     M4, over-blocking half).
  found_synonym      REFERENCE tier only (GNA ``isSynonym``, NCBI
                     ``name_class=synonym``): the string is used as a synonym.
  found_reference    REFERENCE tier: an exact, verified usage record.
  found_near_match   REFERENCE tier: the service matched a DIFFERENT name
                     fuzzily (GNA non-Exact matchType). Never occupancy
.
  found_parahomonym  REFERENCE tier: the source flags this string as a
                     misspelling / orthographic variant of another name
                     (NCBI ``name_class=misspelling``).
  found_unknown      the source answered but prokname could not classify the
                     answer against a sourced table. Treated like
                     ``unavailable``: no confident verdict is emitted.
  unavailable        the source could not be reached / is gated.

Every ``found_*`` status therefore carries an explicit answer; only
``not_found`` and ``found_*`` results with ``verified=True`` may be phrased
confidently. ``verified=False`` means the classification came from an
unverified table row and the wording must stay provisional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    CONFLICT = "conflict"                    # authority reports an occupied name
    PARAHOMONYM_WARNING = "parahomonym_warning"
    VERIFY_WARNING = "verify_warning"        # reference sources flag usage
    BLOCKED = "blocked"                      # authorities unavailable: no ruling
    NO_CLEAR_CONFLICT = "no_clear_conflict"  # absent from authorities — verify


# --- status vocabulary -------------------------------------------------------

NOT_FOUND = "not_found"
FOUND_VALID = "found_valid"
FOUND_OCCUPIED = "found_occupied"
FOUND_SYNONYM = "found_synonym"
FOUND_OTHER = "found_other"
FOUND_REFERENCE = "found_reference"
FOUND_NEAR_MATCH = "found_near_match"
FOUND_PARAHOMONYM = "found_parahomonym"
FOUND_UNKNOWN = "found_unknown"
UNAVAILABLE = "unavailable"

#: Authority-tier statuses that mean "somebody already owns this name string".
OCCUPYING_STATUSES = frozenset({FOUND_VALID, FOUND_OCCUPIED, FOUND_SYNONYM})

#: Statuses that only warn (usage flag, not an occupancy claim).
WARNING_STATUSES = frozenset({
    FOUND_OTHER, FOUND_REFERENCE, FOUND_NEAR_MATCH, FOUND_PARAHOMONYM,
})

#: Statuses that mean "this source could not answer" — they block adjudication.
NON_RULING_STATUSES = frozenset({UNAVAILABLE, FOUND_UNKNOWN})


@dataclass
class SourceResult:
    name: str                # "LPSN" | "SeqCode" | "GNA" | "NCBI" ...
    status: str              # see the module docstring vocabulary
    tier: str                # "authority" | "reference"
    detail: str = ""
    url: str | None = None
    cached: bool = False
    #: True when the answer came out of a live query (allow_network=True).
    #: Offline runs must never replay an online-derived answer — review
    #: that finding (the offline-replay hole).
    online_derived: bool = False
    #: False when the classification rests on an unverified table row or on a
    #: field shape prokname could not source; the wording must stay provisional.
    verified: bool = True

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "tier": self.tier,
            "detail": self.detail,
            "url": self.url,
            "cached": self.cached,
            "online_derived": self.online_derived,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, payload: dict, *, default_name: str = "",
                  default_tier: str = "authority") -> SourceResult:
        """Rebuild a result from a cache record.

        Explicit (rather than ``**payload``) so that a renamed or dropped key
        shows up as a missing field here instead of silently vanishing from
        the adjudication input.
        """
        return cls(
            name=payload.get("name", default_name),
            status=payload.get("status", UNAVAILABLE),
            tier=payload.get("tier", default_tier),
            detail=payload.get("detail", ""),
            url=payload.get("url", ""),
            cached=bool(payload.get("cached", False)),
            online_derived=bool(payload.get("online_derived", False)),
            verified=bool(payload.get("verified", True)),
        )


@dataclass
class NearMatch:
    corpus_name: str
    distance: int
    source: str
    #: Which comparison produced the hit — "whole" (full normalized name),
    #: "stem" (inflectional endings stripped from the epithet) or "core"
    #: (the leading binomial of a longer corpus entry, so that
    #: 'Bacillus subtilis subsp. spizizenii' is reachable by a binomial
    #: query — review that finding).
    caliber: str = "whole"

    def as_dict(self) -> dict:
        return {
            "corpus_name": self.corpus_name,
            "distance": self.distance,
            "source": self.source,
            "caliber": self.caliber,
        }


@dataclass
class CheckReport:
    query: str
    checked_at: str
    sources: list[SourceResult] = field(default_factory=list)
    near_matches: list[NearMatch] = field(default_factory=list)
    near_match_corpus: dict | None = None
    verdict: Verdict | None = None
    warnings: list[str] = field(default_factory=list)
    attribution: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "checked_at": self.checked_at,
            "sources": [s.as_dict() for s in self.sources],
            "near_matches": [m.as_dict() for m in self.near_matches],
            "near_match_corpus": self.near_match_corpus,
            "verdict": self.verdict.value if self.verdict else None,
            "warnings": self.warnings,
            "attribution": self.attribution,
        }
