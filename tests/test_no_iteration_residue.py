"""No published file may carry internal iteration residue.

The repository is published as a single v0.1.0 release, so a reader sees one
state of the software. Three kinds of note survive from how it was built and each
one is a defect for different reasons:

* **Review-finding IDs** — `(M14)`, `N18`, `P2-1`, `B6`, `G5`, and every ID
  written as a citation (`defect M4`, `review B1`, `finding C2`). They point at
  numbered findings in review reports that are not distributed with this
  repository, so a reader cannot check what they justify: the citation delegates
  the reasoning to a document nobody can open. The comment must carry the
  reasoning itself, which is what these comments do anyway. A path may not do it
  either — `test_engine_m9_verification.py` tells a reader nothing.
* **Design-document citations** — `plan v1.3 §2.4`, `benchmark draft §4`,
  `附录 A4`, `review report §2 M5`. Same failure, same fix: an external fact is
  checkable in `docs/provenance/` with a commit pin, a licence term in
  `DATA_LICENSE`, a rule in the shipped data asset.
* **Superseded counts and `## [Unreleased]`** — a changelog that restamps its own
  history with today's test totals, or still has an unreleased heading next to a
  `date-released`, describes a state that does not exist.

Milestones (M0..M5) and benchmark sets (A / B1 / B2 / C / D) are this project's
own vocabulary and stay: the bare-ID regex below stops at M6 and skips B1..B3 and
C1..C3. A milestone or suite that is *cited* as if it were a review finding
("review B1") is still residue, which is what the citation regex is for.

Files that must name these things in order to forbid them are allow-listed, and
`docs/provenance/` is one of them: its job is to record which citations used to
live in code comments and to say what replaced them.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Review-finding ID families. M0..M5 are milestones, B1..B3 / C1..C3 / A / D are
#: benchmark sets, so neither range is in here.
FINDING_ID = re.compile(
    r"\b(?:N\d{1,2}|M(?:[6-9]|1[0-9])[a-z]?|B[4-6]|C[4-9]|G\d{1,2}|P[1-3]-\d+)\b")

#: Any ID written as a citation — "defect M4", "review B1", "finding C2". The
#: leading word is what makes an otherwise ambiguous M0..M5 / B1..B3 a pointer
#: into a report rather than a milestone or a benchmark suite.
CITATION_PHRASE = re.compile(
    r"\b(?:defects?|reviews?|findings?|bugs?|issues?|rounds?)\s+"
    r"(?:item\s+)?[A-Z]\d{1,2}[a-z]?\b", re.I)

#: A file named after a review round says so in its own path. B and C are left
#: out because the benchmark suites really are called B1-set and C-set.
RESIDUE_IN_PATH = re.compile(r"(?:^|_)(?:m|n|g)\d+(?:$|_|\.)", re.I)

DESIGN_DOC_CITATION = re.compile(
    r"plan v\d|plan §|engineering plan|design plan v|benchmark (design )?draft v|"
    r"review report|审阅报告|工程方案|基准测试集设计草案|code review|review round|"
    r"review item|review finding|复核项|审阅项|评审项|附录\s*[A-Z]\d", re.I)

#: `[Unreleased]` sits above a released version only while work is unpublished.
UNRELEASED_HEADING = re.compile(r"^## \[Unreleased\]", re.M)

#: Counts that were true of an older tree and must not be restamped into history.
SUPERSEDED_COUNTS = ("1152", "1061", "954", "104 Studio", "（209）", "(209)")

#: Files that have to write the forbidden spellings to enforce the rule, or whose
#: purpose is recording which citations used to exist.
ALLOWED_FILES = {
    "tests/test_no_iteration_residue.py",
    "tests/test_provenance_paths.py",
    "docs/provenance/upstream-references.md",
    "docs/provenance/upstream-references.zh.md",
}

#: A guard has to be able to fire, so the shapes it forbids are exercised here.
NEGATIVE_CONTROLS = [
    "# version handling (review item N18)",
    "# Authority adapter: LPSN (ICNP authority; plan v1.3, sections 6.1 / 6.4)",
    '"provenance": "review report §2 M4 label list"',
    "## [Unreleased]",
    "# the classifier is wrong here — defect M4, over-blocking half",
]


def _publishable_text_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, timeout=60, check=True,
    )
    out = []
    for rel in proc.stdout.splitlines():
        path = REPO / rel
        if not path.is_file() or Path(rel).as_posix() in ALLOWED_FILES:
            continue
        if Path(rel).suffix.lower() in {".svg", ".png", ".icns", ".dmp", ".so", ".pyc"}:
            continue
        try:
            path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        out.append(path)
    return out


def _publishable_paths() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, timeout=60, check=True,
    )
    return [r for r in proc.stdout.splitlines()
            if Path(r).as_posix() not in ALLOWED_FILES]


def test_the_guard_detects_the_shapes_it_forbids() -> None:
    """A gate that never fires is decoration, so prove it fires on each shape."""
    text = "\n".join(NEGATIVE_CONTROLS)
    assert FINDING_ID.search(text), "the ID family regex does not match a review ID"
    assert CITATION_PHRASE.search(text), "the citation regex does not match 'defect M4'"
    assert DESIGN_DOC_CITATION.search(text), "the citation regex does not match a plan reference"
    assert UNRELEASED_HEADING.search(text), "the changelog regex does not match [Unreleased]"
    assert RESIDUE_IN_PATH.search("tests/test_engine_m9_verification.py"), (
        "the path regex does not match a test named after a review round")


def test_no_review_finding_ids() -> None:
    offenders = []
    for path in _publishable_text_files():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            hit = FINDING_ID.search(line) or CITATION_PHRASE.search(line)
            if hit:
                offenders.append(f"{path.relative_to(REPO)}:{n} {hit.group(0)}")
    assert not offenders, (
        "these lines cite a numbered review finding that no reader can open "
        "(state the guarantee in the comment instead):\n  " + "\n  ".join(offenders[:25])
    )


def test_no_file_is_named_after_a_review_round() -> None:
    offenders = [rel for rel in _publishable_paths()
                 if RESIDUE_IN_PATH.search(Path(rel).stem)]
    assert not offenders, (
        "these paths carry a review-round number that no reader can resolve:\n  "
        + "\n  ".join(offenders)
    )


def test_no_citations_to_undistributed_design_documents() -> None:
    offenders = []
    for path in _publishable_text_files():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            hit = DESIGN_DOC_CITATION.search(line)
            if hit:
                offenders.append(f"{path.relative_to(REPO)}:{n} {hit.group(0)}")
    assert not offenders, (
        "these lines cite a document that is not distributed with this source:\n  "
        + "\n  ".join(offenders[:25])
    )


def test_changelog_is_cut_and_carries_no_superseded_counts() -> None:
    problems = []
    for name in ("CHANGELOG.md", "CHANGELOG.zh.md"):
        path = REPO / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if UNRELEASED_HEADING.search(text):
            problems.append(f"{name} still has a ## [Unreleased] heading")
        for stale in SUPERSEDED_COUNTS:
            if stale in text:
                problems.append(f"{name} restates the superseded figure {stale!r}")
    assert not problems, (
        "the changelog describes a state that does not exist:\n  "
        + "\n  ".join(problems)
    )
