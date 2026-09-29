"""CLI tests (typer CliRunner; offline, no network)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Skip this module cleanly when the optional CLI stack is missing instead
# of erroring at import time and interrupting the whole collection.
pytest.importorskip("typer", reason="prokname.cli requires typer")
pytest.importorskip("rich", reason="prokname.cli requires rich")
from typer.testing import CliRunner  # noqa: E402

from prokname.benchmark import evaluate_gates  # noqa: E402
from prokname.cli import app  # noqa: E402

runner = CliRunner()


def test_gen_json_output_parses_and_carries_disclaimer():
    result = runner.invoke(app, [
        "gen", "--stem", "Boyd", "--type", "person", "--rank", "species",
        "--genus", "Shigella", "--person-gender", "male", "--json",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["candidates"][0]["name"] == "Shigella boydii"
    assert "disclaimer" in payload


def test_gen_human_mode_mentions_review():
    result = runner.invoke(app, [
        "gen", "--stem", "Beijing", "--type", "place", "--rank", "species",
        "--genus", "Klebsiella",
    ])
    assert result.exit_code == 0
    # rich may wrap table cells; check tokens independently
    assert "Klebsiella" in result.output
    assert "beijingensis" in result.output


def test_gen_missing_person_gender_shows_blocked_candidate():
    result = runner.invoke(app, [
        "gen", "--stem", "Boyd", "--type", "person", "--rank", "species",
        "--genus", "Shigella", "--json",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["candidates"][0]["compliant"] is None


def test_gen_bad_input_exits_1():
    result = runner.invoke(app, [
        "gen", "--stem", "Boyd", "--type", "person", "--rank", "empire",
    ])
    assert result.exit_code == 1


def test_check_offline_blocks_with_exit_3():
    result = runner.invoke(app, ["check", "Wukomonas beijingensis"])
    assert result.exit_code == 3
    assert "blocked" in result.output


def test_check_near_match_shown_with_exit_0():
    # authorities unavailable ⇒ blocked (exit 3) is correct behaviour; ensure
    # near-match info is still rendered when present
    result = runner.invoke(app, ["check", "Escherichia colii"])
    assert result.exit_code == 3
    assert "Escherichia coli" in result.output


def test_route_mag_json():
    result = runner.invoke(app, [
        "route", "--source", "MAG", "--icnp-occupied", "no", "--json",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["viable_paths"][0]["code"] == "SeqCode"


def test_route_icnp_occupied_conflict_guidance():
    result = runner.invoke(app, [
        "route", "--source", "MAG", "--icnp-occupied", "yes",
    ])
    assert result.exit_code == 0
    assert "conflict-guidance" in result.output


def test_bench_passes():
    result = runner.invoke(app, ["bench"])
    assert result.exit_code == 0
    assert "passed" in result.output


# ---------------------------------------------------------------------------
# bench --full: artifact, gates, interval labels
# ---------------------------------------------------------------------------

def test_bench_full_writes_a_checkable_json_artifact(tmp_path):
    out = tmp_path / "nested" / "benchmark_results.json"
    result = runner.invoke(
        app, ["bench", "--full", "--json", "--output", str(out)]
    )
    assert result.exit_code in (0, 1), result.output
    assert out.exists(), "bench --full must persist the report for whoever audits the run"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert {"a_set", "b1_set", "b2_set", "c_set", "d_set", "gates_summary"} <= set(payload)
    assert payload["bench_output"]["path"] == str(out.resolve())
    eng = payload["a_set"]["a_inference"]["engine"]
    assert eng["accuracy"]["interval_method"] == "clopper_pearson_exact_binomial"
    assert eng["decidable_subset"]["macro_f1"]["interval_method"] == (
        "case_level_bootstrap_percentile"
    )
    # the JSON on stdout and the JSON on disk are the same payload
    assert json.loads(result.output) == payload


def test_bench_full_output_is_deterministic(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for path in (a, b):
        res = runner.invoke(app, ["bench", "--full", "--json", "--output", str(path)])
        assert res.exit_code in (0, 1)
    la = a.read_text(encoding="utf-8").splitlines()
    lb = b.read_text(encoding="utf-8").splitlines()
    diff = [
        (x, y) for x, y in zip(la, lb, strict=False)
        if x != y and '"run_at"' not in x
    ]
    assert not diff, f"report content must be reproducible; first diffs: {diff[:4]}"


def test_bench_full_no_output_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["bench", "--full", "--json", "--no-output"])
    assert result.exit_code in (0, 1)
    assert json.loads(result.output)["a_set"]["total_cases"] > 0
    assert not list(tmp_path.glob("*.json"))


def test_bench_full_exit_code_follows_the_gates(tmp_path, monkeypatch):
    """Untestable ≠ violated: debt is reported, and only blocks on request.

    Rewritten 2026-09-25. This asserted that the shipped seed made
    `bench --full` exit 1, which is what kept CI permanently red without
    saying anything about the code. The properties that matter now:
      * the JSON artifact carries the debt and does not claim certification;
      * the default exit code reflects violations only;
      * --require-complete makes the debt fail;
      * the exit code and the artifact come from the SAME evaluation.
    """
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["bench", "--full", "--json", "--output", "r.json"])
    payload = json.loads(result.output)
    gates = payload["gates_summary"]
    assert gates["b1_gate_status"] == "insufficient_evidence"
    assert gates["evidence_debt"] == ["b1_fnr"]
    assert gates["gates"]["b1_fnr"] is True, "debt must not read as a violation"
    assert gates["passed"] is True
    assert payload["b1_set"]["gate"]["target_statistically_certified"] is False, (
        "an untested target must never be recorded as certified"
    )
    assert result.exit_code == 0

    human = runner.invoke(app, ["bench", "--full", "--no-output"])
    assert "NOT CERTIFIED" in human.output
    assert "gate FAILED" not in human.output

    milestone = runner.invoke(app, ["bench", "--full", "--no-output",
                                    "--require-complete"])
    assert milestone.exit_code == 1

    # artifact and exit code must agree, because they are one evaluation
    milestone_json = runner.invoke(app, ["bench", "--full", "--json",
                                         "--no-output", "--require-complete"])
    assert json.loads(milestone_json.output)["gates_summary"]["passed"] is False
    assert "b1_fnr" in json.loads(milestone_json.output)["gates_summary"]["failed"]


def test_bench_full_writes_the_same_gate_block_it_exits_on(tmp_path, monkeypatch):
    """The file a report cites cannot disagree with the CI signal."""
    monkeypatch.chdir(tmp_path)
    invoked = runner.invoke(app, ["bench", "--full", "--output", "r.json"])
    written = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert written["gates_summary"]["passed"] is (invoked.exit_code == 0)
    assert written["gates_summary"] == evaluate_gates(
        written, require_complete=written["gates_summary"]["require_complete"])


def test_bench_full_prints_the_interval_kind_next_to_each_metric():
    result = runner.invoke(app, ["bench", "--full", "--no-output"])
    out = result.output
    assert "decidable macro-F1" in out
    assert "Clopper" in out and "bootstrap" in out
    # the layout defect: an accuracy CI printed on the macro-F1 line, and
    # the ghost-label value 0.75 presented as *the* macro-F1
    assert "macro-F1=0.7500" not in out


def test_bench_full_reports_b1_expansion_requirement():
    result = runner.invoke(app, ["bench", "--full", "--no-output"])
    assert "299" in result.output
    assert "insufficient_evidence" in result.output


def test_bench_gate_floor_option_is_plumbed():
    result = runner.invoke(app, ["bench", "--full", "--no-output", "--b1-min-negatives", "0"])
    assert result.exit_code in (0, 1)
    assert "0" in result.output


def test_bench_c_set_status_is_visible():
    result = runner.invoke(app, ["bench", "--full", "--no-output"])
    assert "protocol_only_never_executed" in result.output


def test_bench_full_accepts_a_gan_command_spec(tmp_path):
    fake = tmp_path / "fake-gan"
    fake.write_text("#!/bin/sh\nprintf 'Fakegenia %s\\n' \"$1\"\n", encoding="utf-8")
    fake.chmod(0o755)
    result = runner.invoke(app, [
        "bench", "--full", "--no-output",
        "--gan-command", f"{fake} {{stem}} {{seed}}",
    ])
    assert result.exit_code in (0, 1)
    assert "executed" in result.output


def test_data_assets_listing():
    result = runner.invoke(app, ["data"])
    assert result.exit_code == 0
    assert "person_genitive.json" in result.output


def test_version_flag_exits_zero():
    # regression: newer typer/click raised "Missing command." for --version
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "prokname" in result.output


def test_data_assets_json_output():
    result = runner.invoke(app, ["data", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "assets" in payload
    assert "rules.json" in payload["assets"]
    assert "disclaimer" in payload


def test_project_rate_roundtrip(tmp_path, monkeypatch):
    from prokname.storage import ProjectStore

    monkeypatch.setattr(
        ProjectStore, "__init__",
        lambda self, base_dir=None: (
            setattr(self, "base_dir", tmp_path),
            tmp_path.mkdir(parents=True, exist_ok=True),
        ) and None,
    )
    runner.invoke(app, [
        "project", "create", "demo", "--json",
    ])
    runner.invoke(app, [
        "project", "add", "demo", "--stem", "Beijing", "--type", "place",
        "--genus", "Klebsiella", "--json",
    ])
    result = runner.invoke(app, [
        "project", "rate", "demo", "--candidate", "Klebsiella beijingensis",
        "--score", "4", "--json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["score"] == 4
    show = runner.invoke(app, ["project", "show", "demo", "--json"])
    payload = json.loads(show.output)
    rated = [c for c in payload["candidates"] if c["name"] == "Klebsiella beijingensis"]
    assert rated and rated[0]["score"] == 4


def test_project_rate_missing_candidate_exits_1(tmp_path, monkeypatch):
    from prokname.storage import ProjectStore

    monkeypatch.setattr(
        ProjectStore, "__init__",
        lambda self, base_dir=None: (
            setattr(self, "base_dir", tmp_path),
            tmp_path.mkdir(parents=True, exist_ok=True),
        ) and None,
    )
    runner.invoke(app, ["project", "create", "demo2", "--json"])
    result = runner.invoke(app, [
        "project", "rate", "demo2", "--candidate", "Nope", "--score", "1", "--json",
    ])
    assert result.exit_code == 1


def test_config_persists_taxdump_dir(tmp_path, monkeypatch):
    import prokname.config as config_mod

    monkeypatch.setattr(config_mod, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "config_dir", lambda: tmp_path)
    taxdump = tmp_path / "taxdump"
    taxdump.mkdir()
    result = runner.invoke(app, [
        "config", "--taxdump-dir", str(taxdump), "--json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["config"]["taxdump_dir"] == str(taxdump)
    # second invocation sees the persisted value
    result2 = runner.invoke(app, ["config", "--json"])
    payload2 = json.loads(result2.output)
    assert payload2["env"]["PROKNAME_TAXDUMP_DIR"] == str(taxdump)


def test_project_export_json_wrapped(tmp_path, monkeypatch):
    from prokname.storage import ProjectStore

    monkeypatch.setattr(
        ProjectStore, "__init__",
        lambda self, base_dir=None: (
            setattr(self, "base_dir", tmp_path),
            tmp_path.mkdir(parents=True, exist_ok=True),
        ) and None,
    )
    runner.invoke(app, ["project", "create", "demo3", "--json"])
    result = runner.invoke(app, [
        "project", "export", "demo3", "--format", "csv", "--json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["format"] == "csv"
    assert "prokname" in payload["content"]
    assert "disclaimer" in payload


# ---------------------------------------------------------------------------
# No silent fallback for a rating that was not read back
# ---------------------------------------------------------------------------

def test_project_rate_reports_the_stored_score(tmp_path, monkeypatch):
    from prokname.storage import ProjectStore

    monkeypatch.setattr(
        ProjectStore, "__init__",
        lambda self, base_dir=None: (
            setattr(self, "base_dir", tmp_path),
            tmp_path.mkdir(parents=True, exist_ok=True),
        ) and None,
    )
    runner.invoke(app, ["project", "create", "rate1", "--json"])
    runner.invoke(app, [
        "project", "add", "rate1", "--stem", "Boyd", "--type", "person",
        "--rank", "genus", "--json",
    ])
    shown = runner.invoke(app, ["project", "show", "rate1", "--json"])
    name = json.loads(shown.output)["candidates"][0]["name"]
    result = runner.invoke(app, [
        "project", "rate", "rate1", "--candidate", name, "--score", "4", "--json",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["score"] == 4


def test_project_rate_of_unknown_candidate_is_an_error_not_a_fallback(tmp_path, monkeypatch):
    from prokname.storage import ProjectStore

    monkeypatch.setattr(
        ProjectStore, "__init__",
        lambda self, base_dir=None: (
            setattr(self, "base_dir", tmp_path),
            tmp_path.mkdir(parents=True, exist_ok=True),
        ) and None,
    )
    runner.invoke(app, ["project", "create", "rate2", "--json"])
    result = runner.invoke(app, [
        "project", "rate", "rate2", "--candidate", "NoSuch", "--score", "5", "--json",
    ])
    assert result.exit_code == 1
    payload = json.loads(result.output) if result.output.strip().startswith("{") else {}
    assert payload.get("score") != 5, "must not echo an unstored rating as if saved"


def test_piped_output_survives_a_non_utf8_console_codepage(tmp_path):
    """Redirected output must be UTF-8, whatever the console code page is.

    `check` draws tables with box-drawing characters and its rows can carry ✓ and ⚠.
    A redirected stdout on Windows falls back to the locale code page (cp1252 on a
    default install), where those characters have no byte at all — so the CLI died
    with UnicodeEncodeError on a name it had just adjudicated correctly, and a
    caller piping the report into a file got exit 1 and no verdict. Redirecting is
    how a CLI gets scripted, so the transport is fixed in the code, not by asking
    users to set PYTHONIOENCODING.

    The code page is forced here rather than assumed: the bug reproduces on any
    platform once the child's stdout is a pipe with a legacy encoding.
    """
    repo_src = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, PYTHONIOENCODING="cp1252",
               PYTHONPATH=str(repo_src) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run(
        [sys.executable, "-m", "prokname.cli", "check", "Shigella boydii"],
        capture_output=True, env=env, cwd=tmp_path, timeout=120,
    )
    stderr = proc.stderr.decode("utf-8", "replace")

    assert "UnicodeEncodeError" not in stderr, stderr[-800:]
    # 1 would mean the run died on its own output; the offline verdict is 3.
    assert proc.returncode != 1, f"the CLI failed writing its own report:\n{stderr[-800:]}"
    text = proc.stdout.decode("utf-8")  # strict: this only works if the pipe is UTF-8
    assert text.strip(), "nothing was reported at all"
    assert any(ord(c) > 127 for c in text), "the report lost its non-ASCII marks"
