"""SeqCode Registry-style etymology tables for project exports (FR-08).

The SeqCode Registry asks authors to fill a structured etymology table for
every new name (morpheme-by-morpheme: component, language, grammar,
description — see the official documentation, guide/etymology.md, snapshot
the official seqcode-documentation). prokname stores a coarse
derivation string plus grammatical category and gender per candidate; this
module renders that stored metadata in the Registry's table shape so an
export can be handed to co-authors/registrars as a DRAFT. Filling the
morpheme rows remains the author's task — prokname never invents etymology
it was not given.
"""

from __future__ import annotations

from .model import Candidate

# Grammar column in the Registry style, derived from the candidate's own
# grammatical category (and the genus gender, which drives adjective
# agreement). Genitive nouns do not decline with the genus, so their gender
# column is intentionally not echoed.
#
# "participle" is the fourth category: a participle or
# common-gender adjective (Clostridium perfringens, Streptococcus pyogenes) is a
# MODIFIER and must never be rendered as "N.L. n. in app." — that text goes
# straight into the SeqCode registration etymology table.
_ADJECTIVE_GENDER = {"m": "N.L. masc. adj.", "f": "N.L. fem. adj.", "n": "N.L. neut. adj."}
_CATEGORY_GRAMMAR = {
    "adjective": lambda gender: _ADJECTIVE_GENDER.get(gender or "", "N.L. adj."),
    "participle": lambda _gender: "N.L. part. adj.",
    "genitive": lambda _gender: "N.L. genit. n.",
    "appositive": lambda _gender: "N.L. n. in app.",
}


def _grammar_label(candidate: Candidate) -> str:
    """Grammar cell text, with an explicit unverified marker.

    A category that the engine could not assert (needs_review / compliant is
    None) is labelled '(unverified)' so a registrar never reads a proposed
    analysis as a settled one.
    """
    grammar_of = _CATEGORY_GRAMMAR.get(candidate.grammatical_category or "")
    if grammar_of is None:
        label = "N.L."
    else:
        label = grammar_of(candidate.gender)
    if candidate.grammatical_category and candidate.compliant is None:
        label += " (unverified)"
    return label


def etymology_row(candidate: Candidate) -> dict[str, str]:
    """Map one candidate onto a Registry-style etymology table row set.

    Only the 'Full word' row is filled: prokname knows the whole epithet and
    its derivation, not a verified morpheme segmentation.
    """
    grammar = _grammar_label(candidate)
    return {
        "component": candidate.name,
        "language": "N.L.",
        "grammar": grammar,
        "particle": "",
        "description": candidate.derivation or "",
    }


def render_etymology_tables(candidates: list[Candidate]) -> str:
    """Render one Registry-style Markdown table per candidate with a derivation.

    Blocked placeholders (no epithet) and candidates without any derivation
    text are skipped. Returns "" when there is nothing to render.
    """
    renderable = [
        c for c in candidates
        if c.epithet and (c.derivation or c.grammatical_category)
    ]
    if not renderable:
        return ""

    blocks: list[str] = [
        "## Etymology tables (SeqCode Registry style — DRAFT)",
        "",
        "Derived from prokname's stored derivation metadata in the Registry's",
        "etymology-table shape (component / language / grammar / description).",
        "Only the full word is pre-filled: the morpheme rows must be completed",
        "and verified by the author before name registration — prokname does",
        "not invent etymology it was not given.",
    ]
    for candidate in renderable:
        row = etymology_row(candidate)
        blocks.extend([
            "",
            f"### *{candidate.name}*",
            "",
            "| Component | Language | Grammar | Description or derivation |",
            "|-----------|----------|---------|---------------------------|",
            f"| **Full word** | {row['language']} | {row['grammar']} | {row['description']} |",
            "| *1st morpheme* | | | |",
            "| *2nd morpheme* | | | |",
            "| *3rd morpheme* | | | |",
        ])
    return "\n".join(blocks)
