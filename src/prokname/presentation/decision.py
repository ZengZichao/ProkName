"""Qt-free decision logic shared by the prokname entry points.

Everything that *decides* what a client should show — which verdict a candidate
may inherit, which badge/colour a verdict or route role renders as, whether a
compliance tick rests on inference — lives here instead of inside a widget or a
print call:

* the rules are unit-testable without a GUI (this module imports no Qt, and
  :mod:`prokname.presentation.theme` exposes its palette as plain strings), and
  both ``prokname``'s terminal output and ProkName Studio's badges read from it;
* the fail-safe direction is enforced in exactly one place. A label key with no
  colour entry renders **muted** (``STYLE_FALLBACK_COLOR``), and a verdict or
  route role with no entry renders as explicitly *unknown*. Adding a member to
  ``prokname.dedup.model.Verdict``, or a new role to the router, therefore can
  never surface as a green/optimistic badge — the worst case is "unknown", and
  the drift tests that pin it live with the clients that render the map.
"""
from __future__ import annotations

from enum import Enum
from typing import NamedTuple

from ..dedup.model import Verdict
from ..dedup.nearmatch import normalize_name
from . import theme

# --------------------------------------------------------------------------- #
# A dedup verdict may only travel to the name it was obtained for
# --------------------------------------------------------------------------- #


class CheckProvenance(NamedTuple):
    """A verdict plus the authority query and timestamp behind it."""

    verdict: str
    query: str
    checked_at: str | None = None


def verdict_value(report: object) -> str | None:
    """Verdict value of a check report, or None when there is nothing to carry."""
    verdict = getattr(report, "verdict", None)
    if verdict is None:
        return None
    if isinstance(verdict, Enum):  # Verdict.CONFLICT → "conflict"
        verdict = verdict.value
    # tolerate stand-ins that mimic an Enum member (adapters, test doubles)
    verdict = getattr(verdict, "value", verdict)
    return verdict if isinstance(verdict, str) and verdict else None


def names_agree(queried: str | None, candidate_name: str | None) -> bool:
    """True when ``candidate_name`` is the very name that was queried.

    Both sides go through the *same* normalisation the dedup layer uses for its
    own identity guard (:func:`prokname.dedup.nearmatch.normalize_name`:
    latinize + fold whitespace), so "Wukomonas beijingensis" and
    "wukomonas  BEIJINGENSIS" agree, while "Bacillus beijingensis" and
    "Wukomonas beijingensis" never do.
    """
    if not queried or not candidate_name:
        return False
    left = normalize_name(str(queried))
    right = normalize_name(str(candidate_name))
    return bool(left) and left == right


def check_provenance_for(
    report: object, candidate_name: str | None
) -> CheckProvenance | None:
    """Provenance to attach to ``candidate_name``, or None when untraceable.

    ``report`` is the session's last check (:class:`prokname.dedup.model.CheckReport`).
    Its verdict is only inherited when the report's own query normalises to the
    candidate name; anything else — a check of a different name, a report with
    no query, no report at all — yields None, so the candidate is stored
    **without** a verdict rather than carrying someone else's ruling into the
    project file and the CSV/Markdown deliverable.
    """
    if report is None:
        return None
    value = verdict_value(report)
    if value is None:
        return None
    query = getattr(report, "query", None)
    if not names_agree(query, candidate_name):
        return None
    checked_at = getattr(report, "checked_at", None)
    return CheckProvenance(
        verdict=value,
        query=str(query),
        checked_at=str(checked_at) if checked_at else None,
    )


# --------------------------------------------------------------------------- #
# Label/colour decisions, always fail-safe
# --------------------------------------------------------------------------- #


class Style(NamedTuple):
    """An i18n label key plus the palette token to render it with."""

    key: str
    color: str


#: Colour of any label whose semantic weight is unknown: neutral, never green.
STYLE_FALLBACK_COLOR = theme.COLOR_MUTED

#: Label shown when there is no verdict at all — including a verdict the UI has
#: no mapping for yet. Never an optimistic word, never a stale one.
UNKNOWN_VERDICT_KEY = "verdict_unknown"
UNKNOWN_ROLE_KEY = "role_unknown"

# i18n key → palette token. Single place where a colour is chosen, so an
# unmapped (e.g. newly introduced) key can only ever be neutral.
STYLE_COLORS: dict[str, str] = {
    # dedup verdicts — only "no clear conflict" is green, and it stays a
    # cautious green (the verdict is "absent from authorities", not "valid")
    "verdict_conflict": theme.COLOR_DANGER,
    "verdict_parahomonym_warning": theme.COLOR_WARNING,
    "verdict_verify_warning": theme.COLOR_WARNING,
    "verdict_blocked": theme.COLOR_DANGER_BRIGHT,
    "verdict_no_clear_conflict": theme.COLOR_SUCCESS,
    UNKNOWN_VERDICT_KEY: theme.COLOR_MUTED,
    # route roles — "default" is the only green: "only-viable" (MAG/SAG ⇒
    # SeqCode only) means the other code is unavailable, a constraint, not a
    # smooth path, so it must not read as success.
    "role_default": theme.COLOR_SUCCESS,
    "role_alternative": theme.COLOR_INFO,
    "role_only_viable": theme.COLOR_WARNING,
    "role_conflict_guidance": theme.COLOR_DANGER_BRIGHT,
    UNKNOWN_ROLE_KEY: theme.COLOR_MUTED,
    # compliance tri-state (+ the inference caveat)
    "compliant_yes": theme.COLOR_SUCCESS,
    "compliant_no": theme.COLOR_DANGER,
    "compliant_review": theme.COLOR_WARNING,
    "compliant_yes_inferred": theme.COLOR_WARNING,
    "compliant_yes_unverified": theme.COLOR_WARNING,
}


def style_for(key: str) -> Style:
    """Style of a label key; an unknown key is neutral, never optimistic."""
    return Style(key, STYLE_COLORS.get(key, STYLE_FALLBACK_COLOR))


_VERDICT_KEYS: dict[Verdict, str] = {
    Verdict.CONFLICT: "verdict_conflict",
    Verdict.PARAHOMONYM_WARNING: "verdict_parahomonym_warning",
    Verdict.VERIFY_WARNING: "verdict_verify_warning",
    Verdict.BLOCKED: "verdict_blocked",
    Verdict.NO_CLEAR_CONFLICT: "verdict_no_clear_conflict",
}

_ROLE_KEYS: dict[str, str] = {
    "default": "role_default",
    "alternative": "role_alternative",
    "only-viable": "role_only_viable",
    "conflict-guidance": "role_conflict_guidance",
}


def _as_verdict(verdict: object) -> Verdict | None:
    """Coerce a report's verdict field to a ``Verdict`` member, or None.

    Tolerates a plain/cached string value, but an unknown spelling stays
    unknown — it is never guessed into the nearest optimistic verdict.
    """
    if isinstance(verdict, Verdict):
        return verdict
    if isinstance(verdict, str):
        try:
            return Verdict(verdict)
        except ValueError:
            return None
    return None


def verdict_style(verdict: Verdict | None) -> Style:
    """Badge style for an engine verdict; None/unmapped → muted "unknown"."""
    member = _as_verdict(verdict)
    if member is None:
        return style_for(UNKNOWN_VERDICT_KEY)
    key = _VERDICT_KEYS.get(member)
    return style_for(key) if key else style_for(UNKNOWN_VERDICT_KEY)


def mapped_verdicts() -> set[Verdict]:
    """Every engine verdict with an explicit style (drift guard for tests)."""
    return set(_VERDICT_KEYS)


def role_style(role: str | None) -> Style:
    """Style for a router role; an unmapped role is *unknown*, not "default"."""
    if not isinstance(role, str) or not role:
        return style_for(UNKNOWN_ROLE_KEY)
    key = _ROLE_KEYS.get(role)
    return style_for(key) if key else style_for(UNKNOWN_ROLE_KEY)


def mapped_roles() -> set[str]:
    """Every route role with an explicit style (drift guard for tests)."""
    return set(_ROLE_KEYS)


# --------------------------------------------------------------------------- #
# Compliance must say whether the genus gender was inferred
# --------------------------------------------------------------------------- #

#: Categories whose epithet declines against the genus gender. For these (and
#: only these) a low-confidence inferred gender weakens the "compliant" tick.
GENDER_DEPENDENT_CATEGORIES = frozenset({"adjective"})

#: Gender modes that carry lexicon/expert authority for an agreement decision.
AUTHORITATIVE_GENDER_MODES = frozenset({"lookup", "override"})

#: Tooltip shown on a compliance cell whose "yes" rests on inference.
INFERRED_GENDER_HINT_KEY = "gen_inferred_hint"


def gender_is_authoritative(gender_mode: str | None) -> bool:
    """True when the genus gender came from the lexicon or an expert override."""
    return gender_mode in AUTHORITATIVE_GENDER_MODES


def compliance_style(
    compliant: bool | None,
    gender_mode: str | None = None,
    grammatical_category: str | None = None,
) -> Style:
    """Table style for the engine's three-state compliance verdict.

    ``compliant is True`` obtained from an *inferred* genus gender is not the
    same statement as one backed by the lexicon: inference is never authority,
    so it is labelled and coloured as review-needed instead of as a green "yes".
    The tri-state itself is untouched — ``None`` still renders as "review".
    """
    if compliant is True:
        declines_against_genus = grammatical_category in GENDER_DEPENDENT_CATEGORIES
        if declines_against_genus and not gender_is_authoritative(gender_mode):
            return style_for(
                "compliant_yes_inferred"
                if gender_mode == "inference"
                # no basis recorded at all: still not a green light
                else "compliant_yes_unverified"
            )
        return style_for("compliant_yes")
    if compliant is False:
        return style_for("compliant_no")
    # None — and anything unexpected — stays "needs review".
    return style_for("compliant_review")
