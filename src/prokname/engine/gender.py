"""Genus grammatical gender: dual-mode determination.

lookup mode    : genus_gender.json lexicon hit — authoritative, high confidence.
inference mode : ending/morpheme heuristics (gender_endings.json) — low
                 confidence, ALWAYS flagged needs_review. Never default to
                 masculine (the classic error this engine exists to prevent).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cache
from typing import Literal

from . import data
from .orthography import latinize

Mode = Literal["lookup", "inference", "unknown", "override"]
Confidence = Literal["high", "low", "none"]


class Gender(str, Enum):
    MASCULINE = "m"
    FEMININE = "f"
    NEUTER = "n"

    # short aliases (Gender.M / Gender.F / Gender.N)
    M = "m"
    F = "f"
    N = "n"


@dataclass(frozen=True)
class GenderResult:
    gender: Gender | None
    mode: Mode
    confidence: Confidence
    needs_review: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "gender": self.gender.value if self.gender else None,
            "mode": self.mode,
            "confidence": self.confidence,
            "needs_review": self.needs_review,
            "reason": self.reason,
        }


def clear_derived_caches() -> None:
    """Drop the heuristic-table memo; re-derives from data on next use.

    Registered with prokname.engine.data.on_invalidate(), so reload_data()
    refreshes the engine's derived indexes too, not just the raw assets.
    """
    _sorted_table.cache_clear()


data.on_invalidate(clear_derived_caches)


@cache
def _sorted_table(table_name: str) -> tuple[tuple[str, str], ...]:
    """Longest-first (ending, gender) pairs of a heuristic table, cached."""
    table = data.gender_heuristics().get(table_name, {})
    return tuple(sorted(table.items(), key=lambda kv: len(kv[0]), reverse=True))


def _match_ending(word: str, table_name: str) -> tuple[str, str] | None:
    """Longest-suffix match against a named ending/morpheme table."""
    for ending, gender in _sorted_table(table_name):
        if word.endswith(ending):
            return ending, gender
    return None


def gender_of(genus: str, override: Gender | None = None) -> GenderResult:
    """Determine the grammatical gender of a genus name (dual-mode)."""
    if override is not None:
        return GenderResult(
            gender=override,
            mode="override",
            confidence="high",
            needs_review=False,
            reason="gender supplied by user",
        )

    cleaned = latinize(genus).capitalize()
    lexicon = data.gender_lexicon()
    entry = lexicon.get(cleaned)
    if entry is not None:
        return GenderResult(
            gender=Gender(entry["gender"]),
            mode="lookup",
            confidence="high",
            needs_review=False,
            reason=f"lexicon hit (source: {entry.get('source', 'lpsn-seed')})",
        )

    heuristics = data.gender_heuristics()
    for table_name in ("morphemes", "exception_endings", "generic_endings"):
        if table_name not in heuristics:
            continue
        match = _match_ending(cleaned, table_name)
        if match is not None:
            ending, gender = match
            return GenderResult(
                gender=Gender(gender),
                mode="inference",
                confidence="low",
                needs_review=True,
                reason=(
                    f"{table_name} heuristic: ends in '-{ending}' → {gender}; "
                    "ending-based inference is never authoritative "
                    "(known exceptions exist, e.g. Greek -ma neuters)"
                ),
            )

    return GenderResult(
        gender=None,
        mode="unknown",
        confidence="none",
        needs_review=True,
        reason="no lexicon hit and no applicable ending heuristic; refusing to guess",
    )
