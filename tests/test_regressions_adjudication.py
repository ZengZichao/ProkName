"""Regression tests for the adjudication and I/O contract.

Each test pins one guarantee the suite must not lose: exact-match authority
hits, malformed project files, a partial corpus rebuild that refuses to write,
local findings surfaced under BLOCKED, corpus coverage disclosure, routing
signals that survive, submodules not shadowed by a re-export, atomic writes,
endpoint gating, and the coverage gaps those cases close.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

# These modules used to import `typer.testing` at module scope, which
# turned a missing optional dependency into a collection ERROR and took the
# whole test session down with it.  importorskip degrades it to a skip.
pytest.importorskip("typer", reason="prokname.cli requires typer")
pytest.importorskip("rich", reason="prokname.cli requires rich")
from typer.testing import CliRunner  # noqa: E402

runner = CliRunner()


def _load_rebuild_corpus():
    path = Path(__file__).resolve().parent.parent / "scripts" / "rebuild_corpus.py"
    spec = importlib.util.spec_from_file_location("rebuild_corpus_round2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Structurally-corrupt project files must raise ProjectLoadError
# ---------------------------------------------------------------------------

_CORRUPT_SHAPES = {
    "valid json, not an object": [],
    "missing name": {"candidates": []},
    "name not a string": {"name": 42, "candidates": []},
    "candidates not a list": {"name": "p", "candidates": {"a": {}}},
    "candidate not an object": {"name": "p", "candidates": ["oops"]},
    "candidate missing name": {"name": "p", "candidates": [{"rank": "species"}]},
}


@pytest.mark.parametrize("payload", _CORRUPT_SHAPES.values(), ids=_CORRUPT_SHAPES)
def test_structurally_corrupt_project_raises_projectloaderror(tmp_path, payload):
    from prokname.storage import ProjectLoadError, ProjectStore

    store = ProjectStore(base_dir=tmp_path)
    path = store._project_path("broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProjectLoadError):
        store.load("broken")


@pytest.mark.parametrize("payload", _CORRUPT_SHAPES.values(), ids=_CORRUPT_SHAPES)
def test_cli_project_show_survives_structural_corruption(
    tmp_path, monkeypatch, payload
):
    """The CLI path must not raise KeyError/TypeError into the runner — the
    old behaviour leaked raw KeyError/TypeError as result.exception."""
    import prokname.storage.store as store_mod
    from prokname.cli import app
    from prokname.storage import ProjectStore

    monkeypatch.setattr(store_mod, "_default_projects_dir", lambda: tmp_path / "p")
    store = ProjectStore(base_dir=tmp_path / "p")
    path = store._project_path("broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = runner.invoke(app, ["project", "show", "broken", "--json"])
    assert result.exit_code == 1
    assert "error" in result.output.lower()
    assert not isinstance(result.exception, (KeyError, TypeError))


# ---------------------------------------------------------------------------
# LPSN substring hits must not adjudicate the query name
# ---------------------------------------------------------------------------

class _SubstringFakeLpsn:
    """Mimics the official client with the service default `contains` search:

    `search()` ignores the requested match mode (the underscore→hyphen
    mapping trap) and returns substring-style hits whose full_name does NOT
    equal the query.
    """

    auth_ok = True
    entries: list[dict] = []

    def __init__(self, user, password, public=True, max_retries=10,
                 retry_delay=50, request_timeout=300):
        print("-- Authentication successful --")
        if type(self).auth_ok:
            self.access_token = f"test-token-{id(self):x}"

    def search(self, **params):
        assert params.get("taxon_name")
        self.result = {"count": len(type(self).entries), "next": None}
        return len(type(self).entries)

    def retrieve(self, filter=None):
        yield from type(self).entries


@pytest.fixture
def fake_lpsn_substring(monkeypatch):
    monkeypatch.setenv("PROKNAME_LPSN_USER", "prokname-tester")
    monkeypatch.setenv("PROKNAME_LPSN_PASSWORD", "*" * 12)
    monkeypatch.setattr(_SubstringFakeLpsn, "entries", [], raising=False)
    module = types.ModuleType("lpsn")
    module.LpsnClient = _SubstringFakeLpsn
    monkeypatch.setitem(sys.modules, "lpsn", module)
    return _SubstringFakeLpsn


def test_lpsn_substring_hits_are_not_misread_as_occupied(monkeypatch, fake_lpsn_substring):
    """records merely CONTAINING the query must yield not_found."""
    from prokname.dedup import lpsn as lpsn_mod

    monkeypatch.setattr(fake_lpsn_substring, "entries", [
        {"full_name": "Escherichia colii subsp. coli",
         "lpsn_taxonomic_status": "correct name"},
        {"full_name": "Escherichia colii",
         "lpsn_taxonomic_status": "correct name"},
    ])
    result = lpsn_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "not_found"
    assert "substring" in result.detail


def test_lpsn_identity_match_still_classifies(monkeypatch, fake_lpsn_substring):
    """The exact-match guard must not break the true-positive path."""
    from prokname.dedup import lpsn as lpsn_mod

    monkeypatch.setattr(fake_lpsn_substring, "entries", [
        {"full_name": "Escherichia coli", "lpsn_taxonomic_status": "correct name"},
    ])
    result = lpsn_mod.check("Escherichia  coli", allow_network=True)  # extra space
    assert result.status == "found_valid"
    assert "full_name='Escherichia coli'" in result.detail


def test_lpsn_rejects_non_dict_entries_safely(monkeypatch, fake_lpsn_substring):
    from prokname.dedup import lpsn as lpsn_mod

    monkeypatch.setattr(fake_lpsn_substring, "entries", ["garbage", 42])
    result = lpsn_mod.check("Escherichia coli", allow_network=True)
    assert result.status == "not_found"


# ---------------------------------------------------------------------------
# Adjudication reachability with the REAL orchestrator
# ---------------------------------------------------------------------------

def _isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("PROKNAME_CACHE_DIR", str(tmp_path / "cache"))


def test_cli_exit4_via_real_adjudicate_not_checkname_stub(monkeypatch, tmp_path):
    """pin exit 4 through the real orchestrator path — the old test
    monkeypatched cli.check_name and verified only the mapping table."""
    from prokname import cli
    from prokname.dedup import orchestrator
    from prokname.dedup.model import SourceResult

    _isolated_cache(monkeypatch, tmp_path)

    def found(self_name, *, allow_network=False):
        return SourceResult(
            name="LPSN", status="found_valid", tier="authority",
            detail="stub: correct name",
        )

    monkeypatch.setattr(orchestrator.lpsn, "check", found)
    result = runner.invoke(cli.app, ["check", "Occupiedus preemptus", "--json"])
    assert result.exit_code == 4
    payload = json.loads(result.output)
    assert payload["verdict"] == "conflict"


def test_blocked_verdict_still_reports_local_near_matches(monkeypatch, tmp_path):
    """under BLOCKED, the local near-match signal must stay visible
    (warning listing the hits) — never silently dropped."""
    from prokname.dedup import Verdict, check_name

    _isolated_cache(monkeypatch, tmp_path)
    report = check_name("Wukomonas beijingensis", online=False, near_match=True)
    assert report.verdict is Verdict.BLOCKED
    assert report.near_matches, "seed corpus contains a distance-2 neighbour"
    joined = " ".join(report.warnings)
    assert "near-match" in joined
    assert "not a ruling" in joined
    assert "Wukomonas beijingense" in joined


def test_cli_blocked_with_near_matches_exits_3_and_lists_hits(monkeypatch, tmp_path):
    from prokname.cli import app

    _isolated_cache(monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["check", "Wukomonas beijingensis", "--json"]
    )
    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["verdict"] == "blocked"
    assert payload["near_matches"]
    assert any("not a ruling" in w for w in payload["warnings"])


# ---------------------------------------------------------------------------
# The coverage boundary (corpus size / truncation) is disclosed
# ---------------------------------------------------------------------------

def test_check_report_discloses_corpus_size(monkeypatch, tmp_path):
    from prokname.dedup import check_name
    from prokname.dedup.nearmatch import load_seed_corpus

    _isolated_cache(monkeypatch, tmp_path)
    corpus, _meta = load_seed_corpus()
    report = check_name("Totally unknown name", online=False, near_match=True)
    assert report.near_match_corpus["corpus_size"] == len(corpus)
    assert report.near_match_corpus["truncated"] in (True, False)
    joined = " ".join(report.warnings)
    assert "absence of hits is not evidence of absence" in joined


# ---------------------------------------------------------------------------
# Routing must not swallow the pre-emption signal / contradictory input
# ---------------------------------------------------------------------------

def test_route_unknown_source_keeps_icnp_preemption_warning():
    from prokname.routing import route

    result = route("unknown", icnp_occupied=True)
    joined = " ".join(result.warnings)
    assert "ICNP pre-emption" in joined
    assert "occupied" in joined


def test_route_pure_culture_candidatus_flags_contradiction():
    from prokname.routing import route

    result = route("pure_culture", candidatus=True, icnp_occupied=False)
    joined = " ".join(result.warnings)
    assert "contradictor" in joined
    assert result.viable_paths  # still routed, but with the warning


# ---------------------------------------------------------------------------
# the submodule must not be shadowed by a re-exported function
# ---------------------------------------------------------------------------

def test_engine_generate_submodule_not_shadowed():
    import prokname.engine as engine_pkg
    import prokname.engine.generate as generate_mod

    # The package attribute points at the MODULE, so
    # `prokname.engine.generate.Candidate` keeps working.
    assert engine_pkg.generate is generate_mod
    assert hasattr(generate_mod, "Candidate")
    # The function has exactly ONE spelling now: `generate_candidates`, the
    # alias this facade used to export alongside it, is gone. Having both let
    # a caller import either one and not know which it held.
    assert not hasattr(engine_pkg, "generate_candidates"), (
        "the second spelling came back")
    assert "generate_candidates" not in engine_pkg.__all__
    from prokname.engine.generate import generate

    assert callable(generate)


def test_engine_dead_constant_removed():
    """the dead GENUS_GENDER_RANKS constant is gone."""
    import prokname.engine.generate as generate_mod

    assert not hasattr(generate_mod, "GENUS_GENDER_RANKS")


# ---------------------------------------------------------------------------
# The unknown-action hint must list every valid action
# ---------------------------------------------------------------------------

def test_project_unknown_action_hint_lists_rate(tmp_path, monkeypatch):
    import prokname.storage.store as store_mod
    from prokname.cli import PROJECT_ACTIONS, app

    monkeypatch.setattr(store_mod, "_default_projects_dir", lambda: tmp_path / "p")
    result = runner.invoke(app, ["project", "frobnicate"])
    assert result.exit_code == 1
    for action in PROJECT_ACTIONS:
        assert action in result.output, f"hint missing {action!r}"


# ---------------------------------------------------------------------------
# Rebuild_lpsn uses the official search→retrieve sequence and
# never silently writes an empty corpus
# ---------------------------------------------------------------------------

class _ScriptFakeLpsn:
    """Contract-faithful stub of lpsn.LpsnClient for the rebuild script."""

    def __init__(self, user, password, **kwargs):
        self.result = {}

    def search(self, **params):
        assert params.get("taxon_name"), "script must call search(taxon_name=...)"
        self.result = {"count": 1}
        return 1

    def retrieve(self, filter=None):
        assert self.result, "retrieve() before search() breaks the contract"
        yield {"full_name": "Agenus chartarum",
               "lpsn_taxonomic_status": "correct name", "id": "12345"}


class _AlwaysFailingLpsn(_ScriptFakeLpsn):
    def search(self, **params):
        raise RuntimeError("simulated outage")


@pytest.fixture
def script_env(monkeypatch, tmp_path):
    mod = _load_rebuild_corpus()
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("PROKNAME_LPSN_USER", "prokname-tester")
    monkeypatch.setenv("PROKNAME_LPSN_PASSWORD", "*" * 12)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    return mod, tmp_path / "out"


def test_rebuild_lpsn_writes_nonempty_records(script_env, monkeypatch):
    mod, out_dir = script_env
    module = types.ModuleType("lpsn")
    module.LpsnClient = _ScriptFakeLpsn
    monkeypatch.setitem(sys.modules, "lpsn", module)
    mod.rebuild_lpsn("2026-09-15", out_dir, limit=2)

    output = next(out_dir.glob("lpsn_export_*.json"))
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["count"] > 0
    assert payload["records"][0]["matches"], "matches list must not be empty"
    match = payload["records"][0]["matches"][0]
    assert match["full_name"] == "Agenus chartarum"  # dict access, not getattr
    assert match["lpsn_id"] == "12345"


def test_rebuild_lpsn_refuses_empty_corpus_with_nonzero_exit(script_env, monkeypatch):
    mod, out_dir = script_env
    module = types.ModuleType("lpsn")
    module.LpsnClient = _AlwaysFailingLpsn
    monkeypatch.setitem(sys.modules, "lpsn", module)
    with pytest.raises(SystemExit) as excinfo:
        mod.rebuild_lpsn("2026-09-15", out_dir, limit=1)
    assert excinfo.value.code == 1
    assert not out_dir.exists() or not list(out_dir.glob("lpsn_export_*.json"))


def test_rebuild_lpsn_checkpoint_and_cache_are_atomic_writes(script_env):
    """the script must use atomic writes (tempfile + os.replace)."""
    import inspect

    mod, _out = script_env
    source = inspect.getsource(mod._atomic_write_text)
    assert "os.replace" in source
    assert ".tmp" in source
    # and the checkpoint/cache savers must route through the atomic writer
    assert "_atomic_write_json" in inspect.getsource(mod.save_checkpoint)
    assert "_atomic_write_json" in inspect.getsource(mod.save_cache)


def test_seqcode_rebuild_is_m0_gated(script_env, capsys):
    """the unverified SeqCode endpoint must never be requested."""
    mod, out_dir = script_env
    mod.rebuild_seqcode(out_dir)
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert not list(out_dir.glob("seqcode_export_*.json"))


# ---------------------------------------------------------------------------
# Check_verdict reaches the exports
# ---------------------------------------------------------------------------

def test_check_verdict_survives_csv_and_markdown_export(tmp_path):
    from prokname.storage import ProjectStore
    from prokname.storage.store import Candidate

    store = ProjectStore(base_dir=tmp_path)
    store.add_candidate("demo", Candidate(
        name="Xus y", epithet="y", rank="species", grammatical_category=None,
        gender=None, derivation="", compliant=None, check_verdict="blocked",
    ))
    csv_text = store.export_csv("demo")
    md_text = store.export_markdown("demo")
    assert "check_verdict" in csv_text
    assert "blocked" in csv_text
    assert "Check verdict" in md_text
    assert "blocked" in md_text
