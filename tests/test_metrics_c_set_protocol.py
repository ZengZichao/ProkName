"""Regression: the C-set (GAN comparison) protocol.

The suite never executed in CI (``skip_gan=True``) while its output was
reported, and three of its parts were unsound: guessed CLI flags, a
"compliance referee" that only checked capitalisation, and a pooled standard
deviation that mixed stem-to-stem spread with run-to-run noise.

These tests exercise the protocol end-to-end against a FAKE executable, so the
plumbing is covered deterministically in CI without shipping GPL-3.0 code or
pretending GAN ran.
"""

import json
import os
import stat

import pytest

from prokname.benchmark import gan_compare
from prokname.benchmark.gan_compare import (
    GAN_SEED_ENV,
    _orthographic_guard,
    _validate_command_spec,
    compliance_referee,
    evaluate_c_set,
)

FAKE_GAN = """#!/usr/bin/env python3
import os, sys
stem = sys.argv[1]
seed = os.environ.get("%(env)s", "?")
print("# fake gan: deterministic stand-in for the GPL tool")
print("Fakegenia " + stem.lower())
print("Fakegenia " + stem.lower() + "ensis")
print("badcase_" + stem)
print("Seed" + seed)
"""


# The competing-tool fixtures below are `#!/bin/sh` scripts made executable
# with S_IEXEC. Windows cannot run them at all, so the C-set's "we really
# executed the tool" cases cannot be asserted there; the protocol-only and
# refusal behaviour is still covered on every platform.
POSIX_SHELL_ONLY = pytest.mark.skipif(
    os.name != "posix",
    reason="the fake GAN tool is a POSIX shell script; Windows cannot exec it",
)

@pytest.fixture
def fake_gan(tmp_path):
    path = tmp_path / "fake-gan"
    path.write_text(FAKE_GAN % {"env": GAN_SEED_ENV}, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    return str(path)


# ---------------------------------------------------------------------------
# Command spec: no guessing
# ---------------------------------------------------------------------------

def test_no_spec_never_produces_gan_numbers():
    report = evaluate_c_set(repetitions=1, skip_gan=False)
    assert report["status"] == "protocol_only_never_executed"
    assert report["gan_invocation"] == "no_command_spec"
    assert report["feasibility_rate"]["gan"] is None
    assert report["candidate_counts"]["gan"] is None
    assert "PROTOCOL ONLY, NEVER EXECUTED" in report["protocol_executed_note"]
    # the failure is stated per stem, not silently dropped
    assert all("no GAN command spec" in r["errors"][0] for r in report["per_stem_results"])


def test_skip_gan_reports_itself_as_skipped():
    report = evaluate_c_set(repetitions=1, skip_gan=True)
    assert report["gan_invocation"] == "skipped_by_flag"
    assert report["gan_executed"] is False
    assert "GPL-3.0 isolation" in report["protocol_executed_note"]


def test_spec_without_stem_is_refused():
    """A spec that ignores the input would measure something else entirely."""
    with pytest.raises(ValueError, match="does not use the stem"):
        _validate_command_spec(["gan", "gen", "--type", "{etymology_type}"])
    with pytest.raises(ValueError, match="does not use the stem"):
        evaluate_c_set(repetitions=1, skip_gan=False, gan_command="gan gen --type {etymology_type}")


def test_spec_rejects_unknown_placeholders():
    with pytest.raises(ValueError, match="unknown placeholder"):
        _validate_command_spec(["gan", "{stem}", "--x", "{nonsense}"])


def test_spec_accepts_string_and_argv_forms():
    assert _validate_command_spec("gan gen {stem} --seed {seed}") == [
        "gan", "gen", "{stem}", "--seed", "{seed}"
    ]
    assert _validate_command_spec(["/bin/echo", "{stem}"]) == ["/bin/echo", "{stem}"]
    with pytest.raises(ValueError, match="empty GAN command spec"):
        _validate_command_spec([])


def test_missing_executable_is_an_honest_error(tmp_path):
    report = evaluate_c_set(
        repetitions=1, skip_gan=False,
        gan_command=[str(tmp_path / "no-such-gan"), "{stem}"],
    )
    assert report["status"] == "protocol_only_never_executed"
    assert report["feasibility_rate"]["gan"] is None
    errs = report["per_stem_results"][0]["gan"]["errors"]
    assert any("not found" in e or "Executable" in e for e in errs)


# ---------------------------------------------------------------------------
# Protocol execution against the fake tool
# ---------------------------------------------------------------------------

@POSIX_SHELL_ONLY
def test_protocol_runs_with_supplied_spec(fake_gan):
    report = evaluate_c_set(
        repetitions=3, base_seed=42, skip_gan=False,
        gan_command=[fake_gan, "{stem}", "{seed}"],
    )
    assert report["status"] == "executed"
    assert report["gan_executed"] is True
    assert report["feasibility_rate"]["gan"] is not None
    for stem in report["per_stem_results"]:
        gan = stem["gan"]
        assert gan["errors"] == []
        assert len(gan["candidate_counts"]) == 3
        assert all(n >= 3 for n in gan["candidate_counts"])
        # the exact command is recorded, and each repetition got its own seed
        assert len(gan["cli_commands"]) == 3
        assert any(str(42) in c for c in gan["cli_commands"])
        assert any(str(44) in c for c in gan["cli_commands"])
    # report stays JSON-serialisable
    json.dumps(report)


@POSIX_SHELL_ONLY
def test_report_is_deterministic_for_the_prokname_side(fake_gan):
    kwargs = dict(repetitions=2, base_seed=7, skip_gan=False,
                  gan_command=[fake_gan, "{stem}", "{seed}"])
    a, b = evaluate_c_set(**kwargs), evaluate_c_set(**kwargs)
    strip = lambda r: {k: v for k, v in r.items() if k != "per_stem_results"}  # noqa: E731
    assert strip(a) == strip(b)
    for x, y in zip(a["per_stem_results"], b["per_stem_results"]):
        assert x["prokname"] == y["prokname"]


@POSIX_SHELL_ONLY
def test_failing_tool_reports_error_without_fabricating_output(tmp_path):
    loser = tmp_path / "loser"
    loser.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    loser.chmod(loser.stat().st_mode | stat.S_IEXEC)
    report = evaluate_c_set(
        repetitions=1, skip_gan=False, gan_command=[str(loser), "{stem}"],
    )
    assert report["gan_executed"] is False
    assert any("exited 3" in e for e in report["per_stem_results"][0]["gan"]["errors"])


# ---------------------------------------------------------------------------
# Referee: real rules, not just capitalisation
# ---------------------------------------------------------------------------

def test_referee_honours_etymology_type():
    """Two names identical except for the etymology claim must differ.

    *Klebsiella michiganensis* is the fixture the repository itself certifies:
    the frozen engine regression seed (`engine/bench.py` VALIDATE_CASES) and
    the frozen B1 set both label it compliant under a **place** claim.  (The
    first draft of this test used *Rhizobium mongolensis*, which cannot be the
    compliant half: *Rhizobium* is neuter, so a place adjective has to read
    *mongolense* — and `engine/bench.py`, which this round must not edit,
    pins ``expect=False`` for exactly that pair.)
    """
    ok = compliance_referee("Klebsiella michiganensis", "place")
    bad = compliance_referee("Klebsiella michiganensis", "feature")
    assert ok["stage"] == "prokname_validate_agreement"
    assert bad["stage"] == "prokname_validate_agreement"
    assert ok["compliant"] is True
    assert bad["compliant"] is not True
    # the two verdicts differ *because* of the claimed etymology, not the shape
    assert ok["etymology_type"] == "place"
    assert bad["etymology_type"] == "feature"


def test_referee_uses_the_engine_validator_and_reports_its_verdict():
    good = compliance_referee("Shigella boydii", "person")
    bad = compliance_referee("Shigella boydiae", "person")
    assert good["compliant"] is True
    assert bad["compliant"] is False


def test_referee_still_rejects_shape_violations():
    assert compliance_referee("escherichia coli", "feature")["stage"] == "orthographic_guard"
    assert compliance_referee("Escherichia coli", "feature")["compliant"] is not False
    assert _orthographic_guard("E. coli") is False
    assert _orthographic_guard("Escherichia coli") is True


def test_genus_rank_candidates_are_not_assessed_rather_than_passed():
    """`not_assessed` must not be counted as compliance (the 0-vs-null rule)."""
    res = compliance_referee("Wukongomonas", "feature")
    assert res["compliant"] is None
    assert res["stage"] == "not_assessed"
    report = evaluate_c_set(repetitions=1, skip_gan=True)
    prok = report["feasibility_rate"]["prokname"]
    assert prok["agreement_refereed"] is None
    assert prok["candidates_not_assessed"] > 0
    assert "not_assessed" in json.dumps(report)


def test_referee_declares_itself_not_independent():
    report = evaluate_c_set(repetitions=1, skip_gan=True)
    ref = report["compliance_referee"]
    assert ref["independent"] is False
    assert "human annotators" in ref["referee_limitations"]
    assert "validate_agreement" in " ".join(ref["stages"])
    assert ref["honours_etymology_type"] is True


# ---------------------------------------------------------------------------
# Variance bookkeeping
# ---------------------------------------------------------------------------

@POSIX_SHELL_ONLY
def test_variance_sources_are_separated(fake_gan):
    report = evaluate_c_set(
        repetitions=3, skip_gan=False, gan_command=[fake_gan, "{stem}", "{seed}"],
    )
    prok = report["candidate_counts"]["prokname"]
    gan = report["candidate_counts"]["gan"]
    for side in (prok, gan):
        assert "between_stem" in side
        assert "within_stem_run_to_run" in side
        assert "input difficulty" in side["between_stem"]["meaning"]
        assert "repeated" in side["within_stem_run_to_run"]["meaning"]
    # prokname is deterministic: run-to-run spread must be exactly zero, and
    # the report must call that a determinism check, not "stability"
    assert prok["deterministic_system"] is True
    assert prok["within_stem_run_to_run"]["stems_with_nonzero_variation"] == 0
    assert "MUST NOT be shown next to" in prok["variance_note"]
    assert gan["deterministic_system"] is False
    assert gan["within_stem_run_to_run"]["per_stem"][0]["n"] == 3
    assert "sample sd" in gan["within_stem_run_to_run"]["per_stem"][0]["estimator"]


@POSIX_SHELL_ONLY
def test_between_stem_spread_is_not_called_run_to_run():
    """The old pooled mean±std is gone: no single 'std' next to a pooled mean."""
    report = evaluate_c_set(repetitions=1, skip_gan=True)
    body = json.dumps(report["candidate_counts"])
    assert '"std"' not in body
    assert "dispersion" in body
    assert "estimator" in body


def test_module_no_longer_advertises_guessed_flags():
    src = open(gan_compare.__file__, encoding="utf-8").read()
    assert '"--stem"' not in src
    assert 'cmd = [gan_bin' not in src
    assert "Exact CLI flags to be documented" not in src


def test_c_set_is_reachable_from_the_full_run():
    """the 350-line adapter must actually execute in `bench --full`."""
    from prokname.benchmark.evaluator import run_full_benchmark

    report = run_full_benchmark(n_resamples=100, repetitions=1, skip_gan=True)
    assert report["c_set"]["suite"].startswith("C-set")
    assert report["c_set"]["total_stems"] > 0
    # even skipped, the prokname side ran and the referee ran
    assert report["c_set"]["per_stem_results"][0]["prokname"]["candidate_counts"]
