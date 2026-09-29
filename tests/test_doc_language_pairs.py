"""Every prose document ships in two languages, and English is the primary one.

The rule: a document exists as ``NAME.md`` (English) plus ``NAME.zh.md`` (Chinese),
each opening with ``[English](…) | [中文](…)`` and English listed first. English leads
because the repository is public and its readership is international; the Chinese
companion exists because the community the tool is written for works in Chinese.

This gate exists because the convention broke in both directions in one pass: one
design document shipped Chinese-only, and the provenance records plus
``DATA_LICENSE`` shipped English-only. An unchecked convention drifts back, and a
half-translated documentation set is worse than an honestly monolingual one — it
invites a reader to assume coverage that is not there.

Quoted upstream text (a licence footer, an API response) stays in its original
language in *both* copies; for those reference documents the Chinese companion says
so at the top, and this file checks that it does.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SWITCH_RE = re.compile(r"\[English\]\(([^)]+)\)\s*\|\s*\[中文\]\(([^)]+)\)")
CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
ASCII_LETTER_RE = re.compile(r"[A-Za-z]")

#: Where prose lives. Deliberately non-recursive below ``docs/`` except for the one
#: subdirectory that holds records, so a cache directory cannot join the census.
DOC_DIRS = [REPO, REPO / "docs", REPO / "docs" / "provenance"]

#: The two language copies do not always spell themselves the same way: the data
#: licence has no extension in English. Listed as (english, chinese) from the root.
IRREGULAR_PAIRS = [("DATA_LICENSE", "DATA_LICENSE.zh.md")]

#: Reference documents whose Chinese companion must name the English file as
#: authoritative, because they carry verbatim upstream quotations.
REFERENCE_DOCS = ["DATA_LICENSE.zh.md"]

_SWITCH_HEAD_LINES = 12


def _markdown_files() -> list[Path]:
    found: list[Path] = []
    for directory in DOC_DIRS:
        if directory.is_dir():
            found.extend(sorted(directory.glob("*.md")))
    return found


def _pairs() -> list[tuple[Path, Path]]:
    """Every (english, chinese) pair the census should contain, existing or not."""
    out: list[tuple[Path, Path]] = []
    for path in _markdown_files():
        if path.name.endswith(".zh.md"):
            continue
        out.append((path, path.with_name(path.name[: -len(".md")] + ".zh.md")))
    for en, zh in IRREGULAR_PAIRS:
        out.append((REPO / en, REPO / zh))
    return out


def test_every_english_document_has_a_chinese_companion() -> None:
    missing = sorted(
        f"{en.relative_to(REPO)} -> {zh.name}"
        for en, zh in _pairs()
        if en.is_file() and not zh.is_file()
    )
    assert not missing, (
        "these documents have no Chinese companion, so half the readership cannot "
        "read them: " + ", ".join(missing)
    )


def test_no_chinese_document_is_orphaned() -> None:
    """English is primary: a `.zh.md` with no `.md` original inverts that."""
    irregular = {zh for _, zh in IRREGULAR_PAIRS}
    orphans = sorted(
        path.relative_to(REPO).as_posix()
        for path in _markdown_files()
        if path.name.endswith(".zh.md")
        and path.name not in irregular
        and not path.with_name(path.name[: -len(".zh.md")] + ".md").is_file()
    )
    assert not orphans, (
        "English is the primary document; these have no English original: "
        + ", ".join(orphans)
    )


def test_both_copies_open_with_a_language_switch_listing_english_first() -> None:
    offenders: list[str] = []
    for en, zh in _pairs():
        for path in (en, zh):
            if not path.is_file():
                continue
            head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:_SWITCH_HEAD_LINES])
            match = SWITCH_RE.search(head)
            if match is None:
                offenders.append(
                    f"{path.relative_to(REPO)}: no `[English](…) | [中文](…)` line "
                    f"within its first {_SWITCH_HEAD_LINES} lines"
                )
                continue
            if head.find("[English]") > head.find("[中文]"):
                offenders.append(f"{path.relative_to(REPO)}: 中文 is listed before English")
    assert not offenders, "language switch missing or mis-ordered:\n  " + "\n  ".join(offenders)


def test_the_english_copy_is_english_and_the_chinese_copy_is_chinese() -> None:
    """Checked by script, not by length.

    A document that cites Chinese source names — the provenance register does — is
    legitimately partly non-Latin and still an English document, so the English side
    is judged by which script dominates rather than by any CJK being present.
    """
    problems: list[str] = []
    for en, zh in _pairs():
        for path in (en, zh):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            cjk = len(CJK_RE.findall(text))
            latin = len(ASCII_LETTER_RE.findall(text))
            is_chinese_copy = path.name.endswith(".zh.md") or path.name in {
                zh for _, zh in IRREGULAR_PAIRS
            }
            if is_chinese_copy:
                if cjk < 50:
                    problems.append(
                        f"{path.relative_to(REPO)}: only {cjk} CJK characters — "
                        "the companion is not actually translated"
                    )
            elif cjk >= latin:
                problems.append(
                    f"{path.relative_to(REPO)}: {cjk} CJK vs {latin} Latin characters — "
                    "the file named as the English original looks like the translation"
                )
    assert not problems, (
        "a document's language does not match its name:\n  " + "\n  ".join(problems)
    )


def test_reference_companions_declare_the_english_file_authoritative() -> None:
    """Upstream quotations cannot be re-translated and re-quoted, so the reader of the
    Chinese copy must be told which text the obligation is actually written in."""
    missing = [
        name
        for name in REFERENCE_DOCS
        if (REPO / name).is_file() and "权威" not in (REPO / name).read_text(encoding="utf-8")
    ]
    assert not missing, (
        "these Chinese reference documents do not state that the English original is "
        "authoritative (权威): " + ", ".join(missing)
    )
