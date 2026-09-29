"""The two LPSN status vocabularies, and what a joined status string may conclude.

Why this file exists
------------------------
A *whole-string* exact lookup against ONE flat enum cannot read real LPSN
output. LPSN carries a
*nomenclatural* status ("validly published under the ICNP", "conserved name",
"not validly published", "validly published under ICN", "illegitimate name")
and a *taxonomic* status ("correct name", "preferred name", "synonym",
"misspelling", "later homonym"), and the page/API print them **comma-joined**
into one string, sometimes with parenthetical qualifiers — so every realistic
value falls through to 'unknown'. Listing the joined forms as extra enum values
would not be a fix either: it would conflate the two vocabularies, which is a
semantic error ("validly published under ICN" is not an ICNP claim, and
"misspelling" is the parahomonym signal, not a synonym).

The tables and their provenance live in
`src/prokname/data/lpsn_status.json`; the derivation lives in
`src/prokname/dedup/lpsn.py` (`split_status` -> `build_status_lookup` ->
`combine_status_claims`). This file pins, in order:

1. the structure of the two vocabularies (and that they cannot overlap);
2. every single label of both vocabularies, standalone;
3. the comma-joined combinations that real LPSN records carry;
4. parenthetical qualifiers: kept for the report, interpreted by nobody;
5. the cross-position rules — a nomenclatural label may never be read as an
   occupancy claim, a taxonomic label never as an ICNP-validity claim;
6. the field-shape surprises (None / dict / list) that must degrade, not crash;
7. the safety properties that must never regress (negated labels,
   variant/"in preparation" warnings, unreadable status, unknown labels) —
   plus the end-to-end adapter proof that a real-shaped record yields a
   ruling rather than 'unknown'.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from prokname.dedup import lpsn as L
from prokname.dedup.model import (
    FOUND_OCCUPIED,
    FOUND_OTHER,
    FOUND_UNKNOWN,
    FOUND_VALID,
    NON_RULING_STATUSES,
    OCCUPYING_STATUSES,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _payload() -> dict:
    L.reload_status_table()
    return L.status_table()


def _rows(vocabulary: str) -> list[dict]:
    key = f"{vocabulary}_statuses"
    rows = _payload()[key]
    assert rows, f"{key} is empty — the asset lost a vocabulary"
    return rows


def _keys_of(vocabulary: str) -> set[str]:
    keys: set[str] = set()
    for row in _rows(vocabulary):
        keys.add(L._normalise_label(row["label"]))
        keys.update(L._normalise_label(a) for a in row.get("aliases", []))
    return keys


#: The realistic status strings a live LPSN record / page can produce, with the
#: verdict each must yield. Categories map to statuses via
#: L._CATEGORY_STATUS; `verified` is the AND over the sourced rows matched.
JOINED_CASES = [
    # (status string, category, status, occupies, verified)
    ("correct name", "occupied_valid", FOUND_VALID, True, True),
    ("validly published under the ICNP, correct name",
     "occupied_valid", FOUND_VALID, True, True),
    ("not validly published, synonym",
     "occupied_other", FOUND_OCCUPIED, True, True),
    ("conserved name, correct name",
     "occupied_valid", FOUND_VALID, True, True),
    ("correct name (see also Bacillota)",
     "occupied_valid", FOUND_VALID, True, True),
    ("synonym (and no standing)",
     "occupied_other", FOUND_OCCUPIED, True, True),
    # 'later homonym' is the one un-sourced label in this string: it may be
    # read, but the answer must stay provisional (verified=False).
    ("validly published under ICN, later homonym",
     "occupied_other", FOUND_OCCUPIED, True, False),
    ("illegitimate name",
     "unoccupied_record", FOUND_OTHER, False, True),
    # extras: the other shapes the facts sheet documents
    ("validly published under the ICNP, conserved name",
     "unknown", FOUND_UNKNOWN, None, True),
    ("validly published under the ICNP, preferred name",
     "occupied_other", FOUND_OCCUPIED, True, False),
    ("validly published under ICN, misspelling",
     "unoccupied_record", FOUND_OTHER, False, True),
    ("not validly published, correct name",
     "occupied_other", FOUND_OCCUPIED, True, True),
    ("validly published under the ICNP; correct name",
     "occupied_valid", FOUND_VALID, True, True),
]


# ---------------------------------------------------------------------------
# 1. structure of the asset
# ---------------------------------------------------------------------------

def test_asset_ships_two_separate_vocabularies():
    payload = _payload()
    assert set(payload) >= {"_meta", "nomenclatural_statuses",
                            "taxonomic_statuses", "statuses"}
    allowed = {
        L.NOMENCLATURAL: {L.VALID_ICNP, L.VALID_OTHER_CODE, L.NOT_VALID,
                          L.VALIDITY_UNASSERTED},
        L.TAXONOMIC: {L.OCCUPYING_CORRECT, L.OCCUPYING_IN_USE,
                      L.OCCUPYING_NOT_CORRECT, L.NON_OCCUPYING},
    }
    for vocabulary in (L.NOMENCLATURAL, L.TAXONOMIC):
        for row in _rows(vocabulary):
            assert row["claim"] in allowed[vocabulary], row
            assert row["verified"] in (True, False), row
            assert row["provenance"].strip(), f"{row['label']}: no provenance"
            assert row["note"].strip(), f"{row['label']}: no reviewer note"
            # the flat view must spell out which vocabulary a row came from
            flat = next(r for r in payload["statuses"]
                        if r["label"] == row["label"])
            assert flat["vocabulary"] == vocabulary


def test_the_two_vocabularies_share_no_label_or_alias():
    """Conflation is impossible by construction, not by convention."""
    overlap = _keys_of(L.NOMENCLATURAL) & _keys_of(L.TAXONOMIC)
    assert not overlap, f"labels in BOTH vocabularies: {sorted(overlap)}"


def test_a_label_listed_in_both_tables_is_never_classified(monkeypatch):
    """The loader's ambiguity guard: a duplicated key abstains entirely."""
    payload = {
        "nomenclatural_statuses": [
            {"label": "correct name", "aliases": [], "claim": L.VALID_ICNP,
             "verified": True, "provenance": "test", "note": "test"},
        ],
        "taxonomic_statuses": [
            {"label": "correct name", "aliases": [],
             "claim": L.OCCUPYING_CORRECT, "verified": True,
             "provenance": "test", "note": "test"},
        ],
    }
    lookup, _ = L.build_status_lookup(payload)
    assert lookup["correct name"]["vocabulary"] == L._AMBIGUOUS
    monkeypatch.setattr(L, "_status_lookup", lambda: (lookup, "test"))
    result = L.classify_status("correct name")
    assert result.category == "unknown"
    assert result.status == FOUND_UNKNOWN
    assert result.occupies is None


def test_unreadable_asset_falls_back_to_the_two_vendored_labels_only(
        monkeypatch):
    """Without the asset the adapter must be able to read LESS, not more."""
    lookup, note = L.build_status_lookup({"_meta": {}, "statuses": "nope"})
    assert "FALLBACK" in note
    assert set(lookup) == {"correct name", "synonym"}
    assert all(v["vocabulary"] == L.TAXONOMIC for v in lookup.values())
    # ... so a nomenclatural token can no longer be read at all: the joined
    # string carries an unsourceable token and the whole record abstains.
    monkeypatch.setattr(L, "_status_lookup", lambda: (lookup, "FALLBACK"))
    assert L.classify_status("correct name").category == "occupied_valid"
    joined = L.classify_status("validly published under the ICNP, correct name")
    assert joined.category == "unknown"
    assert joined.status in NON_RULING_STATUSES


# ---------------------------------------------------------------------------
# 2. every single label of both vocabularies, standalone
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row", _rows(L.NOMENCLATURAL),
                         ids=lambda r: r["label"])
def test_every_nomenclatural_label_is_read_as_validity_only(row):
    result = L.classify_status(row["label"])
    assert [t.vocabulary for t in result.tokens] == [L.NOMENCLATURAL]
    assert result.validity == row["claim"]
    # the dimension it may NOT touch:
    assert result.occupancy == "", (
        "a nomenclatural label must never contribute name occupancy")
    assert result.status not in OCCUPYING_STATUSES, (
        "a nomenclatural label alone can never rule that the name is occupied")
    assert result.verified is bool(row["verified"])


@pytest.mark.parametrize("row", _rows(L.TAXONOMIC), ids=lambda r: r["label"])
def test_every_taxonomic_label_is_read_as_occupancy_only(row):
    result = L.classify_status(row["label"])
    assert [t.vocabulary for t in result.tokens] == [L.TAXONOMIC]
    assert result.occupancy == row["claim"]
    assert result.validity == "", (
        "a taxonomic label must never contribute an ICNP-validity claim")
    assert result.verified is bool(row["verified"])


@pytest.mark.parametrize("vocabulary", [L.NOMENCLATURAL, L.TAXONOMIC])
def test_every_alias_resolves_to_its_own_rows_claim(vocabulary):
    for row in _rows(vocabulary):
        for alias in row.get("aliases", []):
            result = L.classify_status(alias)
            assert [t.vocabulary for t in result.tokens] == [vocabulary], alias
            assert result.category == result.category
            assert (result.validity if vocabulary == L.NOMENCLATURAL
                    else result.occupancy) == row["claim"], alias
            assert result.verified is bool(row["verified"]), alias


def test_flat_status_view_matches_the_derivation():
    """`statuses` is a DERIVED convenience view; drift here is a bug."""
    flat = {row["label"]: row for row in _payload()["statuses"]}
    expected_rows = sum(len(_rows(v)) for v in (L.NOMENCLATURAL, L.TAXONOMIC))
    assert len(flat) == expected_rows
    for vocabulary in (L.NOMENCLATURAL, L.TAXONOMIC):
        for row in _rows(vocabulary):
            declared = flat[row["label"]]
            for label in [row["label"], *row.get("aliases", [])]:
                result = L.classify_status(label)
                assert result.category == declared["category"], label
                assert result.status == L._CATEGORY_STATUS[
                    declared["category"]], label
                assert result.verified is bool(row["verified"]), label
                assert declared["maps_to"] == result.status, label
                assert declared["is_valid"] is (
                    result.category == "occupied_valid"), label


def test_nomenclatural_labels_alone_never_rule_on_occupancy():
    """The 'unknown remains the fallback' half of the combination rule."""
    for row in _rows(L.NOMENCLATURAL):
        result = L.classify_status(row["label"])
        assert result.occupies is not True, row["label"]
        assert result.status not in OCCUPYING_STATUSES, row["label"]
        if row["claim"] != L.NOT_VALID:
            # validity asserted (or unasserted) without any taxonomic token:
            # nothing is known about the name string ⇒ no verdict at all
            assert result.category == "unknown", row["label"]
            assert result.status in NON_RULING_STATUSES, row["label"]
        else:
            # an explicitly negative nomenclatural finding is a warning, and
            # is never read as valid (the original M4 failure mode)
            assert result.category == "unoccupied_record", row["label"]
            assert result.status == FOUND_OTHER, row["label"]


def test_combine_status_claims_is_exhaustive_and_never_invents_a_category():
    validities = ["", L.VALID_ICNP, L.VALID_OTHER_CODE, L.NOT_VALID,
                  L.VALIDITY_UNASSERTED, L.CONTRADICTORY]
    occupancies = ["", L.OCCUPYING_CORRECT, L.OCCUPYING_IN_USE,
                   L.OCCUPYING_NOT_CORRECT, L.NON_OCCUPYING, L.CONTRADICTORY]
    for validity in validities:
        for occupancy in occupancies:
            category = L.combine_status_claims(validity, occupancy)
            assert category in L._CATEGORY_VALUES, (validity, occupancy)
            if occupancy != L.OCCUPYING_CORRECT:
                # only a taxonomic 'correct name' may license a clean answer
                assert category != "occupied_valid", (validity, occupancy)
            if not occupancy:
                assert category in {"unknown", "unoccupied_record"}, (
                    validity, occupancy)


# ---------------------------------------------------------------------------
# 3. comma-joined combinations (the strings that used to be all 'unknown')
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status_string,category,expected_status,occupies,verified", JOINED_CASES,
    ids=[case[0] for case in JOINED_CASES])
def test_joined_status_strings_are_read(status_string, category,
                                        expected_status, occupies, verified):
    result = L.classify_status(status_string)
    assert result.category == category
    assert result.status == expected_status
    assert result.occupies is occupies
    assert result.verified is verified
    assert result.label == status_string
    # every token is attributed to exactly one vocabulary, and — for the joined
    # forms — the whole string is NOT one of the table keys: the verdict comes
    # from the combination, never from listing long enum values.
    assert result.tokens
    for token in result.tokens:
        assert token.vocabulary in (L.NOMENCLATURAL, L.TAXONOMIC)
    if len(L.split_status(status_string)) > 1:
        all_keys = _keys_of(L.NOMENCLATURAL) | _keys_of(L.TAXONOMIC)
        assert L._normalise_label(status_string) not in all_keys, status_string


def test_icnp_validity_plus_correct_name_is_the_only_occupied_valid_shape():
    """The cross-code trap: 'validly published under ICN' ≠ '… under the ICNP'."""
    under_icn = L.classify_status("validly published under ICN, correct name")
    under_icnp = L.classify_status(
        "validly published under the ICNP, correct name")
    assert under_icnp.category == "occupied_valid"
    assert under_icn.category == "occupied_other"
    assert under_icn.status == FOUND_OCCUPIED
    assert under_icn.validity == L.VALID_OTHER_CODE
    assert "ICN" in under_icn.note, "the cross-code warning must be readable"


def test_taxonomic_correct_name_cannot_make_a_negative_record_valid():
    for status_string in ("not validly published, correct name",
                          "illegitimate name, correct name",
                          "in preparation, correct name",
                          "convalescing name, correct name"):
        result = L.classify_status(status_string)
        assert result.status != FOUND_VALID, status_string
        assert result.category == "occupied_other", status_string


def test_contradictory_evidence_refuses_a_verdict():
    for status_string in ("correct name, synonym",
                          "correct name, later homonym",
                          "correct name, misspelling",
                          "validly published under the ICNP, not validly published",
                          "conserved name, not validly published"):
        result = L.classify_status(status_string)
        assert result.category == "unknown", status_string
        assert result.status in NON_RULING_STATUSES, status_string
        assert result.occupies is None, status_string


# ---------------------------------------------------------------------------
# 4. parenthetical qualifiers
# ---------------------------------------------------------------------------

def test_split_status_tracks_bracket_depth():
    assert L.split_status("validly published under the ICNP, correct name") == [
        "validly published under the ICNP", "correct name"]
    assert L.split_status("correct name (see also Bacillota, cf. Smith 2020); "
                          "synonym") == [
        "correct name (see also Bacillota, cf. Smith 2020)", "synonym"]
    assert L.split_status("conserved name [ICNP]; correct name") == [
        "conserved name [ICNP]", "correct name"]


@pytest.mark.parametrize("fragment,expected_bare,expected_qualifier", [
    ("correct name (see also Bacillota)", "correct name", "see also Bacillota"),
    ("synonym (and no standing)", "synonym", "and no standing"),
    ("correct name (and explicitly recommended for medical use)",
     "correct name", "and explicitly recommended for medical use"),
    ("correct name (a) (b)", "correct name", "a; b"),
    ("correct name (nested (inner) outer)", "correct name",
     "nested (inner) outer"),
    ("var.", "var.", ""),                       # trailing '.' is NOT stripped
    ("cass./orth. var.", "cass./orth. var.", ""),
    ("misspelling", "misspelling", ""),
])
def test_strip_qualifier(fragment, expected_bare, expected_qualifier):
    assert L.strip_qualifier(fragment) == (expected_bare, expected_qualifier)


@pytest.mark.parametrize("status_string,bare_label,qualifier", [
    ("correct name (see also Bacillota)", "correct name", "see also Bacillota"),
    ("synonym (and no standing)", "synonym", "and no standing"),
    ("validly published under the ICNP (Approved Lists 1980), correct name",
     "correct name", "Approved Lists 1980"),
])
def test_qualified_labels_are_looked_up_bare_and_reported_verbatim(
        status_string, bare_label, qualifier):
    result = L.classify_status(status_string)
    assert result.tokens
    assert bare_label in [t.label for t in result.tokens]
    # the qualifier is preserved for the detail string, never reinterpreted
    assert result.qualifiers == (qualifier,)
    assert qualifier in result.provenance
    assert qualifier in result.note
    assert result.label == status_string           # nothing dropped either
    assert result.category in {"occupied_valid", "occupied_other"}


def test_a_qualifier_may_not_sneak_a_second_label_past_the_tables():
    """'(validly published under the ICNP)' is a qualifier, not a token."""
    plain = L.classify_status("correct name")
    qualified = L.classify_status("correct name (validly published under ICN)")
    assert qualified.category == plain.category
    # it is reported, and it is not read as a nomenclatural claim:
    assert qualified.qualifiers == ("validly published under ICN",)
    assert qualified.validity == ""


def test_a_string_of_only_qualifiers_yields_no_verdict():
    result = L.classify_status("(and no standing)")
    assert result.category == "unknown"
    assert result.status in NON_RULING_STATUSES


def test_unbalanced_parenthesis_is_kept_not_swallowed():
    result = L.classify_status("correct name (see also Bacillota")
    assert result.tokens[0].label == "correct name"
    assert result.qualifiers == ("see also Bacillota",)


# ---------------------------------------------------------------------------
# 5/6. shapes, empties, and the abstention ladder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [None, "", "   ", {}, {"nested": {"deeper": "x"}},
                                 [], [[]], {"a": {}}])
def test_unreadable_or_empty_status_is_a_non_ruling_warning(raw):
    result = L.classify_status(raw)
    assert result.category == "unoccupied_record", raw
    assert result.status == FOUND_OTHER, raw
    assert result.occupies is False, raw
    assert result.verified is False, raw          # never confident
    assert result.status not in OCCUPYING_STATUSES


@pytest.mark.parametrize("raw", [{"lpsn_taxonomic_status": None},
                                 {"status": "n/a"}, ["None"]])
def test_a_status_that_flattens_to_garbage_is_not_read_as_empty(raw):
    """An explicit non-label value is vocabulary drift, not a missing status:
    it must abstain (`found_unknown` ⇒ BLOCKED), never be dressed up as the
    'record exists but does not occupy' warning."""
    result = L.classify_status(raw)
    assert result.category == "unknown", raw
    assert result.status in NON_RULING_STATUSES, raw
    assert result.verified is False, raw


@pytest.mark.parametrize("raw,expected_status", [
    ({"lpsn_taxonomic_status": "validly published under the ICNP, "
                               "correct name"}, FOUND_VALID),
    ({"name": "correct name", "id": 7}, FOUND_VALID),
    ([{"lpsn_taxonomic_status": "not validly published, synonym"}],
     FOUND_OCCUPIED),
    (["not validly published", "synonym"], FOUND_OCCUPIED),
    ({"a": "validly published under the ICNP", "b": "correct name"},
     FOUND_VALID),
])
def test_flattened_shapes_are_tokenised_like_a_plain_string(raw,
                                                           expected_status):
    result = L.classify_status(raw)
    assert result.status == expected_status, raw
    assert result.category != "unknown", raw


def test_unsourceable_label_never_produces_a_clean_verdict():
    for raw in ("brand new lpsn status", "descriptive name",
                "validly published under the ICNPA",
                "nomen nudum", "rejected name", "nomen confusum"):
        result = L.classify_status(raw)
        assert result.category == "unknown", raw
        assert result.status in NON_RULING_STATUSES, raw
        assert result.occupies is None, raw
        assert result.verified is False, raw
        assert "unrecognised status token" in result.provenance, raw


def test_substring_lookalikes_are_not_matched():
    """The M4 substring bug must stay dead in the tokenised matcher too."""
    for raw in ("non correct name whatsoever",
                "a name validly published under the ICNP (allegedly)",
                "correct names", "not a synonym"):
        assert L.classify_status(raw).category == "unknown", raw


# ---------------------------------------------------------------------------
# 7. safety properties of the first fix, and the M3 fixture contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    "Non-validly published name",
    "Not validly published name",
    "NON-VALIDLY PUBLISHED NAME",
    "not validly published",
    "not validly published, correct name",
    "validly published under ICN, correct name",
])
def test_negated_and_cross_code_labels_are_never_read_as_valid(label):
    """Neither a negated nor another code's 'validly published'
    may license the answer 'this name is validly published and correct'."""
    result = L.classify_status(label)
    assert result.status != FOUND_VALID, label
    assert result.category != "occupied_valid", label


@pytest.mark.parametrize("label", [
    "variant", "Variant", "var.", "cass./orth. var.", "orth. var.",
    "emend. candidate", "emend. pro pend.", "misspelling",
    "in preparation", "In Press", "announced", "provisional name",
    "convalescing name",
])
def test_variant_and_announcement_labels_stay_non_occupying_warnings(label):
    result = L.classify_status(label)
    assert result.category == "unoccupied_record", label
    assert result.status == FOUND_OTHER, label
    assert result.occupies is False, label


def test_misspelling_is_the_parahomonym_signal_not_a_synonym():
    result = L.classify_status("misspelling")
    assert result.status != FOUND_OCCUPIED
    assert result.tokens[0].claim == L.NON_OCCUPYING
    assert "PARAHOMONYM" in result.note.upper()
    assert "not a synonym" in result.note


def test_pinned_fixture_labels_all_classify(load_fixture):
    """tests/fixtures/lpsn_status_labels.json is the M3 contract: no row of it
    may fall through to 'found_unknown' (that is what test_api_fixtures.py
    asserts too — this keeps the two vocabularies honest about it)."""
    rows = load_fixture("lpsn_status_labels.json")["labels"]
    assert rows, "the label fixture is empty"
    for row in rows:
        result = L.classify_status(row["value"])
        assert result.category in {"occupied_valid", "occupied_other",
                                   "unoccupied_record"}, row["value"]
        assert result.status == L._CATEGORY_STATUS[result.category]
        if "validly published" in row["value"].casefold():
            assert result.status != FOUND_VALID, row["value"]
            assert result.occupies is not True, row["value"]


# ---------------------------------------------------------------------------
# end-to-end: the adapter, with a stub of the official client
# ---------------------------------------------------------------------------


class StubLpsnClient:
    """Mimics the parts of lpsn.LpsnClient the adapter relies on."""

    entries: list[dict] = []

    def __init__(self, user, password, **kwargs):
        self.access_token = "stub-token"

    def search(self, **params):
        self.result = {"count": len(type(self).entries), "next": None}
        return len(type(self).entries)

    def retrieve(self, filter=None):
        yield from type(self).entries


@pytest.fixture
def stub_lpsn(monkeypatch):
    module = types.ModuleType("lpsn")
    module.LpsnClient = StubLpsnClient
    monkeypatch.setitem(sys.modules, "lpsn", module)
    monkeypatch.setenv("PROKNAME_LPSN_USER", "prokname-test")
    monkeypatch.setenv("PROKNAME_LPSN_PASSWORD", "not-a-secret")
    return StubLpsnClient


def _check_with(monkeypatch, client, status_value, query="Escherichia coli"):
    monkeypatch.setattr(client, "entries", [
        {"full_name": query, "lpsn_taxonomic_status": status_value},
    ], raising=False)
    return L.check(query, allow_network=True)


def test_adapter_now_rules_on_a_real_shaped_record(monkeypatch, stub_lpsn):
    """The regression the whole rewrite exists for.

    'validly published under the ICNP, correct name' is how LPSN prints the
    two fields of a live genus page; the previous whole-string matcher had no
    such enum value and returned `found_unknown`, i.e. no ruling at all.
    """
    status_string = "validly published under the ICNP, correct name"
    # proof that this is the combination doing the work, not one long label:
    assert L._normalise_label(status_string) not in (
        _keys_of(L.NOMENCLATURAL) | _keys_of(L.TAXONOMIC))
    assert L.classify_status(status_string).category == "occupied_valid"

    result = _check_with(monkeypatch, stub_lpsn, status_string)
    assert result.status == FOUND_VALID
    assert result.verified is True
    assert result.tier == "authority"
    assert result.online_derived is True
    assert status_string in result.detail
    assert "nomenclatural=valid_under_icnp" in result.detail
    assert "taxonomic=occupies_correct_name" in result.detail
    assert "NOT verified" not in result.detail


def test_adapter_reports_the_qualifier_instead_of_dropping_it(monkeypatch,
                                                              stub_lpsn):
    result = _check_with(monkeypatch, stub_lpsn,
                         "correct name (see also Bacillota)")
    assert result.status == FOUND_VALID
    assert result.verified is True
    assert "see also Bacillota" in result.detail


def test_adapter_keeps_an_unsourced_token_provisional(monkeypatch, stub_lpsn):
    result = _check_with(monkeypatch, stub_lpsn,
                         "validly published under ICN, later homonym")
    assert result.status == FOUND_OCCUPIED          # occupied, conflict tier
    assert result.verified is False                 # … but provisional wording
    assert "NOT verified against a live LPSN response" in result.detail
    assert "nomenclatural:validly published under ICN" in result.detail


def test_adapter_blocks_on_a_label_it_cannot_source(monkeypatch, stub_lpsn):
    result = _check_with(monkeypatch, stub_lpsn,
                         "validly published under ICN, descriptive name; synonym")
    assert result.status == FOUND_UNKNOWN
    assert result.status in NON_RULING_STATUSES
    assert result.verified is False
    assert "descriptive name" in result.detail
    assert "not_found" != result.status             # ≠ 'the name is free'


def test_adapter_still_refuses_to_let_the_record_identity_rule(monkeypatch,
                                                               stub_lpsn):
    """The two-vocabulary rewrite must not weaken the identity guard."""
    monkeypatch.setattr(stub_lpsn, "entries", [
        {"full_name": "Escherichia coli strain X",
         "lpsn_taxonomic_status": "validly published under the ICNP, "
                                  "correct name"},
    ], raising=False)
    result = L.check("Escherichia coli", allow_network=True)
    assert result.status == "not_found"
    assert "full_name" in result.detail


def test_asset_can_be_edited_and_reloaded():
    before = L.classify_status("validly published under the ICNP, correct name")
    L.reload_status_table()
    after = L.classify_status("validly published under the ICNP, correct name")
    assert before.category == after.category == "occupied_valid"
    # the raw asset is exposed for expert review, both tables intact
    payload = L.status_table()
    assert json.dumps(payload)  # serialisable, i.e. valid JSON as shipped
    assert payload["nomenclatural_statuses"] and payload["taxonomic_statuses"]
