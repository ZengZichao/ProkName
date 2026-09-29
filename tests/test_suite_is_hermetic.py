"""The suite must not read from, or write to, the developer's real cache.

This is a regression guard for a defect that cost a day to diagnose: tests
calling `check_name()` without the per-test `isolated_cache` fixture wrote
genuine query records into `~/.cache/prokname/`, and the NCBI index writer left
hundreds of pickles named after long-deleted pytest tmp directories
(`ncbi_index/names_index._private_var_folders_…_pytest-1051_….pkl`).

The symptom was the dangerous part, not the disk usage: a test that asserted
what an adapter answered could pass or fail depending on what an earlier run —
or a `prokname check` on the command line — had left in the cache. A suite whose
result depends on machine history cannot support a claim about a nomenclature
rule.
"""

from __future__ import annotations

import os
from pathlib import Path

from prokname.dedup import cache as cache_mod
from prokname.dedup.cache import cache_dir


def test_cache_root_is_not_the_real_user_home():
    default = Path(os.path.expanduser("~")) / ".cache" / "prokname"
    current = cache_dir()
    assert current != default, (
        "the suite is running against the developer's real cache; the session "
        "fixture in tests/conftest.py did not take effect")
    assert ".pytest" in str(current) or "prokname-home" in str(current), (
        f"cache root is neither the user default nor a tmp dir: {current}")


def test_a_plain_check_name_call_writes_nothing_outside_the_sandbox():
    """No test should have to remember to isolate; that is the point."""
    before = set(Path(cache_dir()).rglob("*.json"))
    from prokname.dedup import check_name

    report = check_name("Syntheticus guard check", online=False,
                        near_match=False, use_cache=True)
    assert report.verdict is not None
    written = set(Path(cache_dir()).rglob("*.json")) - before
    assert written, "sanity: the sandbox cache should have received the record"
    default = Path(os.path.expanduser("~")) / ".cache" / "prokname"
    for path in written:
        assert not str(path).startswith(str(default)), (
            f"test wrote into the real user cache: {path}")


def test_the_taxdump_sidecar_writer_is_redirect_too(tmp_path):
    """PROKNAME_TAXDUMP_INDEX_DIR defaults under the cache root; both moved."""
    index_dir = os.environ.get("PROKNAME_TAXDUMP_INDEX_DIR")
    assert index_dir, "the index dir is not redirected"
    assert not index_dir.startswith(str(Path(os.path.expanduser("~"))
                                        / ".cache")), index_dir
    # and the disable switch still wins when a test asks for it
    old = os.environ.pop("PROKNAME_TAXDUMP_INDEX", None)
    try:
        from prokname.dedup import ncbi as ncbi_mod
        path = ncbi_mod._index_sidecar_path((str(tmp_path / "names.dmp"), 1))
        assert path is not None and str(path).startswith(index_dir)
    finally:
        if old is None:
            os.environ.pop("PROKNAME_TAXDUMP_INDEX", None)
        else:
            os.environ["PROKNAME_TAXDUMP_INDEX"] = old


def test_cache_clear_protects_what_it_says_it_protects():
    """`clear()` deletes query records and never the checkpoint tree.

    Re-asserted here because the guard above changes *where* clearing happens
    during tests; the protection rule must not drift with the location.
    """
    root = Path(cache_dir())
    checkpoint_tree = root / "_checkpoints"
    checkpoint_tree.mkdir(parents=True, exist_ok=True)
    kept = checkpoint_tree / "rebuild.lock.json"
    kept.write_text("{}", encoding="utf-8")
    kept_root = root / "_checkpoint.json"
    kept_root.write_text("{}", encoding="utf-8")
    victim = root / "lpsn" / "Somename.offline.deadbeef.json"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text('{"_cached_at": "2026-01-01T00:00:00+00:00",'
                      '"_query": "Somename"}', encoding="utf-8")

    removed = cache_mod.clear(confirm=True)
    assert kept.exists(), "clear() deleted a file inside the _checkpoints tree"
    assert kept_root.exists(), "clear() deleted a root _checkpoint.json"
    assert removed >= 1, "clear() reported removing nothing it should have"
    assert not victim.exists(), "the query record survived clear()"
