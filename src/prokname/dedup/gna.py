"""Reference-source adapter: GNA GNverifier.

GNA GNverifier aggregates hundreds of authority databases and provides a
broad cross-reference for whether a name is in use across public sequence /
taxonomy databases. It is a REFERENCE source, never an authority — it cannot
rule on ICNP/SeqCode validity.

Design contract (same as LPSN/SeqCode adapters):
- Never fabricate a ruling; if the service is unavailable return 'unavailable'.
- Reference-tier: failure does NOT block adjudication (unlike authority sources).
- Network is opt-in: offline by default.

REST contract (pinned to gnames/gnverifier source commit f73968c1, local
snapshot gnames/gnverifier, and its recorded fixture
pkg/io/verifrest/fixtures/names.yaml):
- `POST https://verifier.globalnames.org/api/v1/verifications` with a JSON
  body `{"nameStrings": [...], "withAllMatches": false, ...}` (the older
  GET-with-query-params form of the previous v1 service is NOT the current
  contract; the client in the gnverifier source posts a JSON Input object).
- Response: `{"metadata": {...}, "names": [{"name", "matchType",
  "bestResult": {...}|null, "results": [...]}]}` where each result carries
  `matchedName`, `currentName`, `matchType`, `editDistance`, `isSynonym`,
  `dataSourceId`, `dataSourceTitleShort`, `taxonomicStatus`, ...
- Fixture evidence for the field names and the matchType strings actually
  emitted by the service (vendored, read-only):
  gnverifier/pkg/io/verifrest/fixtures/names.yaml:15 — an entry-level
  `"matchType":"Exact"` together with a bestResult carrying
  `"matchType":"Exact", "editDistance":0`, versus the non-matching
  `"matchType":"NoMatch"` entry, which has NO bestResult at all.
  gnmatcher/internal/testdata/testdata.csv:1-5 (columns
  name, matchType, matchedName, editDistance) shows the wider matcher-side
  vocabulary — ExactMatch / ExactCanonicalMatch / ExactPartialMatch /
  FuzzyCanonicalMatch / FuzzyPartialMatch / NoMatch — and that a
  FuzzyCanonicalMatch is a match to a *different* string (e.g.
  'Raliella subelongata' → 'Kaliella subelongata', editDistance 1).

Fuzzy matches never occupy a name:
- "In use" may only be asserted when BOTH the entry-level `matchType` and the
  best-result `matchType` are Exact and the reported `editDistance` is 0.
- Any other combination is reported as `found_near_match` — a distinctly
  labelled warning that the orchestrator can never read as occupancy.
- `editDistance` participates in that decision and in the detail text; a
  missing/None `editDistance` is rendered as "n/a" instead of the malformed
  "edit distance: )" the old code produced.
- M2 live-testing gate: CLOSED for this service. One live response was recorded
  on 2026-09-25 to tests/fixtures/gna_verifications_live.json (three cases:
  Exact / Fuzzy / NoMatch), and
  tests/test_api_fixtures.py::test_live_gna_captures_still_drive_the_real_adapter
  replays it through this parse path. It confirmed the entry-level vocabulary the
  service really emits today is Exact / Fuzzy / NoMatch (the vendored
  names.yaml sample only carried Exact and NoMatch, and the gnmatcher testdata
  lists the wider *Match spellings, which this endpoint never returns). Re-run
  `python scripts/record_live_gna.py --check` to detect a rename upstream
  before it degrades a published name into 'not found'.
"""

from __future__ import annotations

from .model import (
    FOUND_NEAR_MATCH,
    FOUND_REFERENCE,
    FOUND_SYNONYM,
    NOT_FOUND,
    UNAVAILABLE,
    SourceResult,
)

GNA_URL = "https://verifier.globalnames.org/"
VERIFICATIONS_URL = "https://verifier.globalnames.org/api/v1/verifications"
_TIER = "reference"

#: The only matchType value that licenses "this name is in use", per
#: gnverifier/pkg/io/verifrest/fixtures/names.yaml:15. Compared case-folded
#: and whitespace-collapsed, but never by substring.
EXACT_MATCH_TYPE = "exact"

#: Values seen in the vendored sources. Anything else (a renamed field, a new
#: service vocabulary) is classified as NOT exact — see _is_exact().
KNOWN_MATCH_TYPES = frozenset({
    "exact", "nomatch", "fuzzy", "partialexact", "strict", "coercesimple",
    "abovegenus", "virus",
    # gnmatcher-side spellings (testdata.csv), kept so a gnmatcher-backed
    # deployment is recognised rather than silently treated as unknown
    "exactmatch", "exactcanonicalmatch", "exactpartialmatch",
    "fuzzycanonicalmatch", "fuzzypartialmatch",
})


def _match_type(raw: object) -> str:
    return " ".join(str(raw or "").split()).casefold()


def _is_exact(raw: object) -> bool:
    """True only for an unambiguous Exact matchType.

    Unknown / missing values are non-exact on purpose: the service was never
    asked to certify occupancy of a name it could not label.
    """
    return _match_type(raw) == EXACT_MATCH_TYPE


def _format_distance(raw: object) -> str:
    """Render editDistance without ever emitting an empty placeholder."""
    if raw is None or raw == "":
        return "edit distance: n/a"
    try:
        return f"edit distance: {int(raw)}"
    except (TypeError, ValueError):
        return f"edit distance: {raw!r}"


def _distance_is_zero(raw: object) -> bool:
    """`editDistance` sanity check; an absent field cannot confirm exactness."""
    if raw is None or raw == "":
        return False
    try:
        return int(raw) == 0
    except (TypeError, ValueError):
        return False


def check(name: str, *, allow_network: bool = False) -> SourceResult:
    """Query GNA GNverifier for a name. Offline ⇒ 'unavailable' (reference tier)."""
    if not allow_network:
        return SourceResult(
            name="GNA", status=UNAVAILABLE, tier=_TIER,
            detail="offline mode (default); reference source — "
                   "failure does not block adjudication",
            url=GNA_URL,
        )
    try:
        import httpx
    except ImportError:
        return SourceResult(
            name="GNA", status=UNAVAILABLE, tier=_TIER,
            detail=(
                "httpx not installed; install the online extra "
                "(pip install -e '.[online]') or httpx"
            ),
            url=GNA_URL,
            online_derived=True,
        )
    try:
        resp = httpx.post(
            VERIFICATIONS_URL,
            json={
                "nameStrings": [name],
                "withAllMatches": False,
                "withVernaculars": False,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        names = data.get("names", []) if isinstance(data, dict) else []
        if not names:
            return SourceResult(
                name="GNA", status=NOT_FOUND, tier=_TIER,
                detail="no GNA match", url=GNA_URL, online_derived=True,
            )
        entry = names[0] if isinstance(names[0], dict) else {}
        entry_match = entry.get("matchType")
        best = entry.get("bestResult")
        if not isinstance(best, dict) or not best:
            # names.yaml:15 shows a NoMatch entry carries no bestResult at all.
            return SourceResult(
                name="GNA", status=NOT_FOUND, tier=_TIER,
                detail=(
                    f"no GNA match (entry matchType={_match_type(entry_match) or 'n/a'})"
                ),
                url=GNA_URL, online_derived=True,
            )
    except Exception as exc:
        return SourceResult(
            name="GNA", status=UNAVAILABLE, tier=_TIER,
            detail=f"GNA query failed: {exc!r} (reference source — non-blocking)",
            url=GNA_URL,
            online_derived=True,
        )

    matched = best.get("matchedName", "")
    current = best.get("currentName") or matched
    source_title = best.get("dataSourceTitleShort", "") or "unknown source"
    best_match = best.get("matchType")
    distance = best.get("editDistance")

    exact_entry = _is_exact(entry_match)
    exact_best = _is_exact(best_match)
    zero_distance = _distance_is_zero(distance)
    # Occupancy is only ever asserted on a full Exact × Exact × distance-0
    # agreement between the entry and its best result.
    exact = exact_entry and exact_best and zero_distance

    via = f" via {source_title}" if source_title else ""
    detail = (
        f"GNA entry matchType={_match_type(entry_match) or 'n/a'}, "
        f"best matchType={_match_type(best_match) or 'n/a'}, "
        f"{_format_distance(distance)}{via}: {current or '-'} "
        f"(matched: {matched or '-'}; {_identity_phrase(matched, name)})"
    )
    if exact:
        status = FOUND_SYNONYM if best.get("isSynonym") else FOUND_REFERENCE
        return SourceResult(
            name="GNA", status=status, tier=_TIER, detail=detail, url=GNA_URL,
            online_derived=True,
            verified=_match_type(best_match) in KNOWN_MATCH_TYPES,
        )
    # Fuzzy / partial / unknown match ⇒ the service matched SOME OTHER NAME to
    # the query. That is a similarity warning, never "this name is in use".
    why = "not Exact"
    if exact_entry and exact_best and not zero_distance:
        why = "labelled Exact but editDistance is not 0 (self-contradictory payload)"
    elif not exact_best:
        why = "best-result matchType is not Exact"
    elif not exact_entry:
        why = "entry-level matchType is not Exact"
    return SourceResult(
        name="GNA", status=FOUND_NEAR_MATCH, tier=_TIER,
        detail=(
            f"{detail} — NEAR MATCH ONLY ({why}); GNA matched a string that "
            "is not this name, so it does NOT occupy the query name"
        ),
        url=GNA_URL, online_derived=True, verified=False,
    )


def _canonical(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _identity_phrase(matched: object, query: str) -> str:
    """Whether the matched string is the query itself (modulo case/space)."""
    return (
        "matched string equals the query"
        if _canonical(matched) == _canonical(query)
        else "matched string differs from the query"
    )
