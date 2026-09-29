"""The doc-count checker must see every spelling a count appears in.

`scripts/fill_test_counts.py --verify` is the gate that keeps the numbers in
README/USAGE/CHANGELOG honest. It once read only one spelling — a cue word *before*
the number ("collects 958 tests") — so a hand-written "1061 with `[gui]`, 954
without" in CHANGELOG sat in the docs through two commits of a green CI. These
tests pin the detection itself, because a checker with a blind spot reports
"matches reality" about the text it cannot see.

The numbers used for the pattern tests are deliberately not the live ones: what
is under test is whether a sentence is *recognised*, and coupling that to the
current collection count would make these tests go stale for the same reason the
docs did.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

#: The documents a reader acts on. CHANGELOG is absent on purpose: an entry there
#: is a dated record of what was true the day it was written.
LIVING_DOCS = ("README.md", "README.zh.md", "USAGE.md", "USAGE.zh.md",
               "CONTRIBUTING.md", "CONTRIBUTING.zh.md")

_spec = importlib.util.spec_from_file_location(
    "fill_test_counts", REPO / "scripts" / "fill_test_counts.py")
assert _spec and _spec.loader
fill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fill)

#: A stand-in for whatever the suite collects today.
COUNT = 1234


@pytest.mark.parametrize("sentence", [
    f"the run collects {COUNT} tests",
    f"安装后收集 {COUNT} 项",
    f"`.[dev]` → {COUNT} 项",
    f"即 {COUNT} tests are executed by CI",
    f"checked by CI: {COUNT} tests",
])
def test_every_documented_spelling_is_found(sentence: str) -> None:
    assert fill._counts_in(sentence) == {COUNT}


def test_a_count_is_found_whether_the_cue_comes_before_or_after() -> None:
    """Regression: the trailing-cue spelling once passed `--verify` unseen.

    CHANGELOG wrote "1061 with `[gui]`, 954 without" — the cue sat *after* the
    number, the only pattern that existed looked *before* it, and a stale figure
    reported "matches reality" for two commits.
    """
    assert fill._counts_in("collects 1061 tests") == {1061}
    assert fill._counts_in("1061 tests are collected by CI") == {1061}
    assert fill._counts_in("收集 1061 项") == {1061}


def test_a_number_that_is_not_a_count_is_not_mistaken_for_one() -> None:
    """The gate guards counts in prose, not every integer in the repository."""
    assert fill._counts_in("requires Python 3.11 and 40 MB of RAM") == set()


def test_refresh_replaces_the_documented_number() -> None:
    text = f"安装后收集 {COUNT} 项"
    assert fill._refresh_text(text, COUNT, 4321) == "安装后收集 4321 项"


def test_refresh_touches_only_the_count() -> None:
    text = f"on Python 3.11 the run collects {COUNT} tests"
    assert fill._refresh_text(text, COUNT, 4321) == \
        "on Python 3.11 the run collects 4321 tests"


def test_placeholders_are_still_recognised() -> None:
    assert fill.PATTERN.search("collects <!-- FINAL:TESTCOUNT --> tests")
    assert not fill.PATTERN.search("collects <!-- FINAL:TESTCOUNT_HEADLESS --> tests"), (
        "the headless marker belonged to the two-tier GUI matrix; if a doc still "
        "carries one, `--check` must not call it filled")


def test_the_docs_agree_with_each_other() -> None:
    """No unfilled placeholder anywhere, and one number shared by all.

    Whether that number equals *today's* collection is `--verify`'s job: it costs
    a pytest collection, so it stays a CI step instead of doubling the suite. What
    is free to check, and what this project actually got wrong, is one file being
    edited and the others left behind — hence "exactly one count, in every file
    that quotes one at all".
    """
    seen: set[int] = set()
    quoters: list[str] = []
    for name in fill.DOCS:
        path = REPO / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert not fill.PATTERN.search(text), f"{name} still has a placeholder"
        counts = fill._counts_in(text)
        if counts:
            quoters.append(name)
        seen |= counts
    assert len(seen) == 1, f"docs quote {sorted(seen)} — expected one count"
    assert len(quoters) >= 6, f"only {quoters} quote the count"


def test_no_document_still_presents_studio_as_part_of_this_package() -> None:
    """The split is only real if the living documents say so.

    CHANGELOG entries are dated records and keep their wording; the documents a
    reader acts on must not tell them to install a GUI extra that no longer
    exists, or to run a ``prokname studio`` command this package no longer has.
    """
    offenders: list[str] = []
    for name in LIVING_DOCS:
        path = REPO / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in (r"\[gui\]", r"prokname\s+studio\b", r"prokname\.studio\b"):
            if re.search(pattern, text):
                offenders.append(f"{name}: {pattern}")
    assert not offenders, f"the GUI extra survived in the living docs: {offenders}"


def test_the_living_docs_refer_to_studio_by_what_is_published() -> None:
    """ProkName Studio is another repository, not a folder beside this one.

    "Install the directory next to this one" is true on the machine it was typed
    on and wrong for every reader, the CI runner and the release build; the engine
    and Studio refer to each other by package name and repository URL.
    """
    local_path = re.compile(
        r"\.\./ProkName|ProkName-Studio-项目代码|~/Documents|/Users/|file://")
    offenders = []
    for name in LIVING_DOCS:
        path = REPO / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in local_path.finditer(text):
            offenders.append(f"{name}: …{text[max(0, match.start() - 40):match.end() + 20]}…")
    assert not offenders, f"docs describe the other project by a local path: {offenders}"
