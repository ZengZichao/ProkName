"""`--debug` surfaces the client output we swallow, and changes nothing else.

The support problem: `check --online` runs the official LPSN client
inside redirect_stdout so machine-readable output stays clean, and everything
the client said on the way to failing is then discarded. Exit code 3 cannot
distinguish "LPSN answered: not found" from "authentication never succeeded"
from "the query was rejected" — and those have completely different remedies.
"""

from __future__ import annotations

import types

import pytest

from prokname import diagnostics
from prokname.dedup import lpsn as lpsn_mod


@pytest.fixture(autouse=True)
def reset_diagnostics():
    """Never leak an enabled flag into an unrelated test's stderr."""
    before = diagnostics.enabled()
    yield
    diagnostics.configure(before)


def test_off_by_default_nothing_is_emitted(capsys):
    diagnostics.configure(False)
    diagnostics.note("lpsn", "secret client chatter")
    assert capsys.readouterr().err == ""


def test_enabled_echoes_the_swallowed_text(capsys):
    diagnostics.configure(True)
    out = capsys.readouterr().err
    assert "diagnostics enabled" in out
    assert "unchanged" in out  # says the one thing users worry about
    diagnostics.note("lpsn-client-output", "Retrying in 2s (attempt 2/2)")
    err = capsys.readouterr().err
    assert "Retrying in 2s" in err
    assert "[prokname:lpsn-client-output]" in err


def test_a_long_capture_is_truncated_not_unbounded(capsys):
    diagnostics.configure(True)
    capsys.readouterr()
    diagnostics.note("lpsn-client-output", "x" * (diagnostics.MAX_NOTE_CHARS * 3))
    err = capsys.readouterr().err
    assert "more chars]" in err
    assert len(err) < diagnostics.MAX_NOTE_CHARS + 200


def test_diagnostics_never_change_a_verdict(monkeypatch, capsys):
    """The whole point of the guard rail: this observes, it does not rule."""
    import sys

    class _Boom:
        """A client that authenticates and then fails on the request.

        `access_token` must be set: lpsn.check() raises its own
        "authentication failed" before any search when it is absent, which
        would test the wrong branch.
        """

        access_token = "token-for-the-test"

        def __init__(self, *args, **kwargs):  # noqa: ANN002,ANN003,ARG002
            pass

        def search(self, **kwargs):  # noqa: ARG002,D102
            raise RuntimeError("simulated 503")

    monkeypatch.setitem(sys.modules, "lpsn",
                        types.ModuleType("lpsn"))
    sys.modules["lpsn"].LpsnClient = _Boom
    monkeypatch.setenv("PROKNAME_LPSN_USER", "u")
    monkeypatch.setenv("PROKNAME_LPSN_PASSWORD", "p")
    diagnostics.configure(True)
    capsys.readouterr()

    result = lpsn_mod.check("Bacillus subtilis", allow_network=True)
    assert result.status == "unavailable"
    assert result.tier == "authority"
    err = capsys.readouterr().err
    assert "simulated 503" in err, "the reason must reach the operator"


def test_environment_variable_turns_it_on(monkeypatch):
    from importlib import reload

    monkeypatch.setenv("PROKNAME_DEBUG", "1")
    reload(diagnostics)
    assert diagnostics.configure() is True
    monkeypatch.setenv("PROKNAME_DEBUG", "off")
    reload(diagnostics)
    assert diagnostics.configure() is False
    reload(diagnostics)


def test_cli_check_accepts_debug_without_altering_the_exit_code():
    """The flag must not become a way to change a verdict by changing a flag."""
    pytest.importorskip("typer")
    from typer.testing import CliRunner

    from prokname.cli import app

    runner = CliRunner()
    plain = runner.invoke(app, ["check", "Wukomonas beijingensis",
                                "--no-near-match"])
    loud = runner.invoke(app, ["check", "Wukomonas beijingensis",
                               "--no-near-match", "--debug"])
    assert plain.exit_code == loud.exit_code == 3
    assert "diagnostics enabled" in loud.stderr or "diagnostics enabled" in loud.output
