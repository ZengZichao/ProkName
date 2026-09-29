"""The engine has no GUI, and the shared presentation layer stays importable.

Two facts this file keeps true, both of which used to be enforced against a
`prokname.studio` package that lived in this tree:

* ``prokname.cli`` renders verdicts and route roles through
  :mod:`prokname.presentation`, the one map ProkName Studio also renders from.
  The duplicated ``Verdict → colour`` dict this replaces let a verdict look urgent
  in one surface and neutral in the other.
* Nothing in this package imports Qt. Studio is now a separate project that
  depends on ``prokname``; an engine module that imported PySide6 would drag a
  GUI into ``pip install prokname``, and the dependency would point both ways.

The checks are deliberately dependency-free (no typer, no Qt): they read source
text and import stdlib only, so a violation cannot hide by being unimportable.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "prokname"

#: GUI/CLI toolkits that must never appear in the engine's import graph.
GUI_MODULES = ("PySide6", "PyQt6", "PyQt5", "qtpy", "kivy", "tkinter")


def _imports_of(module_path: Path) -> list[str]:
    """Every module the file imports, resolved to dotted names.

    Static (AST) rather than by importing, so a violation is caught even when
    it would raise on import in this environment.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative: resolve against this file's package
                pkg_parts = list(module_path.relative_to(SRC.parent).parts[:-1])
                up = node.level - 1
                base = pkg_parts[: len(pkg_parts) - up] if up else pkg_parts
                prefix = ".".join(base) + "." if base else ""
                for alias in node.names:
                    found.append(f"{prefix}{alias.name}")
            elif node.module:
                found.append(node.module)
    return found


def _engine_files():
    return (p for p in sorted(SRC.rglob("*.py")) if "__pycache__" not in p.parts)


def test_no_engine_module_imports_a_gui_toolkit():
    """`pip install prokname` must not pull Qt in, at any import depth.

    Deferred (function-body) imports count here, unlike the presentation-layer
    check below: a GUI toolkit reached from inside a function is still a runtime
    dependency, just one that fails later and less clearly.
    """
    offenders = [
        f"{path.relative_to(SRC.parent)}: {module}"
        for path in _engine_files()
        for module in _imports_of(path)
        if module.split(".")[0] in GUI_MODULES
    ]
    assert not offenders, (
        "the engine imports a GUI toolkit; ProkName Studio is a separate project "
        f"that depends on this one, not a layer inside it: {offenders}"
    )


def test_no_engine_module_imports_prokname_studio():
    """The dependency runs one way: Studio → prokname, never prokname → Studio."""
    offenders = [
        f"{path.relative_to(SRC.parent)}: {module}"
        for path in _engine_files()
        for module in _imports_of(path)
        if module == "prokname_studio" or module.startswith("prokname_studio.")
    ]
    assert not offenders, f"the engine reaches into Studio: {offenders}"


def test_presentation_package_stays_qt_and_ui_free():
    """The shared layer is only reusable while it imports nothing heavy.

    `decision` and `theme` are Qt-free at import time by design, which is what
    lets the CLI and the headless test suite use them. Only *module-level*
    imports count here, because this layer's job is to be importable anywhere;
    a top-level `from PySide6...` would put a GUI into `pip install prokname`,
    so it fails a test instead of being a comment.
    """
    forbidden = GUI_MODULES + ("typer", "rich")
    offenders = []
    for path in sorted((SRC / "presentation").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            targets = [a.name for a in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                targets.append(node.module)
            for target in targets:
                if (target or "").split(".")[0] in forbidden:
                    offenders.append(f"{path.name}:{node.lineno} imports {target}")
    assert not offenders, (
        "prokname.presentation must stay importable with no GUI or CLI "
        f"installed: {offenders}"
    )


def test_presentation_is_importable_with_no_gui_installed(monkeypatch):
    """Prove the above at runtime, the way a bare install would experience it."""
    class _Blocker:
        def find_spec(self, name, path=None, target=None):  # noqa: ANN001,ARG002
            if name.split(".")[0] in GUI_MODULES:
                raise ImportError(f"blocked for this test: {name}")
            return None

    blocker = _Blocker()
    for name in list(sys.modules):
        if name.startswith("PySide6") or name.startswith("prokname.presentation"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    sys.meta_path.insert(0, blocker)
    try:
        spec = importlib.util.find_spec("prokname.presentation.decision")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.role_style("only-viable").color.startswith("#")
        assert module.role_style("role-never-invented").key == "role_unknown"
        assert module.verdict_style(None).key == "verdict_unknown"
    finally:
        try:
            sys.meta_path.remove(blocker)
        except ValueError:  # pragma: no cover - monkeypatch already dropped it
            pass


def test_the_cli_renders_through_the_one_verdict_palette():
    """The duplicated Verdict→colour map is gone, in both directions.

    cli.py used to keep its own dict; a verdict could then look urgent in the
    GUI and neutral in the terminal. Pin that there is exactly one map and the
    CLI resolves through it.
    """
    from prokname.dedup.model import Verdict
    from prokname.presentation import decision, theme

    cli_text = (SRC / "cli.py").read_text(encoding="utf-8")
    assert "verdict_style(" in cli_text, "CLI stopped using the shared map"
    palette = {v for k, v in vars(theme).items() if k.startswith("COLOR_")}
    for verdict in Verdict:
        style = decision.verdict_style(verdict)
        assert style.color.startswith("#"), (verdict, style.color)
        assert style.color in palette, (
            f"{verdict}: colour is not a theme token, so the palette has a "
            f"hard-coded value again ({style.color})")
        assert style.key != decision.UNKNOWN_VERDICT_KEY, (
            f"{verdict}: a documented verdict renders as unknown — the shared "
            "map lost a row that both surfaces still need")
    assert decision.mapped_verdicts() == set(Verdict), (
        "a Verdict member has no palette entry; both surfaces would silently "
        "show it as unknown")
