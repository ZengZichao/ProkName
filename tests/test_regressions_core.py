"""Regression tests for the engine's core correctness contract.

Each test pins one guarantee: exit-code contract, project-action input
validation, export format whitelist, cache collision/TTL/atomicity, storage
corruption tolerance and collisions, holdout normalisation, B2 top-3 metric,
independent compliance checking, confusion-matrix label coverage, bounded
edit distance, and the taxdump parser in rebuild_corpus.py.

Each test states the behaviour that must hold now and after any refactor, not
how the defect was originally found.
"""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# Skip (never interrupt collection) when the CLI stack is absent, so the
# 150-odd engine/storage/dedup cases in this file stay runnable in a slim env.
pytest.importorskip("typer", reason="prokname.cli requires typer")
pytest.importorskip("rich", reason="prokname.cli requires rich")
from typer.testing import CliRunner  # noqa: E402

from prokname.benchmark.gan_compare import (  # noqa: E402
    _orthographic_guard,
    compliance_referee,
)
from prokname.benchmark.holdout import check_holdout  # noqa: E402
from prokname.benchmark.metrics import confusion_matrix  # noqa: E402
from prokname.dedup import cache  # noqa: E402
from prokname.dedup.nearmatch import bounded_levenshtein, levenshtein  # noqa: E402
from prokname.engine.generate import generate  # noqa: E402
from prokname.storage import ProjectLoadError, ProjectStore  # noqa: E402
from prokname.storage.store import Candidate  # noqa: E402

runner = CliRunner()


def _patch_store_dir(monkeypatch, tmp_path):
    """Point ProjectStore's default dir at a tmp directory."""
    import prokname.storage.store as store_mod

    monkeypatch.setattr(
        store_mod, "_default_projects_dir", lambda: tmp_path / "projects"
    )


# ---------------------------------------------------------------------------
# CLI: exit-code contract (2 = usage error, so conflict moved to 4)
# ---------------------------------------------------------------------------

def test_usage_error_exits_2_not_conflict():
    from prokname.cli import app
    result = runner.invoke(app, ["gen"])  # missing required --stem
    assert result.exit_code == 2


def test_project_delete_without_name_is_handled_error():
    from prokname.cli import app
    result = runner.invoke(app, ["project", "delete"])
    assert result.exit_code == 1
    assert not isinstance(result.exception, AttributeError)


def test_project_show_and_export_without_name_error():
    from prokname.cli import app
    assert runner.invoke(app, ["project", "show"]).exit_code == 1
    assert runner.invoke(app, ["project", "export"]).exit_code == 1


def test_project_add_bad_type_exits_1_with_message(tmp_path, monkeypatch):
    from prokname.cli import app
    _patch_store_dir(monkeypatch, tmp_path)
    result = runner.invoke(app, [
        "project", "add", "demo", "--stem", "Boyd", "--type", "emoji",
        "--json",
    ])
    assert result.exit_code == 1
    assert "error" in result.output


def test_project_export_unknown_format_exits_1(tmp_path, monkeypatch):
    from prokname.cli import app
    _patch_store_dir(monkeypatch, tmp_path)
    runner.invoke(app, ["project", "create", "fmt", "--json"])
    result = runner.invoke(app, [
        "project", "export", "fmt", "--format", "xml", "--json",
    ])
    assert result.exit_code == 1
    assert "json|csv|markdown" in result.output


def test_check_offline_still_exits_3_blocked():
    from prokname.cli import app
    result = runner.invoke(app, ["check", "Totally unknown name"])
    assert result.exit_code == 3


def test_check_conflict_exits_4(tmp_path, monkeypatch):
    from prokname import cli
    from prokname.dedup.model import CheckReport, SourceResult, Verdict

    report = CheckReport(
        query="X y", checked_at="now",
        sources=[SourceResult(name="LPSN", status="found_valid", tier="authority")],
        verdict=Verdict.CONFLICT,
    )
    monkeypatch.setattr(cli, "check_name", lambda *a, **kw: report)
    result = runner.invoke(cli.app, ["check", "X y", "--json"])
    assert result.exit_code == 4


# ---------------------------------------------------------------------------
# Storage: corruption tolerance, name collisions, atomic writes
# ---------------------------------------------------------------------------

def test_load_corrupt_project_raises_projectloaderror(tmp_path):
    store = ProjectStore(base_dir=tmp_path)
    store.create("corrupt")
    path = store._project_path("corrupt")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectLoadError):
        store.load("corrupt")


def test_project_names_do_not_collide_after_sanitisation(tmp_path):
    store = ProjectStore(base_dir=tmp_path)
    a = store.create("my project")
    b = store.create("my_project")
    assert store._project_path("my project") != store._project_path("my_project")
    assert store.load("my project").created_at == a.created_at
    assert store.load("my_project").created_at == b.created_at
    # listing reports the stored names, not the hash-suffixed stems
    assert sorted(store.list_projects()) == ["my project", "my_project"]


def test_save_is_atomic_no_tmp_leftovers(tmp_path):
    store = ProjectStore(base_dir=tmp_path)
    store.create("atomic")
    store.add_candidate("atomic", Candidate(
        name="X y", epithet="y", rank="species", grammatical_category=None,
        gender=None, derivation="", compliant=None,
    ))
    assert list(tmp_path.glob("*.tmp")) == []
    assert store.load("atomic").candidates[0].name == "X y"


# ---------------------------------------------------------------------------
# Cache: dynamic dir, collision safety, TTL, invalidate
# ---------------------------------------------------------------------------

def test_cache_dir_resolves_env_on_every_call(monkeypatch, tmp_path):
    monkeypatch.setenv("PROKNAME_CACHE_DIR", str(tmp_path / "c1"))
    assert cache.cache_dir() == tmp_path / "c1"
    monkeypatch.setenv("PROKNAME_CACHE_DIR", str(tmp_path / "c2"))
    assert cache.cache_dir() == tmp_path / "c2"


def test_cache_spaced_and_underscored_names_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setenv("PROKNAME_CACHE_DIR", str(tmp_path))
    cache.put("lpsn", "Bacillus subtilis", {"name": "LPSN", "status": "not_found"})
    cache.put("lpsn", "Bacillus_subtilis", {"name": "LPSN", "status": "found_other"})
    assert cache.get("lpsn", "Bacillus subtilis")["status"] == "not_found"
    assert cache.get("lpsn", "Bacillus_subtilis")["status"] == "found_other"


def test_cache_ttl_expiry_prunes_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("PROKNAME_CACHE_DIR", str(tmp_path))
    cache.put("lpsn", "Old name", {"name": "LPSN", "status": "not_found"})
    path = cache._cache_path("lpsn", "Old name")
    aged = json.loads(path.read_text(encoding="utf-8"))
    aged["_cached_at"] = (
        datetime.now(UTC) - timedelta(days=8)
    ).isoformat(timespec="seconds")
    path.write_text(json.dumps(aged), encoding="utf-8")
    assert cache.get("lpsn", "Old name") is None
    assert not path.exists()


def test_cache_invalidate(tmp_path, monkeypatch):
    monkeypatch.setenv("PROKNAME_CACHE_DIR", str(tmp_path))
    cache.put("lpsn", "Gone", {"name": "LPSN", "status": "not_found"})
    assert cache.invalidate("lpsn", "Gone") is True
    assert cache.invalidate("lpsn", "Gone") is False
    assert cache.get("lpsn", "Gone") is None


def test_orchestrator_marks_cache_hits(tmp_path, monkeypatch):
    monkeypatch.setenv("PROKNAME_CACHE_DIR", str(tmp_path))
    from prokname.dedup import check_name
    # offline results are 'unavailable' and never cached; prime one instead
    cache.put("lpsn", "Cached name", {
        "name": "LPSN", "status": "not_found", "tier": "authority",
        "detail": "no LPSN record for this name", "url": "https://lpsn.dsmz.de/",
    })
    report = check_name("Cached name", online=False, near_match=False)
    lpsn = next(s for s in report.sources if s.name == "LPSN")
    assert lpsn.cached is True


# ---------------------------------------------------------------------------
# Holdout: leak detection must use the engine's own normalisation
# ---------------------------------------------------------------------------

def test_holdout_detects_diacritic_leak():
    # 'Bacïllus' latinizes to 'bacillus', which IS in the lexicon
    from prokname.benchmark.sets import ATestCase
    report = check_holdout([ATestCase(genus="Bacïllus", label="m", in_lexicon=False)])
    assert not report["passed"]
    assert "Bacïllus" in report["violations"]


# ---------------------------------------------------------------------------
# Metrics: confusion matrix must not silently drop out-of-set labels
# ---------------------------------------------------------------------------

def test_confusion_matrix_extends_labels_with_observed():
    cm = confusion_matrix(["unknown"], ["needs_review"], ["m", "f", "n"])
    assert cm["unknown"]["needs_review"] == 1


# ---------------------------------------------------------------------------
# Compliance referee: shape guard + the REAL validator
# ---------------------------------------------------------------------------

def test_compliance_shape_guard_rejects_diacritics():
    assert _orthographic_guard("Wukomonas beijingensü") is False
    assert _orthographic_guard("Wukomonas bjoern") is True
    assert _orthographic_guard("Wukomonas bei-jingensis") is False
    assert _orthographic_guard("wukomonas beijingensis") is False


def test_compliance_guard_still_accepts_genus_shape_for_c_set():
    # C-set compares single-token genus candidates: the shape guard must pass
    # them; whether the *rules* apply is a separate, explicit verdict.
    assert _orthographic_guard("Wukongomonas") is True
    assert _orthographic_guard("wukongomonas") is False
    assert _orthographic_guard("Wukongo-monas") is False


def test_compliance_referee_is_not_just_a_shape_check():
    """The pre-fix 'referee' returned True for anything capitalised and ASCII.

    It ignored `etymology_type` entirely (`del etymology_type`), so no
    feasibility number from it was compliance evidence.
    """
    # Klebsiella (feminine) + -ensis agrees; the previous fixture used
    # Rhizobium mongolensis, but Rhizobium is neuter, so that pair is genuinely
    # non-compliant and could never support `compliant is True`
    # (engine/bench.py pins expect=False for it).
    good = compliance_referee("Klebsiella michiganensis", "place")
    wrong_claim = compliance_referee("Klebsiella michiganensis", "feature")
    assert good["compliant"] is True
    assert wrong_claim["compliant"] is not True
    assert good["etymology_type"] == "place"
    # a name that passes the shape guard but breaks agreement must be rejected
    assert compliance_referee("Shigella boydiae", "person")["compliant"] is False
    assert compliance_referee("Shigella boydii", "person")["compliant"] is True


def test_compliance_referee_marks_genus_rank_as_not_assessed():
    res = compliance_referee("Wukongomonas", "feature")
    assert res["compliant"] is None
    assert res["stage"] == "not_assessed"


# ---------------------------------------------------------------------------
# Bounded edit distance: agrees with full Levenshtein, prunes distant pairs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("a", "b", "cap"),
    [("beijingensis", "beijingense", 2), ("coli", "colii", 2),
     ("aaaa", "bbbb", 2), ("", "abc", 3), ("abc", "", 1), ("abc", "abc", 0)],
)
def test_bounded_levenshtein_matches_full(a, b, cap):
    d = levenshtein(a, b)
    got = bounded_levenshtein(a, b, cap)
    if d <= cap:
        assert got == d
    else:
        assert got == cap + 1


def test_scan_length_prune_does_not_miss_hits():
    from prokname.dedup.nearmatch import load_seed_corpus, scan
    corpus, _ = load_seed_corpus()
    hits = scan(corpus, "Escherichia colii", max_distance=1)
    assert any(h.corpus_name == "Escherichia coli" for h in hits)


# ---------------------------------------------------------------------------
# rebuild_corpus taxdump parser: real names.dmp format
# ---------------------------------------------------------------------------

def _load_rebuild_corpus():
    path = Path(__file__).resolve().parent.parent / "scripts" / "rebuild_corpus.py"
    spec = importlib.util.spec_from_file_location("rebuild_corpus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_names_dmp_handles_tab_pipe_separator():
    mod = _load_rebuild_corpus()
    sample = (
        "1\t|\tall\t|\t\t|\tsynonym\t|\n"
        "2\t|\tBacteria\t|\tBacteria <bacteria>\t|\tscientific name\t|\n"
    )
    records = mod.parse_names_dmp(sample)
    assert {"taxid": "2", "name": "Bacteria", "class": "scientific name"} in records
    assert {"taxid": "1", "name": "all", "class": "synonym"} in records
    assert [r for r in records if r["class"] == "scientific name"]


# ---------------------------------------------------------------------------
# Data assets wired for transparency
# ---------------------------------------------------------------------------

def test_rate_limiter_budget_asset_is_listed():
    from prokname.engine.data import asset_status
    assert "rate_limit_budget.json" in asset_status()


def test_gender_inference_unchanged_through_cached_tables():
    from prokname.engine.gender import gender_of
    gr = gender_of("Frankomonas")
    assert gr.gender.value == "f" and gr.mode == "inference"
    gr2 = gender_of("Closteridium")
    assert gr2.gender.value == "n" and gr2.mode == "inference"


# ---------------------------------------------------------------------------
# Generation still deterministic after orthography table expansion
# ---------------------------------------------------------------------------

def test_anchor_names_still_generate():
    cands = generate("Boyd", "person", "species",
                     genus="Shigella", person_gender="male")
    assert [c.name for c in cands] == ["Shigella boydii"]
