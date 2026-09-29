"""Unit tests: near-match (parahomonym) scan.

Review defects covered:
- The corpus index cache is content-addressed (not `id(list)`), bounded
  (LRU) and explicitly clearable.
- The generic name is never stemmed into the epithet domain, and long
  entries ('Bacillus subtilis subsp. spizizenii', 'Candidatus Pelagibacter
  ubique') are reachable from a binomial query through the core view.
- `corpus_is_stale(None)` degrades to "stale" instead of TypeError.
"""

from datetime import date

import pytest

from prokname.dedup import nearmatch as nearmatch_mod
from prokname.dedup.nearmatch import (
    INDEX_CACHE_MAX,
    clear_index_cache,
    core_name,
    corpus_index_key,
    corpus_is_stale,
    index_cache_info,
    levenshtein,
    load_seed_corpus,
    normalize_name,
    scan,
    scan_stemmed,
    stem_latin,
    stem_name,
)


@pytest.fixture(autouse=True)
def _fresh_index_cache():
    """The index cache is process-global; isolate it between tests."""
    clear_index_cache()
    yield
    clear_index_cache()


def _corpus(names, source="t"):
    return [{"name": n, "source": source} for n in names]


# ---------------------------------------------------------------------------
# Distance primitives
# ---------------------------------------------------------------------------

def test_levenshtein_basics():
    assert levenshtein("abc", "abc") == 0
    # -ensis vs -ense differ by TWO edits (substitute i→e, delete s)
    assert levenshtein("beijingensis", "beijingense") == 2
    assert levenshtein("coli", "colii") == 1
    assert levenshtein("", "abc") == 3


def test_normalize_folds_case_spaces_diacritics():
    assert normalize_name("  Wukomonas   beijingensis ") == "wukomonas beijingensis"
    assert normalize_name("Mülleria müllerii") == "muelleria muellerii"


# ---------------------------------------------------------------------------
# Whole-name scan
# ---------------------------------------------------------------------------

def test_scan_finds_one_edit_neighbours():
    corpus, _meta = load_seed_corpus()
    hits = scan(corpus, "Escherichia colii", max_distance=1)
    assert any(h.corpus_name == "Escherichia coli" for h in hits)


def test_scan_excludes_exact_match_itself():
    corpus, _ = load_seed_corpus()
    hits = scan(corpus, "Escherichia coli")
    assert not any(h.corpus_name == "Escherichia coli" for h in hits)


def test_scan_reports_source_and_sorted_by_distance():
    corpus, _ = load_seed_corpus()
    hits = scan(corpus, "Wukomonas beijingensiss", max_distance=2)
    distances = [h.distance for h in hits]
    assert distances == sorted(distances)
    assert all(h.source for h in hits)


# ---------------------------------------------------------------------------
# Staleness must be None-safe
# ---------------------------------------------------------------------------

def test_corpus_staleness():
    assert corpus_is_stale("2020-01-01", reference=date(2026, 8, 16))
    assert not corpus_is_stale("2026-08-01", reference=date(2026, 8, 16))
    assert not corpus_is_stale(
        "2020-01-01", max_age_days=10**6, reference=date(2026, 8, 16)
    )


@pytest.mark.parametrize("bad", [None, "", "   ", "not-a-date", 20260816,
                                 "16-08-2026"])
def test_unusable_corpus_date_reads_as_stale_never_crashes(bad):
    """`meta.get("corpus_date", default)` can legitimately yield None."""
    assert corpus_is_stale(bad, reference=date(2026, 8, 16)) is True


def test_fresh_corpus_date_is_not_stale():
    assert corpus_is_stale("2026-08-10", max_age_days=180,
                           reference=date(2026, 8, 16)) is False


# ---------------------------------------------------------------------------
# Stem-level scan (v2, Taxamatch / GNmatcher-inspired)
# ---------------------------------------------------------------------------

def test_stem_latin_strips_inflectional_endings():
    # place-adjective paradigm (-ensis/-ense) collapses to one stem
    assert stem_latin("beijingensis") == "beijing"
    assert stem_latin("beijingense") == "beijing"
    # person genitives (-ii/-iae, documented -i variant)
    assert stem_latin("boydii") == "boyd"
    assert stem_latin("boydiae") == "boyd"
    assert stem_latin("colii") == stem_latin("coli")
    # words without a known ending pass through unchanged
    assert stem_latin("wukong") == "wukong"
    # degenerate stems are never produced
    assert stem_latin("via") == "via"


def test_stem_name_keeps_the_genus_and_stems_the_epithet():
    """genus and epithet are different comparison domains."""
    assert stem_name(normalize_name("Wukomonas beijingensis")) == "wukomonas beijing"
    assert stem_name(normalize_name("Wukomonas beijingense")) == "wukomonas beijing"
    # the generic token is never reduced into the epithet domain any more
    assert stem_name("escherichia") == "escherichia"
    assert stem_name("escherichia coli") == "escherichia col"
    assert stem_name(normalize_name("Bacillus")) == "bacillus"
    # rank markers survive in the full stem view (only the core view drops them)
    assert stem_name("bacillus subtilis subsp. spizizenii").startswith(
        "bacillus subtil "
    )


def test_core_name_reduces_to_the_leading_binomial():
    assert core_name("bacillus subtilis") == "bacillus subtilis"
    assert core_name("bacillus subtilis subsp. spizizenii") == "bacillus subtilis"
    assert core_name("candidatus pelagibacter ubique") == "pelagibacter ubique"
    assert core_name("escherichia coli str. K-12") == "escherichia coli"
    assert core_name("wukomonas") == "wukomonas"


def test_scan_stemmed_flags_suffix_only_variants():
    corpus, _ = load_seed_corpus()
    hits = scan_stemmed(corpus, "Wukomonas beijingense")
    names = [h.corpus_name for h in hits]
    assert "Wukomonas beijingensis" in names  # stem-equal, distance 0
    assert "Wukomonas beijingense" not in names  # the exact name is excluded


def test_scan_stemmed_flags_genitive_variant():
    corpus, _ = load_seed_corpus()
    hits = scan_stemmed(corpus, "Shigella boydiae")
    assert any(h.corpus_name == "Shigella boydii" for h in hits)


def test_scan_stemmed_excludes_distant_pairs():
    corpus, _ = load_seed_corpus()
    hits = scan_stemmed(corpus, "Shigella boydii")
    assert not any(h.corpus_name == "Bacillus subtilis" for h in hits)
    assert not any(h.corpus_name == "Escherichia coli" for h in hits)


def test_scan_stemmed_sorted_by_distance():
    corpus, _ = load_seed_corpus()
    hits = scan_stemmed(corpus, "Wukomonas beijingense")
    distances = [h.distance for h in hits]
    assert distances == sorted(distances)


# ---------------------------------------------------------------------------
# Long / multi-word corpus entries must be reachable
# ---------------------------------------------------------------------------

def _legacy_scan(corpus_names, query, max_distance=2):
    """A deliberately naive scan, kept only to measure what the real one gains.

    Full normalized names, length-bucketed by that length, every token
    stem-stripped, no core view.
    """
    from prokname.dedup.nearmatch import bounded_levenshtein

    q = normalize_name(query)
    legacy_stem = lambda name: " ".join(  # noqa: E731 - mirrors the naive scan
        stem_latin(t) for t in name.split()
    )
    hits = []
    for entry in corpus_names:
        norm = normalize_name(entry["name"])
        if abs(len(norm) - len(q)) > max_distance:
            continue  # bucketing by full length drops long entries
        d = bounded_levenshtein(q, norm, max_distance)
        if 0 < d <= max_distance:
            hits.append(entry["name"])
        elif abs(len(legacy_stem(norm)) - len(legacy_stem(q))) <= 1:
            if legacy_stem(norm) == legacy_stem(q) and norm != q:
                hits.append(entry["name"])
    return sorted(set(hits))


@pytest.mark.parametrize("query, entry", [
    ("Bacillus subtilis", "Bacillus subtilis subsp. spizizenii"),
    ("Pelagibacter ubique", "Candidatus Pelagibacter ubique"),
    ("Bacillus subtilis subsp. astrhanovicus", "Bacillus subtilis"),
])
def test_long_entries_are_reachable_by_a_short_query(query, entry):
    corpus = _corpus([entry, "Unrelated exampleum"])
    names = [h.corpus_name for h in scan(corpus, query)]
    assert entry in names, f"{entry!r} is still unreachable for {query!r}"
    assert entry not in _legacy_scan(corpus, query), (
        "the legacy scan must actually have missed it, otherwise this test "
        "proves nothing about the recall gain"
    )


def test_appendix_a7_recall_change_on_the_shipped_seed_corpus():
    """Report the recall change the fix produces (before → after)."""
    corpus, _ = load_seed_corpus()
    queries = [
        "Bacillus subtilis", "Pelagibacter ubique", "Escherichia coli",
        "Wukomonas beijingense", "Shigella boydii",
    ]
    before = {q: _legacy_scan(corpus, q) for q in queries}
    after = {q: [h.corpus_name for h in scan(corpus, q)] for q in queries}
    n_before = sum(len(v) for v in before.values())
    n_after = sum(len(v) for v in after.values())
    assert n_after > n_before, (before, after)
    # the specific A7 case: the subspecies entry is only found after the fix
    assert "Bacillus subtilis subsp. spizizenii" not in before[
        "Bacillus subtilis"]
    assert "Bacillus subtilis subsp. spizizenii" in after["Bacillus subtilis"]
    # and the candidatus entry becomes reachable for the bare binomial
    assert "Candidatus Pelagibacter ubique" in after["Pelagibacter ubique"]


def test_stemmed_scan_also_reaches_long_entries():
    corpus = _corpus(["Bacillus subtilis subsp. spizizenii"])
    names = [h.corpus_name for h in scan_stemmed(corpus, "Bacillus subtilis")]
    assert names == ["Bacillus subtilis subsp. spizizenii"]


def test_genus_stem_change_does_not_hide_a_same_genus_pair():
    """The old code stemmed 'Bacillus' → 'bacill'; both sides moved together,
    but a DIFFERENT genus of the same stem could now be confused. Verify that
    genus identity is required."""
    corpus = _corpus(["Bacillus megaterium", "Bacillatus megaterium"])
    hits = scan_stemmed(corpus, "Bacillus megateri")
    names = {h.corpus_name for h in hits}
    assert "Bacillus megaterium" in names
    assert "Bacillatus megaterium" not in names, (
        "stemming the generic name lets a different genus collapse onto the "
        "query — the that finding describes"
    )


# ---------------------------------------------------------------------------
# Content-addressed, bounded index cache
# ---------------------------------------------------------------------------

def test_index_key_is_content_based_not_identity_based():
    a = _corpus(["Syntheticus alpha", "Syntheticus beta"])
    b = _corpus(["Syntheticus alpha", "Syntheticus beta"])
    assert a is not b
    assert corpus_index_key(a) == corpus_index_key(b), (
        "equal content must reuse one index (the old id() key could not)"
    )
    scan(a, "Syntheticus alph")
    scan(b, "Syntheticus alph")
    assert index_cache_info()["entries"] == 1


def test_index_key_separates_same_length_different_content():
    a = _corpus(["Syntheticus alpha", "Syntheticus beta"])
    b = _corpus(["Artificialis gamma", "Artificialis delta"])
    assert len(a) == len(b)
    assert corpus_index_key(a) != corpus_index_key(b)


def test_index_key_ignores_entry_order():
    a = _corpus(["Syntheticus alpha", "Syntheticus beta"])
    b = list(reversed(a))
    assert corpus_index_key(a) == corpus_index_key(b)


def test_indexed_corpus_matches_its_own_content_when_interleaved():
    """The aliasing scenario that `id(list)` keys allowed.

    Two corpora of identical length, queried interleaved after the first list
    has been freed: every answer must come from the corpus that was passed
    in, never from a recycled memory address's old index.
    """
    import gc

    a = _corpus(["Syntheticus alpha", "Syntheticus beta"])
    hits_a = [h.corpus_name for h in scan(a, "Syntheticus alph")]
    ids = {id(a)}
    del a
    gc.collect()
    b = _corpus(["Artificialis gamma", "Artificialis delta"])
    address_recycled = id(b) in ids
    hits_b = [h.corpus_name for h in scan(b, "Syntheticus alph")]
    assert hits_a == ["Syntheticus alpha"]
    assert hits_b == [], (
        "corpus B was answered from corpus A's index (address recycling "
        f"{'DID' if address_recycled else 'did not'} occur here)"
    )
    # the index actually in the cache must be B's, for B's key
    index = nearmatch_mod._INDEX_CACHE[corpus_index_key(b)]
    assert {rec[0] for rec in index.records} == {"Artificialis gamma",
                                                 "Artificialis delta"}


def test_index_cache_is_bounded_and_evicts_lru():
    corpora = [_corpus([f"Syntheticus genus{i}"]) for i in range(INDEX_CACHE_MAX + 5)]
    for corpus in corpora:
        scan(corpus, "Syntheticus")
    info = index_cache_info()
    assert info["entries"] <= INDEX_CACHE_MAX, info
    assert len(info["keys"]) == info["entries"]
    # the most recent corpora are the ones kept
    assert corpus_index_key(corpora[-1]) in info["keys"]
    assert corpus_index_key(corpora[0]) not in info["keys"]


def test_index_cache_does_not_grow_without_bound_across_many_queries():
    """An unbounded cache grows by one entry per corpus, forever."""
    for i in range(25):
        scan(_corpus([f"Syntheticus wide{i}", "Syntheticus other"]),
             "Syntheticus other")
    assert index_cache_info()["entries"] <= INDEX_CACHE_MAX


def test_clear_index_cache_reports_and_empties():
    scan(_corpus(["Syntheticus alpha"]), "Syntheticus alph")
    assert index_cache_info()["entries"] == 1
    assert clear_index_cache() == 1
    assert index_cache_info()["entries"] == 0


def test_mutating_a_corpus_invalidates_its_cached_index():
    corpus = _corpus(["Syntheticus alpha"])
    assert scan(corpus, "Syntheticus alpha") == []  # exact name is excluded
    corpus.append({"name": "Syntheticus alphax", "source": "t"})
    hits = [h.corpus_name for h in scan(corpus, "Syntheticus alpha")]
    assert hits == ["Syntheticus alphax"], (
        "an in-place mutation must not be served the pre-mutation index"
    )
    assert index_cache_info()["entries"] == 2


def test_entries_field_shape_is_reported():
    scan(_corpus(["Syntheticus alpha"]), "Syntheticus")
    assert nearmatch_mod.index_cache_info()["max_entries"] == INDEX_CACHE_MAX
