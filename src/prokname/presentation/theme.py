"""The semantic colour tokens prokname's clients render verdicts with.

This module is the single place that says what a *meaning* looks like: a conflict
is red, a clean check is a cautious green, an unmapped verdict is neutral grey.
It is consumed by :mod:`prokname.presentation.decision` (which maps meanings to
tokens), by :mod:`prokname.cli` (which prints them as rich markup), and by
ProkName Studio.

What deliberately is **not** here: how a window is painted. The Fusion palette,
the stylesheet and the light / dark decision belong to Studio's own
``appearance`` module — a CLI has no window to theme, and a hue that is a
property of the surface it is drawn on cannot be part of the engine's contract.

Plain strings only, so headless logic that *decides* which token to use (see
:mod:`prokname.presentation.decision`) imports and tests without a GUI installed.
"""
from __future__ import annotations

# -- semantic tokens (badges / table foregrounds / CLI markup reuse these) ----
COLOR_SUCCESS = "#27ae60"
COLOR_WARNING = "#f39c12"
COLOR_DANGER = "#c0392b"
COLOR_DANGER_BRIGHT = "#e74c3c"
COLOR_INFO = "#2980b9"
COLOR_MUTED = "#7f8c8d"
COLOR_TEXT = "#2c3e50"
