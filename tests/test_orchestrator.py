"""Unit tests: dedup orchestration & adjudication.

Coverage notes tied to the 2026-09-19 review:
- M3: the three "reachable only by hand-rolling a CheckReport" verdicts are
  now exercised END TO END through `check_name()` with a stubbed authority
  pair (the real SeqCode adapter is still M0-gated, so stubbing is the only
  honest way to reach them until that gate opens).
- M4: an authority record that does NOT occupy the name (`found_other`) warns;
  it no longer forces a conflict. An unclassifiable authority answer blocks.
- Cached answers are bound to the network state that produced them.
- A null corpus date must not crash the check.
"""

import json

import pytest

from prokname.dedup import Verdict, check_name, orchestrator
from prokname.dedup.model import (
    CheckReport,
    NearMatch,
    SourceResult,
)
from prokname.dedup.orchestrator import adjudicate


def _report(sources, near=None):
    return CheckReport(
        query="X", checked_at="now", sources=sources,
        near_matches=near or [],
    )


def _authority(status, name="LPSN", **kw):
    return SourceResult(name=name, status=status, tier="authority", **kw)


def _reference(status, name="GNA", **kw):
    return SourceResult(name=name, status=status, tier="reference", **kw)


def _stub(monkeypatch, **by_source):
    """Replace adapter `check` functions on the orchestrator's module refs."""
    for source, result in by_source.items():
        module = getattr(orchestrator, source)

        def _make(res=result):
            def _check(_name, *, allow_network=False, **_kw):
                return SourceResult(
                    name=res.name, status=res.status, tier=res.tier,
                    detail=res.detail or "stubbed", url=res.url,
                    online_derived=bool(allow_network), verified=res.verified,
                )
            return _check

        monkeypatch.setattr(module, "check", _make())


CLEAN_FAR_QUERY = "Xylophonius zqwbtrkp"  # no near match in the 16-name seed


# ---------------------------------------------------------------------------
# Offline default behaviour
# ---------------------------------------------------------------------------

def test_offline_check_blocks_rather_than_faking_not_found(isolated_cache):
    """Offline must block, and no source may claim the name is free.

    SeqCode is a snapshot-backed authority, so 'everything says unavailable' is
    not the true description of an offline run. What must still hold is the
    nothing rules 'not_found', every authority is non-ruling, and the verdict
    is BLOCKED.
    """
    from prokname.dedup.model import NON_RULING_STATUSES, NOT_FOUND

    report = check_name(CLEAN_FAR_QUERY, online=False)
    authorities = [s for s in report.sources if s.tier == "authority"]
    assert authorities, "offline run carried no authority source at all"
    assert all(s.status != NOT_FOUND for s in report.sources), (
        [s.status for s in report.sources]
    )
    assert all(s.status in NON_RULING_STATUSES for s in authorities), (
        [(s.name, s.status) for s in authorities]
    )
    assert all(not s.online_derived for s in report.sources)
    assert report.verdict is Verdict.BLOCKED
    assert report.attribution["LPSN"].startswith("CC BY-SA 4.0")


def test_seqcode_snapshot_hit_conflicts_without_network(isolated_cache):
    """A SeqCode-published name is a CONFLICT even offline (2026-09-25).

    Before the snapshot existed this call returned BLOCKED for every input.
    The positive half of the adapter is now real capability, so pin it here at
    the orchestrator level too, not just in the adapter tests.
    """
    import json
    from pathlib import Path

    from prokname.dedup.seqcode import SNAPSHOT_ASSET, snapshot_info

    if not snapshot_info().get("present"):
        pytest.skip("no SeqCode snapshot; rebuild with "
                    "python scripts/build_seqcode_snapshot.py")
    asset = (Path(__file__).resolve().parent.parent
             / "src" / "prokname" / "data" / SNAPSHOT_ASSET)
    row = json.loads(asset.read_text(encoding="utf-8"))["names"][0]
    report = check_name(row["name"], online=False, near_match=False)
    assert report.verdict is Verdict.CONFLICT, (
        f"{row['name']}: snapshot says registered, verdict was "
        f"{report.verdict.value}")
    seqcode_result = next(s for s in report.sources if s.name == "SeqCode")
    assert seqcode_result.status == "found_valid"
    assert not seqcode_result.online_derived


# ---------------------------------------------------------------------------
# adjudicate() unit behaviour (status vocabulary)
# ---------------------------------------------------------------------------

def test_authority_hit_rules_conflict():
    r = _report([
        _authority("found_valid"),
        _authority("not_found", name="SeqCode"),
    ])
    assert adjudicate(r) is Verdict.CONFLICT


@pytest.mark.parametrize("status", ["found_occupied", "found_synonym"])
def test_occupied_but_incorrect_names_also_conflict(status):
    """'synonym' / 'later homonym' occupy the string: still a conflict."""
    r = _report([_authority(status), _authority("not_found", name="SeqCode")])
    assert adjudicate(r) is Verdict.CONFLICT


def test_authority_unavailable_blocks_even_with_clean_reference():
    r = _report([
        _authority("unavailable"),
        _authority("not_found", name="SeqCode"),
    ])
    assert adjudicate(r) is Verdict.BLOCKED


def test_unclassifiable_authority_answer_blocks_instead_of_guessing():
    """M4/M3: `found_unknown` is as un-ruling as `unavailable`."""
    r = _report([
        _authority("found_unknown", detail="LPSN label 'brand new status'"),
        _authority("not_found", name="SeqCode"),
    ])
    assert adjudicate(r) is Verdict.BLOCKED


def test_non_occupying_authority_record_is_a_warning_not_a_conflict():
    """M4, over-blocking half: 'variant' / 'in preparation' free the name."""
    r = _report([
        _authority("found_other", detail="category=unoccupied_record"),
        _authority("not_found", name="SeqCode"),
    ])
    assert adjudicate(r) is Verdict.VERIFY_WARNING


def test_near_match_alone_warns_without_ruling():
    r = _report(
        [_authority("not_found"), _authority("not_found", name="SeqCode")],
        near=[NearMatch("Wukomonas beijingense", 1, "demo")],
    )
    assert adjudicate(r) is Verdict.PARAHOMONYM_WARNING


def test_clean_pass_still_demands_manual_verification():
    clean = _report([
        _authority("not_found"), _authority("not_found", name="SeqCode"),
    ])
    assert adjudicate(clean) is Verdict.NO_CLEAR_CONFLICT


@pytest.mark.parametrize("status", [
    "found_reference", "found_synonym", "found_other", "found_near_match",
    "found_parahomonym",
])
def test_reference_flags_warn_but_never_conflict(status):
    """Reference-tier answers may only ever warn."""
    r = _report([
        _authority("not_found"), _authority("not_found", name="SeqCode"),
        _reference(status),
    ])
    assert adjudicate(r) is Verdict.VERIFY_WARNING


def test_adapter_crash_degrades_to_unavailable(monkeypatch, isolated_cache):
    def boom(_name, *, allow_network=False, **_kw):
        raise ValueError("adapter exploded")

    monkeypatch.setattr(orchestrator.lpsn, "check", boom)
    report = check_name(CLEAN_FAR_QUERY, online=False, near_match=False)
    lpsn_rows = [s for s in report.sources if s.name.upper() == "LPSN"]
    assert lpsn_rows[0].status == "unavailable"
    assert "adapter crashed" in lpsn_rows[0].detail
    assert report.verdict is Verdict.BLOCKED


# ---------------------------------------------------------------------------
# M3: end-to-end reachability of the three "unreachable" verdicts
# ---------------------------------------------------------------------------

def test_e2e_parahomony_warning_through_check_name(monkeypatch, isolated_cache):
    _stub(monkeypatch,
          lpsn=_authority("not_found"),
          seqcode=_authority("not_found", name="SeqCode"),
          gna=_reference("not_found"),
          ncbi=_reference("not_found", name="NCBI"))
    report = check_name("Wukomonas beijingense", online=True)
    assert report.verdict is Verdict.PARAHOMONYM_WARNING
    assert any(m.corpus_name == "Wukomonas beijingensis"
               for m in report.near_matches)
    # the local signal warns; it is never promoted to a conflict
    assert report.verdict is not Verdict.CONFLICT
    assert all(s.status == "not_found" for s in report.sources
               if s.tier == "authority")


def test_e2e_verify_warning_through_check_name(monkeypatch, isolated_cache):
    _stub(monkeypatch,
          lpsn=_authority("not_found"),
          seqcode=_authority("not_found", name="SeqCode"),
          gna=_reference("found_near_match",
                         detail="NEAR MATCH ONLY (matchType is not Exact)"),
          ncbi=_reference("not_found", name="NCBI"))
    report = check_name(CLEAN_FAR_QUERY, online=True)
    assert report.verdict is Verdict.VERIFY_WARNING
    assert any(s.status == "found_near_match" for s in report.sources)
    assert any("≠ availability" in w for w in report.warnings)


def test_e2e_no_clear_conflict_through_check_name(monkeypatch, isolated_cache):
    _stub(monkeypatch,
          lpsn=_authority("not_found"),
          seqcode=_authority("not_found", name="SeqCode"),
          gna=_reference("not_found"),
          ncbi=_reference("not_found", name="NCBI"))
    report = check_name(CLEAN_FAR_QUERY, online=True)
    assert report.verdict is Verdict.NO_CLEAR_CONFLICT
    assert report.near_matches == []
    # the honest caveat must survive the favourable verdict
    assert any("absence from the checked sources" in w for w in report.warnings)


def test_e2e_conflict_through_check_name(monkeypatch, isolated_cache):
    _stub(monkeypatch,
          lpsn=_authority("found_valid"),
          seqcode=_authority("not_found", name="SeqCode"))
    report = check_name("Wukomonas beijingense", online=True)
    assert report.verdict is Verdict.CONFLICT


def test_unverified_classification_is_disclosed_in_warnings(monkeypatch,
                                                            isolated_cache):
    _stub(monkeypatch,
          lpsn=_authority("found_other", verified=False),
          seqcode=_authority("not_found", name="SeqCode"))
    report = check_name(CLEAN_FAR_QUERY, online=True, near_match=False)
    assert any("UNVERIFIED status table row" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# The cache may not smuggle an online answer into an offline run
# ---------------------------------------------------------------------------

def test_online_not_found_is_not_replayed_offline(monkeypatch, tmp_path,
                                                  isolated_cache):
    """The hole: online `not_found` cached → offline run replays it → verdict
    silently upgrades from BLOCKED to NO_CLEAR_CONFLICT."""
    calls = {"n": 0}

    def online_not_found(_name, *, allow_network=False, **_kw):
        """Behaves like the real adapter: an offline call cannot answer."""
        calls["n"] += 1
        if not allow_network:
            return SourceResult(name="LPSN", status="unavailable",
                                tier="authority", detail="offline mode")
        return SourceResult(
            name="LPSN", status="not_found", tier="authority",
            detail="no LPSN record for this name",
        )

    monkeypatch.setattr(orchestrator.lpsn, "check", online_not_found)
    monkeypatch.setattr(orchestrator.seqcode, "check",
                        lambda *_a, **_k: _authority("unavailable"))
    monkeypatch.setattr(orchestrator.gna, "check",
                        lambda *_a, **_k: _reference("unavailable"))
    monkeypatch.setattr(orchestrator.ncbi, "check",
                        lambda *_a, **_k: _reference("unavailable"))

    online_report = check_name(CLEAN_FAR_QUERY, online=True, near_match=False)
    assert online_report.sources[0].status == "not_found"
    assert online_report.sources[0].online_derived is True
    assert (isolated_cache / "lpsn").exists(), "online answer was cached"

    # The offline run must NOT inherit that answer.
    offline = check_name(CLEAN_FAR_QUERY, online=False, near_match=False)
    lpsn_row = [s for s in offline.sources if s.name == "LPSN"][0]
    assert lpsn_row.status == "unavailable"
    assert lpsn_row.cached is False
    assert offline.verdict is Verdict.BLOCKED
    assert any("was NOT replayed offline" in w for w in offline.warnings)
    assert calls["n"] == 2, "the adapter must have been consulted again"


def test_online_run_still_reuses_its_own_cache_with_annotation(monkeypatch,
                                                                isolated_cache):
    hits = {"n": 0}

    def counting(_name, *, allow_network=False, **_kw):
        hits["n"] += 1
        return SourceResult(name="LPSN", status="not_found", tier="authority",
                            detail="cached me")

    monkeypatch.setattr(orchestrator.lpsn, "check", counting)
    monkeypatch.setattr(orchestrator.seqcode, "check",
                        lambda *_a, **_k: _authority("unavailable"))
    monkeypatch.setattr(orchestrator.gna, "check",
                        lambda *_a, **_k: _reference("unavailable"))
    monkeypatch.setattr(orchestrator.ncbi, "check",
                        lambda *_a, **_k: _reference("unavailable"))

    first = check_name(CLEAN_FAR_QUERY, online=True, near_match=False)
    second = check_name(CLEAN_FAR_QUERY, online=True, near_match=False)
    assert hits["n"] == 1
    row = [s for s in second.sources if s.name == "LPSN"][0]
    assert row.cached is True
    assert row.detail.endswith("(cached)")
    assert row.online_derived is True
    assert first.verdict is second.verdict


def test_use_cache_false_skips_the_store(monkeypatch, isolated_cache):
    calls = {"n": 0}

    def counting(_name, *, allow_network=False, **_kw):
        calls["n"] += 1
        return SourceResult(name="LPSN", status="not_found", tier="authority")

    monkeypatch.setattr(orchestrator.lpsn, "check", counting)
    for _ in range(2):
        check_name(CLEAN_FAR_QUERY, online=True, near_match=False,
                   use_cache=False)
    assert calls["n"] == 2
    assert not (isolated_cache / "lpsn").exists()


# ---------------------------------------------------------------------------
# A null corpus_date may not crash the check
# ---------------------------------------------------------------------------

def test_null_corpus_date_is_treated_as_stale(monkeypatch, isolated_cache):
    import prokname.dedup.orchestrator as orch

    monkeypatch.setattr(
        orch, "load_seed_corpus",
        lambda: ([{"name": "Wukomonas beijingensis", "source": "t"}],
                 {"corpus_date": None}),
    )
    report = check_name("Wukomonas beijingense", online=False)  # no TypeError
    assert report.verdict is Verdict.BLOCKED
    assert any("corpus is stale" in w for w in report.warnings)


def test_garbage_corpus_date_is_treated_as_stale(monkeypatch, isolated_cache):
    import prokname.dedup.orchestrator as orch

    monkeypatch.setattr(
        orch, "load_seed_corpus",
        lambda: ([{"name": "Wukomonas beijingensis", "source": "t"}],
                 {"corpus_date": "not-a-date"}),
    )
    report = check_name("Wukomonas beijingense", online=False)
    assert any("corpus is stale" in w for w in report.warnings)


def test_missing_corpus_date_key_is_treated_as_stale(monkeypatch,
                                                     isolated_cache):
    import prokname.dedup.orchestrator as orch

    monkeypatch.setattr(
        orch, "load_seed_corpus",
        lambda: ([{"name": "Wukomonas beijingensis", "source": "t"}], {}),
    )
    report = check_name("Wukomonas beijingense", online=False)
    assert any("corpus is stale" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Scan calibers: whole (default) / stem / both
# ---------------------------------------------------------------------------

def test_default_scan_mode_is_whole_and_unchanged(isolated_cache):
    report = check_name("Wukomonas beijingense", online=False)
    assert report.near_match_corpus["scan_mode"] == "whole"
    # whole-name threshold 2 still catches the suffix variant
    assert any(m.corpus_name == "Wukomonas beijingensis" for m in report.near_matches)


def test_stem_mode_flags_stem_equal_pair_at_distance_zero(isolated_cache):
    report = check_name(
        "Wukomonas beijingense", online=False, near_match_mode="stem",
    )
    assert report.near_match_corpus["scan_mode"] == "stem"
    hit = next(
        m for m in report.near_matches if m.corpus_name == "Wukomonas beijingensis"
    )
    assert hit.distance == 0  # suffix-only difference collapses in stem space


def test_both_mode_unions_and_dedupes_hits(isolated_cache):
    # both calibers hit the same corpus name with different distances:
    # whole-name 1 vs stemmed 0 — the union keeps the smaller distance once
    report = check_name("Escherichia colii", online=False, near_match_mode="both")
    assert report.near_match_corpus["scan_mode"] == "both"
    hits = [m for m in report.near_matches if m.corpus_name == "Escherichia coli"]
    assert len(hits) == 1
    assert hits[0].distance == 0
    distances = [m.distance for m in report.near_matches]
    assert distances == sorted(distances)


def test_invalid_scan_mode_raises(isolated_cache):
    with pytest.raises(ValueError):
        check_name("Escherichia coli", online=False, near_match_mode="fuzzy")


def test_core_view_hits_are_disclosed_as_distance_zero(isolated_cache):
    """a corpus trinomial that embeds the query is reported, and the
    report says out loud that a distance-0 core hit is a warning only."""
    report = check_name("Bacillus subtilis", online=False)
    hit = next(m for m in report.near_matches
               if m.corpus_name == "Bacillus subtilis subsp. spizizenii")
    assert hit.distance == 0
    assert hit.caliber == "whole-core"
    assert any("distance 0 share the binomial core" in w for w in report.warnings)


def test_report_serialisation_round_trips_new_fields(isolated_cache):
    """`prokname check --json` consumers must keep getting every field."""
    report = check_name(CLEAN_FAR_QUERY, online=False)
    payload = json.loads(json.dumps(report.as_dict()))
    assert set(payload) == {
        "query", "checked_at", "sources", "near_matches", "near_match_corpus",
        "verdict", "warnings", "attribution",
    }
    assert {"name", "status", "tier", "detail", "url", "cached",
            "online_derived", "verified"} <= set(payload["sources"][0])
