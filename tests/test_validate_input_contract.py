"""validate_agreement()'s argument contract, stated instead of implied.

The function's previous failure mode for a missing name was an AttributeError
raised several lines inside it, which named neither the bad argument nor the
caller. That stayed invisible while generate() was the only caller (it always
passes both, and skips the call entirely for supra-generic ranks). It became a
live hazard with the benchmark and with any future `prokname validate`, because
a Candidate for family/order legitimately carries epithet=None: the natural
"check everything the engine produced" loop would crash on inspection instead
of being refused with a reason.
"""

from __future__ import annotations

import pytest

from prokname.engine.validate import validate_agreement


@pytest.mark.parametrize("genus,epithet,bad", [
    ("Escherichia", None, "epithet"),
    (None, "coli", "genus"),
    ("Escherichia", "", "epithet"),
    ("Escherichia", "   ", "epithet"),
    ("  ", "coli", "genus"),
    (None, None, "genus"),          # reported one argument at a time
])
def test_a_missing_name_raises_value_error_naming_the_argument(
        genus, epithet, bad):
    with pytest.raises(ValueError) as exc:
        validate_agreement(genus, epithet, "place")
    message = str(exc.value)
    assert f"non-empty {bad}" in message, message
    assert "AttributeError" not in message
    # and it says what to do instead, because "invalid input" alone is not
    # actionable for a caller holding a supra-generic Candidate
    assert "supra-generic" in message or "no specific epithet" in message


def test_a_real_call_still_works():
    result = validate_agreement("Escherichia", "coli", "thing")
    assert result.epithet == "coli"


def test_the_error_text_points_higher_ranks_at_the_right_code():
    """A family name is not a malformed species name; say so, and say where."""
    with pytest.raises(ValueError) as exc:
        validate_agreement("Escherichia", None, "place")
    assert "generate()" in str(exc.value), (
        "the message should redirect higher-rank checks to the code that "
        "handles them, not just reject the call")


def test_generate_never_passes_a_blank_name_into_the_self_check():
    """The reason this was invisible for so long, kept invisible.

    generate() self-checks every epithet it produces and must not do so for the
    supra-generic ranks, where there is no epithet at all. If a future edit
    routes those through the same call, generate() would start raising instead
    of returning a blocked candidate — this catches it here rather than in the
    engine bench.
    """
    from prokname.engine import generate as gen

    for rank in ("family", "order", "class", "phylum"):
        cands = gen.generate("bacillus", "thing", rank, genus="Escherichia")
        assert cands, f"{rank}: no candidate at all"
        for cand in cands:
            assert cand.epithet is None or isinstance(cand.epithet, str)
            assert cand.compliant in (True, False, None)
