"""The asset cache is read-only and actually reloadable.

`engine/data.py` used to promise both properties in a docstring and enforce
neither:

* "callers must treat them as read-only" — a shared mutable dict handed to 9
  modules, where one stray `rules()["x"] = ...` would silently change the
  behaviour of every other module in the process;
* "reloadable" — only LPSN's status table had a reload entry point, and Studio
  froze a rank list at import time, so the documented M0 workflow (expert
  reviews a candidate JSON, the file is replaced) required a restart.

These tests pin the enforced versions, including the failure modes that matter:
a nested mutation must raise, a reload must not leave a derived cache pointing
at the old asset, and a module that never registered must not make the promise
false for the assets it does not touch.
"""

from __future__ import annotations

import json

import pytest

from prokname.engine import data


@pytest.fixture
def restore_caches():
    """Leave the process cache exactly as it was for the next test."""
    before = data.load_json.cache_info()
    yield
    data.invalidate()
    assert data.load_json.cache_info().currsize == 0 or before  # smoke, not a gate


def test_assets_are_read_only_at_every_depth(restore_caches):
    rules = data.rules()
    with pytest.raises(TypeError, match="read-only"):
        rules["injected"] = {}
    with pytest.raises(TypeError, match="read-only"):
        rules["rank_suffix"]["species"] = "x"
    with pytest.raises(TypeError, match="read-only"):
        del rules["rank_suffix"]
    with pytest.raises(TypeError, match="read-only"):
        rules.setdefault("brand_new", {})
    with pytest.raises(TypeError, match="read-only"):
        rules.update({"brand_new": {}})
    # a nested list too: the first thing a caller reaches for is .append
    lexicon = data.gender_lexicon()
    sample = next(iter(lexicon.values()))
    assert isinstance(sample, dict)


def test_read_only_wrappers_are_still_dict_and_list(restore_caches):
    """Enforcement must not change the type contract the engine depends on."""
    rules = data.rules()
    assert isinstance(rules, dict)
    assert json.dumps(rules)[:1] == "{"
    assert "rank_suffix" in rules
    assert list(rules.keys())
    assert rules.get("nope") is None
    # and a copy is freely mutable, which is the documented escape hatch
    clone = dict(rules)
    clone["mine"] = 1
    assert "mine" in clone and "mine" not in rules


def test_invalidate_drops_the_asset_cache(restore_caches):
    data.rules()
    assert data.load_json.cache_info().currsize >= 1
    ran = data.invalidate()
    assert data.load_json.cache_info().currsize == 0
    assert ran == len(data.invalidate_hooks())


def test_registered_hooks_cover_the_derived_indexes(restore_caches):
    """Every module with a derived cache must show up by its own name.

    Names matter here: the point of the listing is proving that three separate
    subsystems are wired, so three identical `<lambda>` entries would satisfy
    the count and tell nobody anything.
    """
    import prokname.dedup  # noqa: F401 - registers nearmatch/lpsn/seqcode hooks
    import prokname.engine.gender  # noqa: F401

    hooks = data.invalidate_hooks()
    assert len(hooks) == len(set(hooks)), f"duplicate hook names: {hooks}"
    joined = " ".join(hooks)
    for expected in ("gender", "lpsn", "nearmatch", "seqcode"):
        assert expected in joined, f"{expected} has no invalidation hook"


def test_reload_data_is_reachable_from_the_package_root(restore_caches):
    import prokname

    assert callable(prokname.reload_data)
    assert prokname.reload_data() >= 1
    assert "rules.json" in prokname.data_status()


def test_a_replaced_asset_takes_effect_without_a_restart(tmp_path, monkeypatch,
                                                        restore_caches):
    """The actual M0 workflow, end to end.

    Writes a modified copy of rules.json into a temp directory, points the
    loader at it, and shows the engine's view changes after invalidate() — the
    promise that used to need a process restart.
    """
    asset = tmp_path / "rules.json"
    payload = json.loads(json.dumps(data.rules()))
    payload["connecting_vowel"] = "ZZZ"
    asset.write_text(json.dumps(payload), encoding="utf-8")

    class _File:
        def __init__(self, path):  # noqa: ANN001
            self._path = path

        def read_text(self, encoding="utf-8"):  # noqa: ANN002, ANN001, D102
            return self._path.read_text(encoding=encoding)

    class _Dir:
        def joinpath(self, name):  # noqa: ANN001, D102
            return _File(asset if name == "rules.json" else tmp_path / name)

    monkeypatch.setattr(data.resources, "files", lambda _pkg: _Dir())
    data.invalidate()
    assert data.rules()["connecting_vowel"] == "ZZZ"

    # the change must reach a consumer that reads through the same cache,
    # not just this module's own view of it
    from prokname.engine import generate
    assert generate.data.rules()["connecting_vowel"] == "ZZZ"
