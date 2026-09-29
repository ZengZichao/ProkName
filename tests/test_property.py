"""Property-based invariants."""

import string

import pytest

from prokname.engine.generate import generate
from prokname.engine.orthography import latinize

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

ALPHABET = string.ascii_lowercase

stems = st.text(alphabet=ALPHABET, min_size=1, max_size=12)
genera = st.sampled_from(
    ["Bacillus", "Klebsiella", "Rhizobium", "Shigella", "Thermus", "Zzz"]
)


@given(stem=stems, genus=genera)
@settings(max_examples=100, deadline=None)
def test_generated_names_respect_orthography_invariants(stem, genus):
    candidates = generate(stem, "place", "species", genus=genus)
    for c in candidates:
        if c.epithet is None:
            continue
        # epithets: lowercase Latin letters only, no hyphens/diacritics/digits
        assert c.epithet == c.epithet.lower()
        assert all("a" <= ch <= "z" for ch in c.epithet)
        # genus token capitalized, rest lowercase
        tokens = c.name.split()
        assert tokens[0][0].isupper() and tokens[0][1:].islower()


@given(word=st.text(min_size=0, max_size=20))
@settings(max_examples=200, deadline=None)
def test_latinize_is_idempotent_and_ascii(word):
    once = latinize(word)
    assert latinize(once) == once
    assert all("a" <= ch <= "z" for ch in once)
