"""Dedup orchestration + adjudication.

Authority sources rule on validity/priority; the near-match scan and reference
sources only warn. If an authority is unavailable, adjudication is BLOCKED —
'not found' and 'could not check' must never be conflated.

Adjudication inputs (see dedup/model.py for the status vocabulary):

- OCCUPYING statuses on an AUTHORITY  → CONFLICT.
- NON-RULING statuses (unavailable, found_unknown) on an authority → BLOCKED.
  "The source answered with something we cannot classify" is deliberately in
  the same bucket as "the source did not answer": prokname would be guessing.
- Everything else that merely *flags* the name (authority found_other =
  a record that does not occupy the name, reference hits, GNA near matches,
  NCBI misspellings) → VERIFY_WARNING, never CONFLICT: over-blocking a
  name that is merely flagged is as wrong as letting a conflict through.
- Local near matches outrank a bare warning but never a conflict.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .. import source_attribution
from . import gna, lpsn, ncbi, seqcode
from .cache import get as cache_get
from .cache import put as cache_put
from .model import (
    NON_RULING_STATUSES,
    OCCUPYING_STATUSES,
    UNAVAILABLE,
    WARNING_STATUSES,
    CheckReport,
    NearMatch,
    SourceResult,
    Verdict,
)
from .nearmatch import (
    corpus_is_stale,
    load_seed_corpus,
    scan,
    scan_stemmed,
)

SCAN_MODES = ("whole", "stem", "both")

#: Adapter modules in query order, with the cache partition name.
_ADAPTERS = (
    (lpsn, "lpsn", "authority"),
    (seqcode, "seqcode", "authority"),
    (gna, "gna", "reference"),
    (ncbi, "ncbi", "reference"),
)


def check_name(
    name: str,
    *,
    online: bool = False,
    near_match: bool = True,
    max_distance: int = 2,
    near_match_mode: str = "whole",
    use_cache: bool = True,
) -> CheckReport:
    """Run the two-tier check for a candidate name and adjudicate.

    near_match_mode selects the local parahomonym scan caliber:
    "whole" (default — full-name Levenshtein at `max_distance`, the
    deliberately recall-heavy v1 warning scan), "stem" (Taxamatch/
    GNmatcher-inspired: inflectional endings stripped, tight distance 1),
    or "both" (union of the two, keeping the smallest distance per hit).

    use_cache=False skips both reading and writing the query cache; the
    default keeps the rate-limit budget behaviour, but cached answers are
    bound to the network state that produced them: an
    offline run never replays an online result, so `not_found` obtained while
    --online was in force cannot masquerade as an offline NO_CLEAR_CONFLICT.
    """
    report = CheckReport(
        query=name,
        checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
        # The same two strings the exports carry: one source of truth
        #, because an attribution line is a compliance claim.
        attribution=source_attribution(),
    )

    report.sources = []
    for adapter, source_name, tier in _ADAPTERS:
        cached = cache_get(source_name, name, online=online) if use_cache else None
        if cached is not None:
            result = SourceResult.from_dict(
                cached, default_name=source_name.upper(), default_tier=tier,
            )
            result.cached = True
            result.detail = cached.get("detail", "") + " (cached)"
            report.sources.append(result)
        else:
            if not online and use_cache and cache_get(
                source_name, name, online=True
            ) is not None:
                # Honesty about the refusal: an online answer IS cached, but
                # replaying it here would let an offline run inherit an
                # authority conclusion.
                report.warnings.append(
                    f"{source_name.upper()}: an online-cached answer exists "
                    "for this name and was NOT replayed offline; re-run with "
                    "--online to use it"
                )
            try:
                src_result = adapter.check(name, allow_network=online)
            except Exception as exc:
                # An adapter crash must degrade to an honest 'unavailable',
                # never take the whole check (or the CLI) down with it.
                src_result = SourceResult(
                    name=source_name.upper(), status=UNAVAILABLE,
                    tier=tier, detail=f"adapter crashed: {exc!r}",
                )
            src_result.online_derived = bool(online)
            report.sources.append(src_result)
            # Cache successful (non-unavailable) results, tagged with the
            # network state that produced them.
            if use_cache and src_result.status != UNAVAILABLE:
                cache_put(source_name, name, src_result.as_dict(),
                          online=bool(online))

    unverified = [
        s.name for s in report.sources
        if not s.verified and s.status != UNAVAILABLE
    ]
    if unverified:
        report.warnings.append(
            "classification of " + ", ".join(unverified) + " rests on an "
            "UNVERIFIED status table row (data/lpsn_status.json / "
            "dedup/ncbi.py): the wording below stays provisional until an "
            "M0 live-response record confirms the mapping"
        )

    if near_match:
        if near_match_mode not in SCAN_MODES:
            raise ValueError(
                f"near_match_mode must be one of {SCAN_MODES}, "
                f"got {near_match_mode!r}"
            )
        corpus, meta = load_seed_corpus()
        matches: list[NearMatch] = []
        if near_match_mode in ("whole", "both"):
            matches.extend(scan(corpus, name, max_distance=max_distance))
        if near_match_mode in ("stem", "both"):
            matches.extend(scan_stemmed(corpus, name))
        if near_match_mode == "both":
            # union: keep the smallest distance when a corpus name is hit
            # by both calibers
            best: dict[str, NearMatch] = {}
            for m in matches:
                if m.corpus_name not in best or m.distance < best[m.corpus_name].distance:
                    best[m.corpus_name] = m
            matches = list(best.values())
        report.near_matches = sorted(matches, key=lambda m: (m.distance, m.corpus_name))
        report.near_match_corpus = {
            "corpus": "lpsn_export+seqcode+taxdump (demo seed)",
            "corpus_date": meta.get("corpus_date"),
            # Honest coverage boundary: report HOW MANY names were scanned
            # and whether the source export was truncated — "no hits" from a
            # 16-name demo corpus is not evidence of absence from LPSN.
            "corpus_size": len(corpus),
            "truncated": bool(meta.get("truncated", False)),
            "provenance": meta.get("provenance"),
            "scan_mode": near_match_mode,
        }
        # A null/absent corpus_date must read as STALE, never crash.
        stale = corpus_is_stale(meta.get("corpus_date"))
        if stale:
            report.warnings.append(
                "near-match corpus is stale; rebuild from official exports "
                ""
            )
        if not report.near_matches:
            report.warnings.append(
                f"near-match corpus contains only {len(corpus)} names — absence "
                "of hits is not evidence of absence"
            )
        zero_core = [m for m in report.near_matches if m.distance == 0]
        if zero_core:
            report.warnings.append(
                "near-match hit(s) at distance 0 share the binomial core of "
                "this query (infrasubspecific or 'Candidatus' entries in the "
                "corpus): "
                + ", ".join(m.corpus_name for m in zero_core[:5])
                + " — warning only, not a ruling"
            )

    report.verdict = adjudicate(report)
    if report.verdict in (Verdict.NO_CLEAR_CONFLICT, Verdict.VERIFY_WARNING):
        report.warnings.append(
            "absence from the checked sources ≠ availability: verify manually "
            "against LPSN / SeqCode Registry before use"
        )
    if report.verdict is Verdict.BLOCKED and report.near_matches:
        # The local signal must stay visible even when the authorities could
        # not be reached: it is a WARNING, never promoted to a ruling.
        shown = ", ".join(m.corpus_name for m in report.near_matches[:5])
        report.warnings.append(
            f"local near-match scan found {len(report.near_matches)} similar "
            f"name(s) — warning only, not a ruling: {shown}"
            + (" …" if len(report.near_matches) > 5 else "")
            + "; re-run with --online for an authority-based verdict"
        )
    return report


def adjudicate(report: CheckReport) -> Verdict:
    """Five-state ruling: authorities adjudicate, reference sources only warn.

    Reachability note:
    PARAHOMONYM_WARNING, VERIFY_WARNING and NO_CLEAR_CONFLICT all require BOTH
    authorities to answer. SeqCode now answers positively from its occupancy
    snapshot, so CONFLICT became reachable for a SeqCode-published name even
    offline. It still cannot answer NEGATIVELY: measured against the registry's
    own per-name `status_name` field, the `status=SeqCode` list silently omits
    names that are valid under SeqCode, so a miss is reported as
    'found_unknown', which lives in NON_RULING_STATUSES exactly like
    'unavailable'. A real `check_name()` run on an unoccupied name therefore
    still lands on BLOCKED until LPSN can answer negatively too. The three
    clean states are pinned by tests/test_orchestrator.py with a stubbed
    authority pair, and NOT by a live run — do not read a green suite as proof
    that they are reachable end-to-end yet.
    """
    authorities = [s for s in report.sources if s.tier == "authority"]

    conflicts = [
        s for s in authorities if s.status in OCCUPYING_STATUSES
    ]
    if conflicts:
        return Verdict.CONFLICT

    if any(s.status in NON_RULING_STATUSES for s in authorities):
        return Verdict.BLOCKED

    if report.near_matches:
        return Verdict.PARAHOMONYM_WARNING

    # Anything that merely flags usage — reference hits, or an authority
    # record that does NOT occupy the name (variant / in preparation /
    # non-validly published) — warns; it is not a ruling.
    # A reference-tier `found_synonym` also warns: reference sources are not
    # authorities, so even their strongest "this name is used as a synonym"
    # answer may only ever raise VERIFY_WARNING.
    flags = [
        s for s in report.sources
        if s.status in WARNING_STATUSES
        or (s.tier == "reference" and s.status.startswith("found_"))
    ]
    if flags:
        return Verdict.VERIFY_WARNING

    return Verdict.NO_CLEAR_CONFLICT
