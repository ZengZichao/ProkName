"""No document may link to something the reader cannot open.

Two broken links were found by hand in one pass: the English USAGE pointed at the
Chinese file's anchor (`#10-已知局限`) inside the English document, and the Studio plan
linked to a `USAGE.zh.md` section that left the repository at the split. Both were
invisible to every other gate — the suite checks code, licence strings and test
counts, and none of them reads a markdown link.

A documentation set that is bilingual *by convention* is only auditable if something
resolves the convention: a relative path that does not exist, or an anchor that no
heading produces, is a claim about the repository that has silently gone false.

Anchors are generated the way GitHub does it — lower-cased, punctuation dropped,
spaces to hyphens, CJK kept — so a heading in either language resolves.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Directories whose markdown files are checked, recursively.
DOC_ROOTS = [REPO, REPO / "docs"]

#: Never scanned: caches, the virtualenv, and build output. Matched against path parts.
SKIP_PARTS = {
    ".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".hypothesis",
    ".mimosa", "build", "dist", "node_modules",
}

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*(```|~~~)")
#: A link written inside a code span is documentation *about* the syntax, not a link
#: to follow — CONTRIBUTING writes `[English](…) | [中文](…)` to explain the convention.
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "//")


def _links(text: str) -> list[str]:
    return LINK_RE.findall(INLINE_CODE_RE.sub("", text))


def _markdown_files() -> list[Path]:
    found: list[Path] = []
    for root in DOC_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if SKIP_PARTS & set(path.relative_to(REPO).parts):
                continue
            found.append(path)
    return found


def _anchors(text: str) -> set[str]:
    """The heading slugs GitHub would generate, ignoring anything inside a code fence."""
    out: set[str] = set()
    inside = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            inside = not inside
            continue
        if inside:
            continue
        match = re.match(r"^#{1,6}\s+(.*)$", line)
        if match:
            out.add(_slug(match.group(1)))
    return out


def _slug(heading: str) -> str:
    text = heading.strip()
    text = re.sub(r"[*`_]", "", text)  # emphasis inside a heading is not part of the slug
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return text.strip().lower().replace(" ", "-")


_ANCHOR_CACHE: dict[Path, set[str]] = {}


def _anchors_of(path: Path) -> set[str]:
    if path not in _ANCHOR_CACHE:
        _ANCHOR_CACHE[path] = _anchors(path.read_text(encoding="utf-8"))
    return _ANCHOR_CACHE[path]


def test_no_relative_link_points_at_a_missing_file() -> None:
    broken: list[str] = []
    for path in _markdown_files():
        text = path.read_text(encoding="utf-8")
        for target in _links(text):
            if target.startswith(EXTERNAL_PREFIXES):
                continue
            file_part = target.partition("#")[0]
            if not file_part:  # a bare #anchor, handled by the next test
                continue
            resolved = (path.parent / file_part).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(REPO)}: {target}")
    assert not broken, (
        "these documentation links resolve to nothing a reader can open:\n  "
        + "\n  ".join(sorted(broken))
    )


def test_every_in_page_anchor_matches_a_heading() -> None:
    broken: list[str] = []
    for path in _markdown_files():
        text = path.read_text(encoding="utf-8")
        own = _anchors_of(path)
        for target in _links(text):
            if target.startswith(EXTERNAL_PREFIXES) or "#" not in target:
                continue
            file_part, _, fragment = target.partition("#")
            if not fragment:
                continue
            if file_part:
                resolved = (path.parent / file_part).resolve()
                if not resolved.is_file():
                    continue  # the missing file is the other test's finding
                available = _anchors_of(resolved)
            else:
                available = own
            if fragment not in available:
                broken.append(f"{path.relative_to(REPO)}: {target}")
    assert not broken, (
        "these links name an anchor no heading produces (an anchor written in the "
        "other language is the classic case):\n  " + "\n  ".join(sorted(broken))
    )


def test_no_document_links_to_the_placeholder_organisation() -> None:
    for path in _markdown_files():
        assert "github.com/prokname/" not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(REPO)} still links to the placeholder organisation"
        )


#: The organisation that did not exist. `pyproject.toml`, `CITATION.cff`, the conda
#: recipe and two HTTP User-Agent strings all carried it, and the code ones hid from
#: a documentation-only check: a URL is how a reader and a crawler reach the project,
#: so a wrong one is a citation that resolves to nothing.
PLACEHOLDER = "github.com/prokname/"

#: A path that resolves on one machine only. Matched as `home/<user>/`, not as a bare
#: prefix, because the guards that forbid machine paths have to write the prefix down —
#: `tests/test_doc_counts.py` keeps a regex that mentions `/Users/`, and that is the rule
#: being stated, not a path being used.
MACHINE_PATH_RES = (
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/Volumes/[A-Za-z0-9._-]+/"),
    re.compile(r"C:\\Users\\[^\\\s]+\\"),
    re.compile(r"~/Documents/[A-Za-z0-9._-]+"),
)

#: This file has to spell the placeholder URL it forbids.
SELF = {Path(__file__).name}


def _publishable_text_files() -> list[Path]:
    """Exactly the files a commit could carry: tracked plus untracked-not-ignored.

    Asked of git rather than walked, because the working tree also holds ignored
    files — the virtualenv, the caches, and `benchmark_results.json`, which is
    generated on the developer's disk and legitimately names that disk.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO, capture_output=True, text=True, timeout=60, check=True,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        return []
    out = []
    for rel in proc.stdout.splitlines():
        path = REPO / rel
        if not path.is_file():
            continue
        try:
            path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary asset, not a place a URL hides
        out.append(path)
    return out


def test_no_published_file_references_the_placeholder_organisation() -> None:
    offenders = [
        path.relative_to(REPO).as_posix()
        for path in _publishable_text_files()
        if path.name not in SELF and PLACEHOLDER in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"'{PLACEHOLDER}' is not a repository anyone can reach; it still appears in: "
        + ", ".join(sorted(offenders))
    )


def test_no_published_file_carries_a_machine_specific_path() -> None:
    offenders = []
    for path in _publishable_text_files():
        if path.name in SELF:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in MACHINE_PATH_RES:
            hit = pattern.search(text)
            if hit:
                offenders.append(f"{path.relative_to(REPO)}: {hit.group(0)}")
    assert not offenders, (
        "these files resolve only on one developer's disk: "
        + ", ".join(sorted(offenders))
    )
