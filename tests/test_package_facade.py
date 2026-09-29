"""Package facades must not export a name that a submodule also owns.

Re-exporting `generate` from `prokname.engine` would bind the
package attribute to a function and break `prokname.engine.generate.Candidate`;
the workaround was to export it as `generate_candidates` instead, which left
one function with two spellings across the codebase (cli.py and Studio imported
`from .engine.generate import generate`). Both states are bad, and the second
is the one that produces "is this a module or a function?" bugs at the call
site.

The rule now: a package's `__all__` may not contain a name that is also one of
its submodules. These tests check that across every subpackage, so adding a
convenient re-export to `dedup/__init__.py` the way it was done here fails
instead of becoming the new normal.
"""

from __future__ import annotations

import importlib
import pkgutil
import types

import pytest

PACKAGES = [
    "prokname",
    "prokname.engine",
    "prokname.dedup",
    "prokname.storage",
    "prokname.routing",
    "prokname.benchmark",
    "prokname.presentation",
]


def _submodule_names(pkg: types.ModuleType) -> set[str]:
    return {
        info.name
        for info in pkgutil.iter_modules(pkg.__path__)
        if not info.name.startswith("_")
    }


@pytest.mark.parametrize("pkg_name", PACKAGES)
def test_facade_never_shadows_a_submodule(pkg_name: str):
    """A package attribute must not mean two different things.

    Re-exporting the *module* under its own name (`from . import theme`) is
    fine — `prokname.presentation.theme` still resolves to the module. What
    breaks is binding that name to a non-module, which is exactly what
    re-exporting the `generate` function would have done to
    `prokname.engine.generate`.
    """
    pkg = importlib.import_module(pkg_name)
    collisions = []
    for name in sorted(set(getattr(pkg, "__all__", []))):
        if name not in _submodule_names(pkg):
            continue
        bound = getattr(pkg, name, None)
        if not isinstance(bound, types.ModuleType):
            collisions.append(f"{pkg_name}.{name} is a "
                              f"{type(bound).__name__}, not the submodule")
        elif bound.__name__ != f"{pkg_name}.{name}":
            collisions.append(f"{pkg_name}.{name} points at {bound.__name__}")
    assert not collisions, (
        "facade export shadows a submodule with a different object; import "
        "the symbol from its own module instead:\n  " + "\n  ".join(collisions)
    )


def test_everything_the_facade_exports_is_importable():
    """`__all__` that names a missing symbol is worse than no facade at all."""
    failures = []
    for pkg_name in PACKAGES:
        pkg = importlib.import_module(pkg_name)
        for name in getattr(pkg, "__all__", []):
            if not hasattr(pkg, name):
                failures.append(f"{pkg_name}.__all__ lists {name!r}")
    assert not failures, "unresolvable facade exports:\n  " + "\n  ".join(failures)


def test_engine_functions_have_exactly_one_spelling_each():
    """The generate/generate_candidates class of bug, caught structurally.

    Every callable the engine exposes must be reachable by one name only, so a
    grep for a function's callers finds all of them.
    """
    import prokname.engine as engine_pkg

    aliases = [
        name for name in dir(engine_pkg)
        if not name.startswith("_")
        and callable(getattr(engine_pkg, name))
        and getattr(getattr(engine_pkg, name), "__name__", name) != name
    ]
    assert not aliases, (
        f"these engine exports are aliases bound under a different name: "
        f"{aliases} — pick one spelling")


def test_person_genitive_siblings_share_parameter_order():
    """(stem, person_gender) in both, and the swap is now impossible to miss.

    `person_genitive_ending` used to take (person_gender, stem) — the reverse of
    `person_genitive_form`. Both arguments are strings, so a swapped call does
    not raise: it quietly returns the ending of a different name, for a tool
    whose entire product is a nomenclatural ruling.
    """
    import inspect

    from prokname.engine.genitive import (
        person_genitive_ending,
        person_genitive_form,
    )

    ending_params = list(inspect.signature(person_genitive_ending).parameters)
    form_params = list(inspect.signature(person_genitive_form).parameters)
    assert ending_params[:2] == form_params[:2] == ["stem", "person_gender"], (
        f"the two siblings drifted apart again: ending{tuple(ending_params)} "
        f"vs form{tuple(form_params)}")
    # and behaviour agrees, which is the point of keeping the wrapper
    assert (person_genitive_ending("boyd", "male")
            == person_genitive_form("boyd", "male").ending)
    assert (person_genitive_ending("Gordon")
            == person_genitive_form("Gordon").ending)
