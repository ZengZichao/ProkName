"""Import-order contracts: the 18 module cycles stay loadable, packages stay acyclic.

`prokname` has module-level import cycles —
today 18 of them, all through package `__init__` facades that re-export symbols.
Python tolerates that, which is precisely the problem: tolerance makes the
*order* in which a process happens to import things an implicit contract, and a
re-export added in the wrong place breaks only the entry point that imports
first. Deleting the cycles would touch 56 modules, so the cheap, honest move is
to pin the behaviour as a regression fact.

Two independent gates live here:

1. every module under `prokname` must import as the *first and only* thing a
   fresh interpreter does (a subprocess per module, because an in-process sweep
   proves only that one ordering works); and
2. the cycles must stay *inside* packages. A package-level cycle would not be a
   tolerated re-export artifact any more — it would mean, say, `dedup` needing
   `cli`, and no import order fixes that.
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "prokname"


def _module_names() -> list[str]:
    """Every importable module below ``src/prokname``, as a dotted name."""
    names = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        *dirs, fname = path.relative_to(SRC).parts
        if fname == "__init__.py":
            names.append(".".join(("prokname", *dirs)))
        else:
            names.append(".".join(("prokname", *dirs, fname[:-3])))
    return names


MODULES = _module_names()


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_in_a_fresh_interpreter(module: str,
                                              tmp_path: pathlib.Path) -> None:
    """No module may depend on something else having been imported first.

    Nothing under ``prokname`` may need Qt: ProkName Studio is a separate project
    that depends on this one, so an engine module that imported PySide6 would
    drag a GUI into ``pip install prokname`` — and this sweep would be the place
    that quietly skipped it.
    """
    result = subprocess.run(  # noqa: S603 - interpreter we control, no shell
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, text=True, check=False, timeout=120,
        env={**os.environ,
             "PROKNAME_CACHE_DIR": str(tmp_path / "cache"),
             "XDG_CONFIG_HOME": str(tmp_path / "config")})
    assert result.returncode == 0, (
        f"{module} cannot be imported first:\n{result.stderr[-1200:]}")
    assert not result.stderr.strip(), (
        f"{module} import emitted output: {result.stderr[:400]}")


def test_the_sweep_covers_every_module_in_the_tree() -> None:
    """A parametrised test that quietly collects one case is not a sweep."""
    files = {p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts}
    # The floor is the point: ProkName Studio's 17 modules left with the split,
    # and a sweep that lost a few more would still be "green" without this.
    assert len(MODULES) == len(files) >= 35, (
        f"{len(MODULES)} module names for {len(files)} files")
    assert len(set(MODULES)) == len(MODULES), "duplicate module names"


def _package_edges() -> dict[str, set[str]]:
    """Import edges between *packages* (the dotted name under ``prokname``)."""
    edges: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        here = path.relative_to(SRC).parts
        source = here[0] if len(here) > 1 else "__kernel__"
        for node in ast.walk(tree):
            target: str | None = None
            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    # Relative: resolve against this module's package.
                    base = list(here[:-1])[:max(0, len(here) - 1 - (node.level - 1))]
                    if node.module:
                        base = [*base, *node.module.split(".")]
                    target = base[0] if base else None
                elif node.module and node.module.startswith("prokname"):
                    parts = node.module.split(".")
                    target = parts[1] if len(parts) > 1 else None
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("prokname."):
                        target = alias.name.split(".")[1]
            if target and target != source:
                edges.setdefault(source, set()).add(target)
    return edges


def test_no_package_level_import_cycles() -> None:
    """Module cycles are tolerated; package cycles would be a design break.

    ``_package_edges`` is a deliberately small re-implementation — the full
    extractor lives in ``项目架构可视化/tools/`` outside the distribution, and a
    wheel must be able to prove its own layering without that directory.
    """
    edges = _package_edges()
    assert edges, "edge extraction produced nothing"
    # Tarjan-free cycle probe: a DFS with a recursion stack.
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {node: WHITE for node in set(edges) | {t for s in edges.values() for t in s}}
    cycles: list[str] = []

    def visit(node: str, stack: tuple[str, ...]) -> None:
        colour[node] = GREY
        for nxt in sorted(edges.get(node, ())):
            if colour.get(nxt, WHITE) == GREY:
                cycles.append(" -> ".join((*stack, node, nxt)))
            elif colour.get(nxt, WHITE) == WHITE:
                visit(nxt, (*stack, node))
        colour[node] = BLACK

    for name in sorted(colour):
        if colour[name] == WHITE:
            visit(name, ())
    assert not cycles, "package-level import cycles appeared:\n  " + "\n  ".join(cycles)


def test_presentation_never_depends_on_an_entry_layer() -> None:
    """The neutral layer must stay neutral, or the fix this test guards is gone.

    ``cli -> presentation`` is allowed, and so is ProkName Studio's import of it
    from outside this package. ``presentation -> cli`` would put the shared policy
    back below one of its consumers — which is the edge this layout exists to cut.
    """
    edges = _package_edges()
    outbound = edges.get("presentation", set())
    assert "cli" not in outbound, outbound
