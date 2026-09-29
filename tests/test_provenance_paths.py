"""Code comments must not cite paths a user cannot open.

The repository used to explain its rules by pointing at
`ProkName-参考项目/…`, `ProkName-手稿文档/…` and `/tmp/…` — files that exist on one
machine and nowhere in a wheel. A citation that cannot be resolved is not
evidence, it is decoration, and it is how an unverifiable claim ends up quoted
as if it were one.

Provenance now lives in `docs/provenance/` inside the package source, and
`scripts/check_upstream_citations.py` re-verifies it against the pinned
upstreams. This test keeps the old habit from creeping back into code.
"""

from __future__ import annotations

import ast
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC_DIRS = [REPO / "src", REPO / "scripts"]

#: Directories that exist on the author's disk and nowhere in a distribution.
_ROOTS = r"(?:ProkName-参考项目|ProkName-手稿文档|ProkName-数据存档包|/tmp)"

#: A *citation* is a comment pointing at a specific file (optionally `:lines`).
#: A bare directory in an argument default, or a note saying a path "is gone",
#: is not a source of truth being delegated to — and this gate must not
#: confiscate the honest sentence that records where a fact used to live.
CITATION = re.compile(
    _ROOTS + r"/[\w./一-鿿-]*\.(?:py|md|json|txt|csv|toml|yaml)(?::\d+)?")


def _comment_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    """`#` comment lines and docstring lines, as (number, text)."""
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    doc_lines: set[int] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and \
                ast.get_docstring(node, clean=False) is not None:
            expr = node.body[0]
            doc_lines.update(range(expr.lineno, (expr.end_lineno or expr.lineno) + 1))
    return [(i, line) for i, line in enumerate(lines, 1)
            if line.lstrip().startswith("#") or i in doc_lines]


def _python_files() -> list[pathlib.Path]:
    return [p for d in SRC_DIRS for p in d.rglob("*.py")
            if "__pycache__" not in p.parts]


def test_no_source_file_cites_an_unresolvable_path() -> None:
    offenders: list[str] = []
    for path in _python_files():
        for number, line in _comment_lines(path):
            if CITATION.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}: "
                                 f"{line.strip()[:90]}")
    assert not offenders, (
        "code cites a file outside the distribution; move the fact into "
        "docs/provenance/ instead:\n  " + "\n  ".join(offenders))


def test_the_citation_pattern_catches_the_real_thing() -> None:
    """A gate that never fires is decoration. Negative control on the four
    shapes this project actually shipped, plus the two it did not."""
    violations = [
        "# see ProkName-参考项目/gnverifier/fuzzy-matching.md:11-23",
        '# per /tmp/prokname_fix/nomenclature_facts.md §8',
        '    """authority: ProkName-手稿文档/审阅报告.md"""',
    ]
    allowed = [
        'ap.add_argument("--out", default=str(REPO.parent / "ProkName-数据存档包"))',
        "# these observations used to sit in /tmp/prokname_fix/ and are gone",
        "# README.md:5-9 states the same thing",
    ]
    assert all(CITATION.search(line) for line in violations), violations
    assert not any(CITATION.search(line) for line in allowed), allowed


def test_the_provenance_record_ships_with_the_source() -> None:
    """The replacement for those citations must be part of the package tree."""
    provenance = REPO / "docs" / "provenance"
    files = sorted(p.name for p in provenance.glob("*.md"))
    assert files, "docs/provenance/ is empty or missing"
    assert any("upstream" in name for name in files), files
    for path in provenance.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        low = text.lower()
        assert ("verified" in low or "retrieved" in low or "复核" in text), (
            f"{path.name} states no date on which the upstream was checked")
        assert "```" in text, f"{path.name} records no re-check command"


def test_the_citation_checker_is_wired_into_ci() -> None:
    """A provenance file nobody re-verifies rots exactly like the old comments."""
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check_upstream_citations.py" in ci, (
        "the citation drift check is not running in CI")
    assert "probe_seqcode_api.py" in ci, (
        "the SeqCode endpoint contract check is not running in CI")
