"""Regression tests: scripts/verify_examples.py must be capable
of failing, must not double-count judgements, and must not print unformatted
placeholders.

The script is a CI gate, so these tests check the gate itself.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "verify_examples.py"


@pytest.fixture(scope="module")
def ve():
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location("ve", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    sys.modules["ve"] = module
    spec.loader.exec_module(module)
    return module


def test_offline_run_reports_and_exits_clean(ve):
    report = ve.run_verification(False)
    assert report["failed"] == 0, [
        (r["name"], r["failures"]) for r in report["results"] if not r["passed"]
    ]
    assert report["passed"] == report["total"]
    # every judgement is distinct: the old run counted 30 of fewer real cases
    assert report["distinct_judgements"] == report["total"]


def test_every_species_case_declares_an_expected_verdict(ve):
    missing = [
        c.name for c in ve.EXAMPLES
        if c.rank in ("species", "subspecies")
        and isinstance(c.expected_compliant, ve._Unset)
    ]
    assert missing == []


def test_person_cases_no_longer_pass_by_abstention(ve):
    """the person branch printed ✓ whenever compliant was None. Now the
    expectation is explicit, so a wrong abstention is a failure."""
    case = next(c for c in ve.EXAMPLES if c.name == "Shigella boydii")
    assert case.expected_compliant is True
    flipped = ve.ExampleCase(
        name=case.name, genus=case.genus, epithet=case.epithet,
        expected_gender=case.expected_gender, etymology_type="person",
        grammatical_category="genitive", person_gender="male",
        expected_compliant=None,          # deliberately wrong expectation
    )
    result = ve.verify_internal(flipped)
    assert result.passed is False
    assert any("expected compliant=None" in f for f in result.failures)


def test_a_broken_generator_fails_the_run(ve):
    """The B1 assertion has teeth: make the expectation wrong and the run fails."""
    original = ve.rules()["rank_suffix_example_type_genera"].get("Bacillaceae")
    cases = [c for c in ve.higher_rank_examples() if c.name == "Bacillaceae"]
    assert cases and cases[0].type_genus == original
    cases[0].type_genus = "Clostridium"          # Clostridiaceae ≠ Bacillaceae
    result = ve.verify_internal(cases[0])
    assert result.passed is False
    assert any("B1" in f for f in result.failures)


def test_duplicate_judgements_are_detected(ve):
    case = ve.ExampleCase("Bacillus subtilis", "Bacillus", "subtilis", "m",
                          "feature", expected_compliant=True)
    dupes = [ve.verify_internal(case), ve.verify_internal(case)]
    guard = ve._duplicate_cases(dupes)
    assert len(guard) == 1 and not guard[0].passed


def test_no_unformatted_placeholders_in_output(ve, capsys):
    """'(does not end in -{expected_suffix})' was printed literally."""
    report = ve.run_verification(False)
    rendered = "\n".join(
        c for r in report["results"] for c in r["checks"] + r["failures"]
    )
    assert "{expected_suffix}" not in rendered
    assert "{case." not in rendered
    # the conserved-name branch now names the suffix it deviates from
    bacilli = next(r for r in report["results"] if r["name"] == "Bacilli")
    assert any("-ia" in c for c in bacilli["checks"])


def test_script_exit_code_is_nonzero_on_failure(tmp_path):
    """The gate must be able to redden CI."""
    broken = tmp_path / "verify_broken.py"
    broken.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
        "import verify_examples as ve\n"
        "ve.EXAMPLES.append(ve.ExampleCase(\n"
        "    'Fake name', 'Fake', 'name', 'm', 'feature', expected_compliant=True))\n"
        "ve.main()\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(broken)], capture_output=True, text=True,
        cwd=str(tmp_path), env={"PYTHONDONTWRITEBYTECODE": "1",
                                "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAILED" in proc.stdout
