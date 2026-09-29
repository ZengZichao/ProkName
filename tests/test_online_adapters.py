"""Unit tests for the ONLINE code paths of the authority/reference adapters.

Two layers of evidence, kept strictly apart:

1. `tests/fixtures/` — source-derived payloads copied/trimmed from the vendored
   upstream client snapshots (`lpsn-api/README.md:81-96`,
   `gnverifier/pkg/io/verifrest/fixtures/names.yaml:15`,
   `gnmatcher/internal/testdata/testdata.csv:1-5`). None of them is a live
   capture: `manifest.json` says `live_capture: false` for every file, and
   `tests/test_api_fixtures.py::test_no_fixture_claims_to_be_a_live_capture`
   enforces that nobody quietly upgrades one. These tests pin the FIELD NAMES
   and the count/matchType semantics, so a rename upstream (e.g.
   `full_name` → `name_string`) breaks a test instead of silently turning
   every query into `not_found`.

2. Stubs of the official clients (`FakeLpsnClient`, `httpx`) that mimic the
   behaviour the adapters must survive — the official `lpsn` client reports
   failures by PRINTING (never raising), `search()` returns a hit COUNT and
   leaves `result` set, `retrieve()` yields full entries as dicts.

Replaying recorded cassettes of the LIVE services (real credentials for LPSN)
remains an M0/M2 deliverable — see tests/test_api_fixtures.py and the
`live_capture_available` fixture in tests/conftest.py.
"""

from __future__ import annotations

import copy
import sys
import types

import pytest

from prokname.dedup import gna as gna_mod
from prokname.dedup import lpsn as lpsn_mod
from prokname.dedup import ncbi as ncbi_mod
from prokname.dedup import seqcode as seqcode_mod

# ---------------------------------------------------------------------------
# LPSN — stub of the official client (PyPI: lpsn)
# ---------------------------------------------------------------------------

#: The contract keys the adapter reads. Renaming any of them upstream MUST
#: break a test; see test_pinned_lpsn_field_names.
LPSN_PINNED_FIELDS = {"full_name", "lpsn_taxonomic_status"}


class FakeLpsnClient:
    """Mimics lpsn.LpsnClient behaviour that the adapter must survive."""

    auth_ok = True
    search_count = 1
    reject_query = False
    entries: list[dict] = [
        {"full_name": "Escherichia coli", "lpsn_taxonomic_status": "correct name"},
    ]
    instances: list[FakeLpsnClient] = []

    def __init__(self, user, password, public=True, max_retries=10,
                 retry_delay=50, request_timeout=300):
        print("-- Authentication successful --")  # official client prints
        if type(self).auth_ok:
            # computed placeholder — no credential material in test sources
            self.access_token = f"test-token-{id(self):x}"
        self.max_retries = max_retries
        type(self).instances.append(self)

    def search(self, **params):
        assert params.get("taxon_name"), "adapter must pass taxon_name=..."
        if type(self).reject_query:
            # lpsn-api/lpsn/client.py:185-188 — error payload, no 'count' key
            self.result = {"title": "Bad Request", "message": "invalid filter"}
            return 0
        self.result = {"count": type(self).search_count, "next": None}
        return type(self).search_count

    def retrieve(self, filter=None):
        yield from type(self).entries


DEFAULTS = {
    "auth_ok": True,
    "search_count": 1,
    "reject_query": False,
    "entries": [
        {"full_name": "Escherichia coli", "lpsn_taxonomic_status": "correct name"},
    ],
}


@pytest.fixture
def fake_lpsn(monkeypatch):
    module = types.ModuleType("lpsn")
    module.LpsnClient = FakeLpsnClient
    monkeypatch.setitem(sys.modules, "lpsn", module)
    monkeypatch.setenv("PROKNAME_LPSN_USER", "prokname-tester")
    monkeypatch.setenv("PROKNAME_LPSN_PASSWORD", "*" * 12)  # placeholder only
    FakeLpsnClient.instances = []
    for attr, value in DEFAULTS.items():
        monkeypatch.setattr(FakeLpsnClient, attr, value, raising=True)
    return FakeLpsnClient


def test_lpsn_online_found_valid(fake_lpsn, capsys):
    result = lpsn_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "found_valid"
    assert "correct name" in result.detail
    assert "full_name='Escherichia coli'" in result.detail
    assert result.online_derived is True
    # the client's stdout chatter must be captured, never leak into output
    assert "Authentication successful" not in capsys.readouterr().out


def test_lpsn_offline_result_is_not_marked_online(fake_lpsn):
    result = lpsn_mod.check("Escherichia coli", allow_network=False)
    assert result.online_derived is False


def test_lpsn_online_uses_capped_retries(fake_lpsn):
    lpsn_mod.check("Escherichia coli", allow_network=True)
    assert fake_lpsn.instances[0].max_retries == lpsn_mod._CLIENT_MAX_RETRIES
    assert fake_lpsn.instances[0].max_retries < 10  # official default


# --- fixture-driven contract ----------------------------

def test_pinned_lpsn_field_names(load_fixture):
    """The fixture must still carry the keys the adapter reads.

    If the vendored example this was derived from is ever replaced by a
    capture with renamed fields, this test — not the adjudication layer —
    has to notice.
    """
    entries = load_fixture("lpsn_retrieve_entries.json")["entries"]
    assert entries, "fixture carries no records"
    for entry in entries:
        missing = LPSN_PINNED_FIELDS - set(entry)
        assert not missing, (
            f"LPSN record lost field(s) {sorted(missing)}: the adapter's "
            "identity guard and status classification read those keys"
        )


def test_lpsn_fixture_records_are_bare_names_without_citations(load_fixture):
    """lpsn-api/README.md:88-96 shows `full_name` as the BARE name.

    The identity guard compares the query to `full_name`; if LPSN ever starts
    embedding the author citation there, every query degrades to not_found.
    """
    for entry in load_fixture("lpsn_retrieve_entries.json")["entries"]:
        name = entry["full_name"]
        assert ", " not in name and "(" not in name, (
            f"{name!r} looks like it carries an author citation; the identity "
            "guard in dedup/lpsn.py would reject every record"
        )


def test_lpsn_online_classifies_fixture_records(monkeypatch, fake_lpsn,
                                                load_fixture):
    monkeypatch.setattr(fake_lpsn, "entries",
                        load_fixture("lpsn_retrieve_entries.json")["entries"])
    assert lpsn_mod.check("Sulfolobus acidocaldarius",
                          allow_network=True).status == "found_valid"
    assert lpsn_mod.check("Sulfolobus brierleyi",
                          allow_network=True).status == "found_occupied"


def test_lpsn_renamed_field_is_detected_not_silently_ignored(monkeypatch, fake_lpsn,
                                                             load_fixture):
    """A field rename upstream must degrade LOUDLY.

    The pre-fix failure mode was: identity guard sees no `full_name` ⇒ every
    record is a "substring-only" hit ⇒ users are told "validly published name
    not in LPSN" with no hint that the contract drifted.
    """
    drift = copy.deepcopy(load_fixture("lpsn_retrieve_entries.json")["entries"])
    for record in drift:
        record["name_string"] = record.pop("full_name")
    monkeypatch.setattr(fake_lpsn, "entries", drift)

    result = lpsn_mod.check("Sulfolobus acidocaldarius", allow_network=True)
    assert result.status == "not_found"
    assert "full_name" in result.detail, (
        "the detail must name the field the identity guard needed, so the "
        "drift is diagnosable from the report alone"
    )


def test_lpsn_count_disambiguation_is_fixture_driven(monkeypatch, fake_lpsn,
                                                     load_fixture):
    """count == 0 with a 'count' key ⇒ not_found; without it ⇒ unavailable."""
    states = load_fixture("lpsn_search_states.json")

    class Zero(FakeLpsnClient):
        def search(self, **params):
            self.result = dict(states["genuine_zero_results"])
            return 0

    module = types.ModuleType("lpsn")
    module.LpsnClient = Zero
    monkeypatch.setitem(sys.modules, "lpsn", module)
    result = lpsn_mod.check("Notanorganism nowherei", allow_network=True)
    assert result.status == "not_found"

    class Rejected(FakeLpsnClient):
        def search(self, **params):
            self.result = dict(states["query_rejected"])
            return 0

    module = types.ModuleType("lpsn")
    module.LpsnClient = Rejected
    monkeypatch.setitem(sys.modules, "lpsn", module)
    result = lpsn_mod.check("Whatever reductum", allow_network=True)
    assert result.status == "unavailable"
    assert "rejected" in result.detail


# --- exhaustive enum -> category mapping ---------------------------------

def _asset_rows() -> list[dict]:
    from prokname.engine import data

    return data.load_json("lpsn_status.json")["statuses"]


@pytest.mark.parametrize("row", _asset_rows(), ids=lambda r: r["label"])
def test_every_status_label_is_classified_by_the_table(row):
    """M4: classification is an exact lookup — no substring reasoning.

    Every label in the expert-reviewable asset (and every alias) must map to
    the category the asset declares, and the two negated labels must never
    read as validly published.
    """
    expected_by_category = {
        "occupied_valid": "found_valid",
        "occupied_other": "found_occupied",
        "unoccupied_record": "found_other",
        "unknown": "found_unknown",
    }
    for label in [row["label"], *row.get("aliases", [])]:
        result = lpsn_mod.classify_status(label)
        assert result.category == row["category"], label
        assert result.status == expected_by_category[row["category"]], label
        assert result.verified is bool(row["verified"]), label


def test_substring_test_is_gone_from_the_implementation():
    """The `in status` comparison must not come back.

    Scoped to the query implementation (module-level prose may quote the old
    line to explain what was removed).
    """
    import inspect

    source = inspect.getsource(lpsn_mod._check) + inspect.getsource(
        lpsn_mod.classify_status
    )
    assert '"correct name" in' not in source
    assert '"validly published" in' not in source
    assert ".lower()" not in source  # case handling goes through the table key


@pytest.mark.parametrize("label", [
    "Non-validly published name",
    "Not validly published name",
    "NON-VALIDLY PUBLISHED NAME",
])
def test_negated_labels_are_never_read_as_valid(label):
    """A negated 'validly published' label must never read as valid."""
    result = lpsn_mod.classify_status(label)
    assert result.status == "found_other"
    assert result.occupies is False


def test_unlisted_label_refuses_a_confident_verdict():
    result = lpsn_mod.classify_status("brand new lpsn status")
    assert result.status == "found_unknown"
    assert result.verified is False
    assert result.occupies is None


def test_status_field_is_normalised_for_case_and_spacing():
    assert (lpsn_mod.classify_status("  CORRECT\nNAME ").status
            == "found_valid")


@pytest.mark.parametrize("raw, expected", [
    ({"lpsn_taxonomic_status": "synonym"}, "found_occupied"),
    ([{"lpsn_taxonomic_status": "correct name"}], "found_valid"),
    (["later homonym"], "found_occupied"),
    ({"nested": {"deeper": "x"}}, "found_other"),  # no readable label
])
def test_dict_or_list_shaped_status_degrades_without_crashing(raw, expected):
    """M4 (last paragraph): shape surprises are handled INSIDE the try."""
    assert lpsn_mod.classify_status(raw).status == expected


def test_dict_shaped_status_survives_the_whole_check(monkeypatch, fake_lpsn):
    """check() must never raise AttributeError out of the classification."""
    monkeypatch.setattr(fake_lpsn, "entries", [
        {"full_name": "Escherichia coli",
         "lpsn_taxonomic_status": {"name": "correct name", "id": 7}},
    ])
    result = lpsn_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "found_valid"


def test_unreadable_status_field_yields_a_warning_not_a_conflict(monkeypatch,
                                                                  fake_lpsn):
    monkeypatch.setattr(fake_lpsn, "entries", [
        {"full_name": "Escherichia coli", "lpsn_taxonomic_status": None},
    ])
    result = lpsn_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "found_other"
    assert result.verified is False


# --- LPSN availability / error posture (unchanged guarantees) --------------

def test_lpsn_online_not_found_is_distinguished_from_error(monkeypatch, fake_lpsn):
    monkeypatch.setattr(fake_lpsn, "search_count", 0)  # genuine zero-result query
    result = lpsn_mod.check("Notanorganism nowherei", allow_network=True)
    assert result.status == "not_found"


def test_lpsn_online_rejected_query_is_not_misread_as_not_found(monkeypatch, fake_lpsn):
    monkeypatch.setattr(fake_lpsn, "reject_query", True)
    result = lpsn_mod.check("Bad Query Here", allow_network=True)
    assert result.status == "unavailable"
    assert "rejected" in result.detail


def test_lpsn_online_auth_failure_is_unavailable(monkeypatch, fake_lpsn):
    module = types.ModuleType("lpsn")

    class NoAuth(FakeLpsnClient):
        auth_ok = False

    module.LpsnClient = NoAuth
    monkeypatch.setitem(sys.modules, "lpsn", module)
    result = lpsn_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "unavailable"
    assert "authentication" in result.detail.lower()


def test_lpsn_online_retrieve_failure_is_unavailable(monkeypatch, fake_lpsn):
    def boom(self, filter=None):
        raise KeyError("results")

    monkeypatch.setattr(fake_lpsn, "retrieve", boom)
    result = lpsn_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "unavailable"
    assert "blocked" in result.detail


# ---------------------------------------------------------------------------
# SeqCode — M0 gate must say what would flip it
# ---------------------------------------------------------------------------

def test_seqcode_negative_ruling_gate_still_lists_what_is_outstanding(fake_lpsn):
    """Rewritten 2026-09-25: the endpoint record now exists, the gate narrowed.

    The old version of this test asserted the adapter answered 'unavailable'
    online and enumerated the M0 flip conditions — the contract had never been
    probed. It has been now (docs/provenance/seqcode-registry-2026-09-25.md),
    which turned out to be the interesting result: the registry has no
    lookup-by-name and its status=SeqCode list provably omits valid names, so
    the gate is no longer "we never looked" but "this source cannot rule
    negatively". Both are non-ruling, and the detail must still say which.
    """
    from prokname.dedup.model import NON_RULING_STATUSES, NOT_FOUND

    result = seqcode_mod.check("Haloferax volcanii", allow_network=True)
    assert result.tier == "authority"
    assert result.status != NOT_FOUND, (
        "SeqCode ruled a name free from a list measured to be incomplete"
    )
    assert result.status in NON_RULING_STATUSES, result.status
    for fragment in seqcode_mod.m0_flip_checklist():
        assert fragment in result.detail
    assert "BLOCKED" in result.detail  # states the consequence, honestly
    assert "status_name" in result.detail  # names the evidence it rests on


# ---------------------------------------------------------------------------
# GNA GNverifier — stub of httpx with the source-pinned response schema
# ---------------------------------------------------------------------------

def _gna_payload(best_result: dict | None) -> dict:
    return {
        "metadata": {"namesNumber": 1, "dataSources": [4]},
        "names": [{
            "id": "744fa750",
            "name": "Escherichia coli",
            "matchType": "Exact" if best_result else "NoMatch",
            "curation": "Curated",
            "dataSourcesNum": 1,
            "bestResult": best_result,
            "results": [],
        }],
    }


BEST = {
    "matchedName": "Escherichia coli",
    "matchedCanonicalFull": "Escherichia coli",
    "currentName": "Escherichia coli",
    "matchType": "Exact",
    "editDistance": 0,
    "isSynonym": False,
    "taxonomicStatus": None,
    "dataSourceId": 4,
    "dataSourceTitleShort": "NCBI Taxonomy",
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self):
        return self._payload


@pytest.fixture
def gna_calls(monkeypatch):
    holder: dict = {"payload": _gna_payload(dict(BEST)), "status": 200}
    calls: dict = {}

    module = types.ModuleType("httpx")

    def _post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        return FakeResponse(holder["payload"], holder["status"])

    module.post = _post
    monkeypatch.setitem(sys.modules, "httpx", module)
    return holder, calls


def test_gna_online_posts_to_pinned_contract(gna_calls):
    _holder, calls = gna_calls
    result = gna_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "found_reference"
    assert "NCBI Taxonomy" in result.detail
    assert calls["url"] == gna_mod.VERIFICATIONS_URL
    assert calls["json"]["nameStrings"] == ["Escherichia coli"]
    assert calls["json"]["withAllMatches"] is False
    assert result.online_derived is True


def test_gna_fixture_exact_payload_is_accepted(load_fixture, monkeypatch):
    """The trimmed upstream recording must classify as a usage record."""
    payload = load_fixture("gna_verifications_exact.json")["response"]
    module = types.ModuleType("httpx")

    def _post(url, json=None, timeout=None):
        return FakeResponse(payload, 200)

    module.post = _post
    monkeypatch.setitem(sys.modules, "httpx", module)
    result = gna_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "found_reference"
    assert "edit distance: 0" in result.detail
    assert result.verified is True


@pytest.mark.parametrize("case", [
    "fuzzy_canonical_match_other_name",
    "exact_entry_fuzzy_best_result",
    "exact_but_missing_edit_distance",
])
def test_gna_fuzzy_never_occupies_a_name(load_fixture, monkeypatch, case):
    """only Exact × Exact × editDistance 0 may be phrased as 'in use'."""
    fixture = load_fixture("gna_verifications_fuzzy.json")
    payload = {k: v for k, v in fixture["cases"][case].items() if k != "_note"}
    module = types.ModuleType("httpx")
    installed = {"payload": payload}

    def _post(url, json=None, timeout=None):
        return FakeResponse(installed["payload"], 200)

    module.post = _post
    monkeypatch.setitem(sys.modules, "httpx", module)
    query = payload["names"][0]["name"]
    result = gna_mod.check(query, allow_network=True)
    assert result.status == "found_near_match", result.detail
    assert "NEAR MATCH ONLY" in result.detail
    assert result.verified is False
    assert "edit distance" in result.detail


def test_gna_missing_edit_distance_does_not_emit_malformed_text(load_fixture,
                                                                monkeypatch):
    """the old code printed 'edit distance: )' when the field was absent."""
    fixture = load_fixture("gna_verifications_fuzzy.json")
    payload = {k: v for k, v
               in fixture["cases"]["exact_but_missing_edit_distance"].items()
               if k != "_note"}
    module = types.ModuleType("httpx")

    def _post(url, json=None, timeout=None):
        return FakeResponse(payload, 200)

    module.post = _post
    monkeypatch.setitem(sys.modules, "httpx", module)
    result = gna_mod.check("Escherichia coli", allow_network=True)
    assert "edit distance: n/a" in result.detail
    assert "edit distance: )" not in result.detail


def test_gna_synonym_flag_needs_an_exact_match(monkeypatch, gna_calls):
    holder, _ = gna_calls
    holder["payload"] = _gna_payload(dict(BEST, isSynonym=True))
    result = gna_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "found_synonym"

    # ... and a fuzzy isSynonym must NOT become found_synonym
    holder["payload"] = _gna_payload(
        dict(BEST, isSynonym=True, matchType="FuzzyCanonicalMatch",
             editDistance=1)
    )
    result = gna_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "found_near_match"


def test_gna_no_best_result_is_not_found(monkeypatch, gna_calls):
    holder, _ = gna_calls
    holder["payload"] = _gna_payload(None)
    result = gna_mod.check("Notanorganism nowherei", allow_network=True)
    assert result.status == "not_found"


def test_gna_no_match_entry_shape_from_fixture_is_not_found(load_fixture,
                                                            monkeypatch):
    fixture = load_fixture("gna_verifications_fuzzy.json")
    payload = {k: v for k, v
               in fixture["cases"]["no_match_entry_without_best_result"].items()
               if k != "_note"}
    module = types.ModuleType("httpx")

    def _post(url, json=None, timeout=None):
        return FakeResponse(payload, 200)

    module.post = _post
    monkeypatch.setitem(sys.modules, "httpx", module)
    result = gna_mod.check("Notanorganism nowherei", allow_network=True)
    assert result.status == "not_found"
    assert "nomatch" in result.detail


def test_gna_empty_names_is_not_found(monkeypatch, gna_calls):
    holder, _ = gna_calls
    holder["payload"] = {"metadata": {"namesNumber": 0}, "names": []}
    result = gna_mod.check("Notanorganism nowherei", allow_network=True)
    assert result.status == "not_found"


def test_gna_http_error_is_unavailable(monkeypatch, gna_calls):
    holder, _ = gna_calls
    holder["status"] = 500
    result = gna_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "unavailable"


def test_gna_network_failure_is_unavailable(monkeypatch):
    module = types.ModuleType("httpx")

    def _boom(*args, **kwargs):
        raise ConnectionError("simulated outage")

    module.post = _boom
    monkeypatch.setitem(sys.modules, "httpx", module)
    result = gna_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "unavailable"


def test_gna_without_httpx_hints_at_online_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)  # import httpx → ImportError
    result = gna_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "unavailable"
    assert "online" in result.detail


# ---------------------------------------------------------------------------
# NCBI online path delegates to GNA (offline name_class behaviour is pinned in
# tests/test_api_fixtures.py against the synthetic names.dmp fixture)
# ---------------------------------------------------------------------------

def test_ncbi_online_delegates_and_propagates_near_match(monkeypatch, gna_calls):
    holder, _ = gna_calls
    holder["payload"] = _gna_payload(
        dict(BEST, matchType="FuzzyCanonicalMatch", editDistance=1)
    )
    result = ncbi_mod.check("Raliella subelongata", allow_network=True)
    assert result.status == "found_near_match"
    assert "via GNA GNverifier" in result.detail
    assert result.online_derived is True
