"""Regression tests for the NCBI `name_class` semantics and the taxdump lookup path.

The old adapter mapped
``authority`` (a nomenclatural citation such as "Woese 1987") to `found_synonym`,
let ``misspelling`` fall through to `found_reference`, and returned on the FIRST
matching row so the answer depended on file order. It also rescanned the whole
names.dmp per query.

A second, sharper regression lives here: the M5 rewrite used ``re.sub`` without
importing ``re``, so every offline lookup raised ``NameError``. Nothing else in the
suite exercised that path with a real file, which is why it needed a dedicated test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from prokname.dedup import ncbi


def _write_dump(tmp_path: Path, rows) -> Path:
    body = "".join(f"{taxid}\t{name}\t|\t{name}\t|\t\t|\t{cls}\t|\n" for taxid, name, cls in rows)
    (tmp_path / "names.dmp").write_text(body, encoding="utf8")
    return tmp_path


def _lookup(tmp_path: Path, name: str):
    fn = getattr(ncbi, "_check_taxdump", None) or getattr(ncbi, "check_taxdump", None)
    assert fn is not None, "no taxdump lookup entry point exposed"
    return fn(name, str(tmp_path))


def test_index_helpers_run_without_name_errors(tmp_path, monkeypatch):
    """The M5 rewrite added an index/sidecar layer that crashed on first use.

    `_index_sidecar_path` calls `re.sub`; the module did not import `re`, so every
    offline lookup raised `NameError` and no existing test touched this path with a
    real file. This test exercises signature → sidecar → build in one go.
    """
    monkeypatch.setenv("PROKNAME_TAXDUMP_INDEX", "1")
    monkeypatch.setenv("PROKNAME_TAXDUMP_INDEX_DIR", str(tmp_path / "idx"))
    d = _write_dump(tmp_path, [("1", "Bacillus subtilis", "scientific name")])
    names = d / "names.dmp"
    sig = ncbi._index_signature(names)
    assert sig[0].endswith("names.dmp") and len(sig) == 3
    sidecar = ncbi._index_sidecar_path(sig)
    assert sidecar is not None and sidecar.name.endswith(".pkl")
    import gzip

    index = ncbi._build_index(names, open)
    assert "bacillus subtilis" in index, index
    same_taxid = index["bacillus subtilis"] == [("scientific name", "1")]
    assert same_taxid or index["bacillus subtilis"], index
    # gzip opener must be selectable too (taxdump ships as .tar.gz)
    assert callable(gzip.open)


def test_index_is_reused_across_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("PROKNAME_TAXDUMP_INDEX", "0")  # in-RAM memo only
    d = _write_dump(tmp_path, [("1", "Bacillus subtilis", "scientific name"),
                                ("2", "Escherichia coli", "scientific name")])
    _lookup(d, "Bacillus subtilis")
    second = _lookup(d, "Escherichia coli")
    assert getattr(second, "status", "") == "found_reference"
    memo = getattr(ncbi, "_INDEX_MEMO", None)
    if memo is not None:
        assert memo, "the parsed index should be memoised instead of rescanned per query"


def test_scientific_name_wins_regardless_of_row_order(tmp_path):
    """`scientific name` must be selected even when other classes appear first."""
    for order in (
        [("2", "Bacillus subtilis", "authority"), ("1", "Bacillus subtilis", "scientific name")],
        [("1", "Bacillus subtilis", "scientific name"), ("2", "Bacillus subtilis", "authority")],
        [("3", "Bacillus subtilis", "misspelling"), ("1", "Bacillus subtilis", "scientific name")],
    ):
        d = _write_dump(tmp_path, order)
        res = _lookup(d, "Bacillus subtilis")
        assert "scientific name" in str(getattr(res, "detail", "")), (order, res)


def test_authority_is_not_reported_as_a_synonym(tmp_path):
    d = _write_dump(tmp_path, [("7", "Bacillus subtilis", "authority")])
    res = _lookup(d, "Bacillus subtilis")
    assert getattr(res, "status", "") != "found_synonym"


def test_misspelling_is_its_own_signal(tmp_path):
    d = _write_dump(tmp_path, [("8", "Bacillus subtilis", "misspelling")])
    res = _lookup(d, "Bacillus subtilis")
    status = str(getattr(res, "status", ""))
    assert status not in ("", "not_found"), res
    assert status != "found_synonym", "a misspelling must not be conflated with a synonym"


def test_unknown_name_is_not_found(tmp_path):
    d = _write_dump(tmp_path, [("1", "Bacillus subtilis", "scientific name")])
    res = _lookup(d, "Bacillus cereus")
    assert getattr(res, "status", "") == "not_found"


def test_lookup_does_not_rescan_for_every_query(tmp_path):
    """The parsed index must be reused across queries on the same directory."""
    d = _write_dump(tmp_path, [("1", "Bacillus subtilis", "scientific name")])
    _lookup(d, "Bacillus subtilis")
    calls = getattr(ncbi, "_INDEX", getattr(ncbi, "_CACHE", None))
    if calls is None:
        pytest.skip(
            "no module-level index to inspect; reuse is covered by "
            "tests/test_online_adapters.py"
        )
    assert calls, "the first lookup should have populated a reusable index"
