"""Shared, Qt-free presentation policy for prokname's entry points.

Why this package exists
-----------------------
`decision` and `theme` used to live inside the GUI layer. They never depended on
Qt — the whole point of both modules is that a colour or a provenance verdict can
be decided, imported and unit-tested without a GUI installed — but their location
meant the only way the CLI could reuse the single authoritative role/verdict
palette was a module-level import from the peer entry-point layer. That was the
one hard reverse edge in the dependency graph.

The rule the layout now enforces:

    cli ─        benchmark → routing → dedup → engine → storage
         ├─→ ↘        ↘          ↘
    (this package: presentation — Qt-free, shared)  ← you are here

    ProkName Studio (a separate project, depending on prokname) ─

* ``prokname.cli`` may import this package (it renders, in a terminal).
* ProkName Studio may import this package (it renders, in a window).
* Neither client may import the other, and this package may never import
  PySide6, typer, rich or Qt at runtime — not even optionally, because
  "optional" in a shared layer is how the edge comes back. Type-only imports
  under ``if TYPE_CHECKING`` are the exception.
  ``tests/test_presentation_layer.py`` fails if any of that stops holding.

`theme` is the single palette (semantic meaning → hex token) and `decision` is
the single mapping from an engine outcome (Verdict, route role, compliance
tri-state) to that palette, with a fail-safe for anything unmapped. Both clients
resolve colours through `decision`, so the terminal and the GUI cannot disagree
about what "blocked" looks like. Which hue a token takes on a light or a dark
surface is the GUI's own decision (Studio's ``appearance`` module), not a
property of the engine.
"""

from . import theme
from .decision import (
    CheckProvenance,
    Style,
    compliance_style,
    mapped_roles,
    mapped_verdicts,
    names_agree,
    role_style,
    style_for,
    verdict_style,
    verdict_value,
)

__all__ = [
    "CheckProvenance",
    "Style",
    "compliance_style",
    "mapped_roles",
    "mapped_verdicts",
    "names_agree",
    "role_style",
    "style_for",
    "theme",
    "verdict_style",
    "verdict_value",
]
