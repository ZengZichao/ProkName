"""API fixture tests: offline adapter behaviour + fault tolerance.

Honest status of the "recorded live responses" story:

- No test in this module touches the network, and none uses the ``vcr``
  fixture: the live capture below is replayed from a recorded JSON file.
- ``tests/fixtures/`` carries source-derived fixtures for the LPSN client
  contract, the LPSN status enumeration, the GNA GNverifier response schema and
  a synthetic NCBI names.dmp, plus ONE genuine capture from a running service
  (``gna_verifications_live.json``, recorded by ``scripts/record_live_gna.py``).
  Each file states which source it came from and whether it is live;
  ``manifest.json`` records the rest, and ``test_live_captures_are_replayable``
  below replays the live one through the real adapter parse path.
- Live captures for LPSN (credentials required) and SeqCode are still
  outstanding; the ``vcr`` scaffold lives in tests/conftest.py.

What IS tested here, without any network:
- every adapter honestly returns 'unavailable' when offline,
- unavailable authorities BLOCK adjudication,
- a reference hit alongside not-found authorities yields VERIFY_WARNING,
  never CONFLICT,
- simulated network failures never crash the orchestrator,
- the live GNA capture still drives the adapter to the same verdicts the
  service gave on the day it was recorded (M2 gate in dedup/gna.py),
- the fixtures under tests/fixtures/ are actually consumed: each one is
  replayed through the REAL adapter parse path (lpsn.classify_status for the
  status-label enumeration, ncbi.check for the synthetic names.dmp), so a
  renamed upstream field or a drifted status label breaks a test instead of
  silently turning published names into "not found", and
  manifest.json is checked for honest provenance (a fixture may only claim
  live_capture if its own _provenance names the recorder, endpoint and time)
  and for the fixtures it lists being referenced by a test.

The GNA and LPSN retrieve/search fixtures are replayed through the online
adapters (against stubbed clients) in tests/test_online_adapters.py.
"""

import importlib.util

import pytest

VCR_AVAILABLE = importlib.util.find_spec("vcr") is not None

# NOTE: do NOT skip this module when vcrpy is missing — these tests are all
# offline and must always run. The vcrpy presence flag is kept only so the
# future cassette-replay tests can gate on it.


# ---------------------------------------------------------------------------
# Offline behaviour (always works, no cassettes needed)
# ---------------------------------------------------------------------------

def test_lpsn_offline_returns_unavailable():
    """LPSN adapter must return 'unavailable' when offline (never fake 'not found')."""
    from prokname.dedup.lpsn import check
    result = check("Escherichia coli", allow_network=False)
    assert result.status == "unavailable"
    assert result.tier == "authority"
    assert result.url == "https://lpsn.dsmz.de/"


def test_seqcode_offline_never_claims_the_name_is_free():
    """SeqCode rules from its local snapshot, and only in one direction.

    Rewritten 2026-09-25. This test used to assert 'unavailable' for every
    offline query, when the adapter was a stub. It now consults
    data/seqcode_registered.json, so the invariant that actually matters is
    weaker in form and stronger in substance: a name the snapshot does not
    carry must come back as something that BLOCKS adjudication
    ('found_unknown'), never as 'not_found' — because the registry's
    status=SeqCode list was measured to omit names that are in fact valid
    under SeqCode (docs/provenance/seqcode-registry-2026-09-25.md).
    """
    from prokname.dedup.model import (
        NON_RULING_STATUSES,
        NOT_FOUND,
        SourceResult,
    )
    from prokname.dedup.seqcode import check

    result = check("Zhweiya fictitia nonexistia", allow_network=False)
    assert result.tier == "authority"
    assert result.status != NOT_FOUND, (
        "SeqCode returned a clean 'not found' from a list that is known to be "
        "incomplete — this is exactly the false-clean ruling the gate exists "
        "to prevent"
    )
    assert result.status in NON_RULING_STATUSES, result.status
    assert result.verified is False
    assert "NOT a ruling" in result.detail
    assert isinstance(result, SourceResult)


def test_seqcode_snapshot_hit_rules_a_conflict_offline():
    """The positive direction is real capability: no network needed."""
    import json
    from pathlib import Path

    from prokname.dedup.model import FOUND_VALID, OCCUPYING_STATUSES
    from prokname.dedup.seqcode import SNAPSHOT_ASSET, check, snapshot_info

    if not snapshot_info().get("present"):
        pytest.skip(
            "no SeqCode snapshot installed; rebuild with "
            "python scripts/build_seqcode_snapshot.py"
        )
    asset = (Path(__file__).resolve().parent.parent
             / "src" / "prokname" / "data" / SNAPSHOT_ASSET)
    rows = json.loads(asset.read_text(encoding="utf-8"))["names"]
    for row in rows[:3]:
        result = check(row["name"], allow_network=False)
        assert result.status == FOUND_VALID, (
            f"{row['name']}: in the occupancy snapshot but not ruled occupying")
        assert result.status in OCCUPYING_STATUSES
        assert result.verified is True
        assert result.online_derived is False, (
            "a local snapshot answer must never be marked as live-derived")
        assert "status_name" in result.detail  # says what it stands on
        assert row["uri"] in result.detail      # and is attributable


def test_seqcode_without_snapshot_is_unavailable_not_not_found(monkeypatch):
    """A missing asset degrades to 'could not ask', never to 'free'."""
    from prokname.dedup import seqcode as seq_mod

    monkeypatch.setattr(seq_mod, "_snapshot", lambda: None)
    result = seq_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "unavailable"
    assert "build_seqcode_snapshot.py" in result.detail
    assert "must not" in result.detail or "must not\n" in result.detail


def test_gna_offline_returns_unavailable():
    """GNA reference adapter must return 'unavailable' when offline (non-blocking)."""
    from prokname.dedup.gna import check
    result = check("Escherichia coli", allow_network=False)
    assert result.status == "unavailable"
    assert result.tier == "reference"


def test_ncbi_offline_no_taxdump_returns_unavailable():
    """NCBI adapter must return 'unavailable' when offline with no taxdump."""
    import os
    # Ensure no taxdump is configured
    old = os.environ.pop("PROKNAME_TAXDUMP_DIR", None)
    try:
        from prokname.dedup.ncbi import check
        result = check("Escherichia coli", allow_network=False)
        assert result.status == "unavailable"
        assert result.tier == "reference"
    finally:
        if old:
            os.environ["PROKNAME_TAXDUMP_DIR"] = old


# ---------------------------------------------------------------------------
# Fault-tolerance tests (simulated failures)
# ---------------------------------------------------------------------------

def test_authority_unavailable_blocks_adjudication():
    """When all authorities are unavailable, the verdict must be BLOCKED."""
    from prokname.dedup import Verdict, check_name
    report = check_name("Somegenus somespecies", online=False)
    assert report.verdict is Verdict.BLOCKED
    # and the honest coverage boundary is disclosed when the local scan
    # found nothing (its corpus is tiny)
    if not report.near_matches:
        assert any(
            "absence of hits is not evidence of absence" in w for w in report.warnings
        )


def test_reference_source_failure_does_not_block():
    """Reference source unavailability must not block adjudication."""
    from prokname.dedup.model import CheckReport, SourceResult, Verdict
    from prokname.dedup.orchestrator import adjudicate
    # Both authorities unavailable + one reference unavailable → BLOCKED (authorities)
    report = CheckReport(
        query="test",
        checked_at="now",
        sources=[
            SourceResult(name="LPSN", status="unavailable", tier="authority"),
            SourceResult(name="SeqCode", status="unavailable", tier="authority"),
            SourceResult(name="GNA", status="unavailable", tier="reference"),
            SourceResult(name="NCBI", status="unavailable", tier="reference"),
        ],
    )
    assert adjudicate(report) is Verdict.BLOCKED


def test_authorities_not_found_with_reference_hit_warns():
    """Authorities 'not_found' + reference 'found' → VERIFY_WARNING (not CONFLICT)."""
    from prokname.dedup.model import CheckReport, SourceResult, Verdict
    from prokname.dedup.orchestrator import adjudicate
    report = CheckReport(
        query="test",
        checked_at="now",
        sources=[
            SourceResult(name="LPSN", status="not_found", tier="authority"),
            SourceResult(name="SeqCode", status="not_found", tier="authority"),
            SourceResult(name="GNA", status="found_reference", tier="reference"),
            SourceResult(name="NCBI", status="unavailable", tier="reference"),
        ],
    )
    assert adjudicate(report) is Verdict.VERIFY_WARNING


def test_network_error_does_not_crash(monkeypatch):
    """A network error in any adapter must not crash the orchestrator."""
    # Simulate hard network failures without touching the real network:
    # the online code paths must convert exceptions into honest
    # 'unavailable' statuses, never raise.
    import prokname.dedup.gna as gna_mod
    from prokname.dedup import check_name

    def _boom(name, *, allow_network=False, **kwargs):
        raise ConnectionError("simulated network outage")

    monkeypatch.setattr(gna_mod, "check", _boom)
    report = check_name("Totally unknown name", online=True, near_match=False)
    assert report.verdict is not None
    assert report.verdict.value == "blocked"


# ---------------------------------------------------------------------------
# Contract pins: replay the fixtures through the REAL
# adapter parse path, so upstream drift breaks a test instead of silently
# turning a published name into "not found". These exercise the same functions
# production calls — lpsn.classify_status() and ncbi.check()'s offline parser.
# ---------------------------------------------------------------------------

def test_lpsn_status_labels_fixture_is_classified_by_the_real_adapter(load_fixture):
    """lpsn_status_labels.json -> dedup.lpsn.classify_status (the parse path).

    Every pinned upstream status label must resolve to a DEFINITE category.
    If data/lpsn_status.json ever loses or renames one, the exact lookup falls
    through to 'found_unknown' and this test goes red — that is precisely the
    silent-drift mode this suite refuses to leave unpinned.
    """
    from prokname.dedup import lpsn as lpsn_mod

    labels = [row["value"]
              for row in load_fixture("lpsn_status_labels.json")["labels"]]
    assert labels, "status-label fixture carries no values"
    for label in labels:
        result = lpsn_mod.classify_status(label)
        assert result.status != "found_unknown", (
            f"{label!r} is pinned as an upstream LPSN status but no longer "
            "classifies — data/lpsn_status.json has drifted"
        )
        assert result.category in {
            "occupied_valid", "occupied_other", "unoccupied_record",
        }, label


def test_lpsn_negated_status_labels_never_read_as_valid(load_fixture):
    """The two 'not/non-validly published' labels.

    A substring test returns VALID for both because they contain
    'validly published'. Routed through the real classifier they must not.
    """
    from prokname.dedup import lpsn as lpsn_mod
    from prokname.dedup.model import FOUND_VALID

    negated = [
        row["value"] for row in load_fixture("lpsn_status_labels.json")["labels"]
        if "validly published" in row["value"].lower()
    ]
    assert negated, "fixture lost its negated-label rows"
    for label in negated:
        result = lpsn_mod.classify_status(label)
        assert result.status != FOUND_VALID, label
        assert result.occupies is not True, label


@pytest.fixture
def ncbi_query(ncbi_taxdump_dir, monkeypatch):
    """A callable that queries the synthetic names.dmp OFFLINE via ncbi.check.

    The pickled index sidecar is disabled so no test writes to the user cache
    dir, and PROKNAME_TAXDUMP_DIR is cleared so no ambient config leaks in —
    the taxdump path is passed explicitly.
    """
    monkeypatch.setenv("PROKNAME_TAXDUMP_INDEX", "0")
    monkeypatch.delenv("PROKNAME_TAXDUMP_DIR", raising=False)
    from prokname.dedup import ncbi as ncbi_mod

    def _query(name):
        return ncbi_mod.check(name, allow_network=False,
                              taxdump_dir=str(ncbi_taxdump_dir))
    return _query


def test_ncbi_authority_is_not_read_as_a_synonym(ncbi_query):
    """name_class 'authority' is an author citation, not a synonym.

    'Syntheticus undecimus' carries BOTH an authority and a synonym row; the
    authority outranks the synonym, so the merged answer is a bare
    non-occupying record — never found_synonym.
    """
    result = ncbi_query("Syntheticus undecimus")
    assert result.status == "found_other", result.detail
    assert result.verified is False


def test_ncbi_misspelling_is_the_parahomonym_signal(ncbi_query):
    """'misspelling' is exactly the one-letter-apart case."""
    assert ncbi_query("Syntheticus mentitus").status == "found_parahomonym"


def test_ncbi_multiple_rows_merge_by_priority_not_file_order(ncbi_query):
    """'Syntheticus octavus' lists misspelling BEFORE the
    scientific-name row; a first-line answer would report a parahomonym, but
    the priority merge must surface the genuine usage record instead.
    """
    result = ncbi_query("Syntheticus octavus")
    assert result.status == "found_reference", result.detail
    assert "merged by name_class priority" in result.detail


def test_ncbi_unknown_name_class_degrades_without_guessing(ncbi_query):
    """An unrecognised name_class must degrade, never be interpreted."""
    result = ncbi_query("Syntheticus decimus")  # class: 'nonsense class'
    assert result.status == "found_other"
    assert result.verified is False


def test_ncbi_absent_name_is_not_found(ncbi_query):
    assert ncbi_query("Notanorganism nowherei").status == "not_found"


# ---------------------------------------------------------------------------
# Live capture (M2 gate in dedup/gna.py): replay the recorded GNverifier
# responses through the REAL parse path. This is the one fixture set captured
# from a running service, so it is what tells us the adapter's assumed schema
# is still the schema upstream emits — and it will break loudly on a rename
# instead of quietly turning a published name into "not found".
# ---------------------------------------------------------------------------

LIVE_GNA = "gna_verifications_live.json"


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_live_gna_captures_still_drive_the_real_adapter(load_fixture, monkeypatch):
    """Every recorded live response must reproduce its pinned verdict offline."""
    import httpx

    from prokname.dedup import gna as gna_mod
    from prokname.dedup.model import (
        FOUND_NEAR_MATCH,
        FOUND_REFERENCE,
        NOT_FOUND,
        OCCUPYING_STATUSES,
    )

    captures = load_fixture(LIVE_GNA)["captures"]
    assert captures, "live capture file carries no responses"
    by_query = {row["query"]: row for row in captures}

    def _fake_post(url, json=None, timeout=None, **kwargs):  # noqa: ANN001,ARG001
        row = by_query[json["nameStrings"][0]]
        return _StubResponse(row["response"])

    monkeypatch.setattr(httpx, "post", _fake_post)

    expected = {"found_reference": FOUND_REFERENCE,
                "found_near_match": FOUND_NEAR_MATCH,
                "not_found": NOT_FOUND}
    for row in captures:
        result = gna_mod.check(row["query"], allow_network=True)
        assert result.status == expected[row["expected_status"]], (
            f"{row['query']!r}: the live service was recorded answering "
            f"{row['expected_status']!r}, but the adapter now derives "
            f"{result.status!r} from the very same payload — {row['why_this_case']}"
        )
    # that finding, restated against real data rather than a hand-written sample:
    fuzzy = gna_mod.check("Escerichia coli", allow_network=True)
    assert fuzzy.status == FOUND_NEAR_MATCH, fuzzy.detail
    assert fuzzy.status not in OCCUPYING_STATUSES, (
        "a Fuzzy GNverifier hit must never read as occupancy"
    )
    assert "NEAR MATCH ONLY" in fuzzy.detail, fuzzy.detail


def test_live_gna_capture_records_the_field_names_it_pins(load_fixture):
    """An upstream rename of anything gna.check() reads must fail here first."""
    doc = load_fixture(LIVE_GNA)
    read_fields = {
        "entry": ["matchType"],
        "bestResult": ["matchType", "editDistance", "matchedName", "currentName",
                       "dataSourceTitleShort", "isSynonym"],
    }
    for row in doc["captures"]:
        entry = (row["response"].get("names") or [{}])[0]
        for key in read_fields["entry"]:
            assert key in entry, (
                f"{row['query']}: live response no longer carries entry.{key}"
            )
        best = entry.get("bestResult")
        if not isinstance(best, dict):
            continue  # a NoMatch entry legitimately carries none
        for key in read_fields["bestResult"]:
            assert key in best, (
                f"{row['query']}: live response no longer carries "
                f"bestResult.{key} — gna.check() would read None there"
            )


def test_live_gna_capture_provenance_is_specific_enough_to_re_derive(load_fixture):
    """'Live' must mean re-derivable, not merely 'we typed this from memory'."""
    prov = load_fixture(LIVE_GNA)["_provenance"]
    assert prov["live_capture"] is True
    for key in ("recorded_at", "recorded_by", "endpoint", "request_shape"):
        assert prov.get(key), f"_provenance.{key} is missing"
    assert "record_live_gna.py" in prov["recorded_by"]
    assert prov["endpoint"].startswith("https://")
    # the recording must use the same request shape production uses, or it
    # pins a contract nobody calls (withAllMatches true returns a different
    # response shape entirely)
    assert prov["request_shape"]["withAllMatches"] is False


# ---------------------------------------------------------------------------
# Manifest honesty + fixture consumption
# ---------------------------------------------------------------------------

def test_no_fixture_claims_to_be_a_live_capture(fixture_manifest, load_fixture):
    """manifest.json must not overstate provenance.

    'Recorded from the live service' is a claim only one fixture here may make,
    and only with the evidence to back it: for every fixture the manifest must
    state live_capture as an explicit boolean with a recorded source, and the
    file's own ``_provenance`` must agree, so nobody can quietly upgrade a
    source-derived sample into an apparent live capture. Anything claiming
    live_capture: true must additionally name the recorder, the endpoint and the
    recording time, so the claim can be re-derived rather than taken on faith.
    """
    files = fixture_manifest["files"]
    assert files, "manifest lists no fixtures"
    live = []
    for name, meta in files.items():
        assert isinstance(meta.get("live_capture"), bool), (
            f"{name}: manifest must state live_capture as an explicit boolean"
        )
        assert meta.get("derived_from"), f"{name}: no provenance source recorded"
        if not name.endswith(".json"):
            continue
        provenance = load_fixture(name).get("_provenance", {})
        assert provenance.get("live_capture") is meta["live_capture"], (
            f"{name}: manifest and the file's own _provenance disagree"
        )
        if meta["live_capture"]:
            live.append(name)
            for key in ("recorded_at", "recorded_by", "endpoint"):
                assert provenance.get(key), (
                    f"{name}: claims live_capture: true without _provenance.{key}"
                )
    assert live, (
        "no fixture claims a live capture any more — the M2/M3 evidence gate "
        "expects at least one genuine capture (see scripts/record_live_gna.py)"
    )


def test_every_recorded_fixture_is_referenced_by_a_test(fixture_manifest,
                                                        fixtures_dir):
    """A fixture no test loads is dead evidence — M3 needs it wired to a test.

    Every file the manifest inventories must be named somewhere under tests/
    (a test module or conftest), so the manifest cannot claim a fixture is
    'consumed_by' something that never touches it.
    """
    corpus = "\n".join(p.read_text(encoding="utf-8")
                       for p in sorted(fixtures_dir.parent.glob("*.py")))
    for name in fixture_manifest["files"]:
        assert name in corpus, (
            f"{name}: listed in manifest.json but referenced by no test file"
        )
