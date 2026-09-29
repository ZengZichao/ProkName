"""Deterministic etymology-to-name engine.

Facade export rule: this package re-exports only names that do not
collide with a submodule. `prokname.engine.generate` is therefore the
*module*, and the function is reached as `prokname.engine.generate.generate`.

The rule exists because re-exporting the function as `generate` would shadow
the submodule attribute and break `prokname.engine.generate.Candidate`-style
access, and because the previous workaround — aliasing it to
`generate_candidates` here — left the same function with two spellings:
`from .engine import generate_candidates` inside the package and
`from .engine.generate import generate` in cli.py and Studio. Two spellings is
how a caller ends up with a module where it expected a callable, silently.

tests/test_package_facade.py enforces the rule for every subpackage.
"""

from .categories import EtymologyType, GrammaticalCategory, categories_for
from .gender import Gender, GenderResult, gender_of
from .generate import Candidate, GenitiveStem, genitive_stem_of
from .genitive import (
    GenitiveCellUnavailable,
    GenitiveForm,
    person_genitive_ending,
    person_genitive_endings,
    person_genitive_form,
)
from .validate import ValidationResult, validate_agreement

__all__ = [
    "EtymologyType",
    "GrammaticalCategory",
    "categories_for",
    "Gender",
    "GenderResult",
    "gender_of",
    "Candidate",
    "GenitiveStem",
    "genitive_stem_of",
    "GenitiveCellUnavailable",
    "GenitiveForm",
    "person_genitive_ending",
    "person_genitive_endings",
    "person_genitive_form",
    "ValidationResult",
    "validate_agreement",
]
