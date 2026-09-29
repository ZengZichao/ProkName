"""Guard for that finding: the version is declared in exactly one place.

Why this file exists
--------------------
prokname stamps its version into *every* deliverable — the export fingerprints in
``storage/store.py`` (``tool="prokname v<version>"``), the CSV/Markdown headers
and the benchmark report. Every place that used to copy the literal by hand could
therefore ship a deliverable that contradicted the installed wheel.

After the fix the declarations split into two kinds, and this file checks both:

* *derived* — ``pyproject.toml`` (``dynamic = ["version"]`` +
  ``[tool.hatch.version]``) reads the literal out of ``src/prokname/__init__.py``;
  the tests below assert that wiring and the absence of a local copy;
* *mirrored* — ``CITATION.cff`` and ``recipes/bioconda/meta.yaml`` are rendered
  without executing this package (CFF is plain YAML; conda-build needs the
  version before the source is even fetched), so they keep a literal. Their
  mismatch is exactly what this test fails on.

ProkName Studio is a separate project that depends on this one. It freezes its own
app bundle and guards its own spec; the engine carries no bundle to check here, and
reads nothing from Studio.

Nothing here requires an installed distribution. The repository's own suite
runs from a plain checkout (``PYTHONPATH=src``), where ``importlib.metadata``
raises ``PackageNotFoundError`` by design; the one test that compares against
distribution metadata skips with that explanation instead of failing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "src" / "prokname"

INIT_FILE = PACKAGE_DIR / "__init__.py"
PYPROJECT_FILE = REPO_ROOT / "pyproject.toml"
CITATION_FILE = REPO_ROOT / "CITATION.cff"
META_FILE = REPO_ROOT / "recipes" / "bioconda" / "meta.yaml"
REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
REQUIREMENTS_DEV_FILE = REPO_ROOT / "requirements-dev.txt"
DOCKERFILE = REPO_ROOT / "Dockerfile"

#: Same regex hatchling ([tool.hatch.version]) uses. Line-anchored, so the
#: runtime re-binding of ``__version__`` further down in the module can never
#: become the build-time declaration.
VERSION_LITERAL_RE = re.compile(r'''^__version__\s*=\s*["']([^"']+)["']''', re.MULTILINE)
CFF_VERSION_RE = re.compile(r'''^\s*version:\s*["']?([^"'\s#]+)["']?\s*''', re.MULTILINE)
META_VERSION_RE = re.compile(r'{%\s*set version\s*=\s*"([^"]+)"\s*%}')
PEP440ISH_RE = re.compile(r"\d+\.\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?")


def _source_version() -> str:
    """The version declared by the single source of truth."""
    matches = VERSION_LITERAL_RE.findall(INIT_FILE.read_text(encoding="utf-8"))
    assert matches, "no `__version__ = \"...\"` literal found in " + str(INIT_FILE)
    assert len(matches) == 1, (
        "src/prokname/__init__.py now has more than one line matching "
        f"`__version__ = ...` ({len(matches)}). The build backend takes the "
        "first match, so a second one silently forks the single source of truth."
    )
    return matches[0]


# ---------------------------------------------------------------------------
# The single source itself
# ---------------------------------------------------------------------------


def test_source_literal_is_a_well_formed_version():
    version = _source_version()
    assert PEP440ISH_RE.fullmatch(version), (
        f"src/prokname/__init__.py declares {version!r}, which is not a PEP 625 "
        "normalisable version; hatchling and bioconda would both choke on it."
    )


def test_module_reports_the_same_literal_the_file_declares():
    """The runtime copy must be that literal, never a second hand-written one."""
    prokname = pytest.importorskip("prokname")
    assert prokname.SOURCE_VERSION == _source_version(), (
        "prokname.SOURCE_VERSION no longer matches the `__version__` literal "
        "in src/prokname/__init__.py — the single source has forked."
    )


def test_fallback_is_explicit_not_silent():
    """An unresolvable distribution says so instead of inventing a version."""
    prokname = pytest.importorskip("prokname")
    version, origin = prokname._resolve_version("prokname-does-not-exist-anywhere")
    assert origin.startswith("source-literal ("), origin
    assert version == prokname.SOURCE_VERSION == _source_version()
    # The module-level value is exactly what the resolver returns right now,
    # and it always states which branch produced it.
    assert prokname.__version__ == prokname._resolve_version()[0]
    assert prokname.__version_source__.startswith(
        ("source-literal (", "distribution-metadata")
    )


def test_installed_metadata_and_source_agree():
    """When metadata *is* available it must not contradict the source tree.

    Skipped — not failed — from a bare checkout: this repository's suite runs
    via ``PYTHONPATH=src`` with no install step, so ``importlib.metadata``
    raises ``PackageNotFoundError`` and ``__version__`` legitimately comes from
    the documented fallback (the single source of truth). The skip message
    names the branch that was taken so "skipped" is never mistaken for
    "verified".
    """
    prokname = pytest.importorskip("prokname")
    if prokname.__version_source__ != "distribution-metadata":
        pytest.skip(
            "no installed prokname distribution visible to this interpreter "
            f"(__version__ came from {prokname.__version_source__}). The "
            "source-literal path is the supported one for `PYTHONPATH=src` "
            "runs and is covered by test_fallback_is_explicit_not_silent; run "
            "this test in a `pip install .` environment to exercise the "
            "metadata path."
        )
    from importlib.metadata import version as dist_version

    assert dist_version("prokname") == _source_version(), (
        "the installed distribution's metadata disagrees with "
        "src/prokname/__init__.py — rebuild/reinstall, otherwise the export "
        "fingerprints of one of these two artifacts are wrong."
    )


# ---------------------------------------------------------------------------
# Derived declarations
# ---------------------------------------------------------------------------


def test_pyproject_derives_the_version_instead_of_repeating_it():
    tomllib = pytest.importorskip("tomllib")
    text = PYPROJECT_FILE.read_text(encoding="utf-8")
    data = tomllib.loads(text)

    assert "version" not in data["project"], (
        "pyproject.toml re-declares [project].version: make it dynamic "
        "again so a wheel's metadata cannot drift from prokname.__version__"
    )
    assert "version" in data["project"].get("dynamic", []), (
        '[project].dynamic must list "version", otherwise the hatchling source '
        "below is inert and the build fails"
    )
    hatch_version = data.get("tool", {}).get("hatch", {}).get("version", {})
    assert hatch_version.get("path") == "src/prokname/__init__.py", (
        f"[tool.hatch.version].path is {hatch_version.get('path')!r}; it must "
        "point at the single source of truth, else hatchling falls back to its "
        "own file-name heuristics (or fails)"
    )
    assert not re.search(r'^\s*version\s*=\s*["\']', text, re.MULTILINE), (
        "a hard-coded `version = \"...\"` assignment reappeared in pyproject.toml"
    )


# ---------------------------------------------------------------------------
# The two unavoidable mirrors
# ---------------------------------------------------------------------------


def test_citation_cff_mirrors_match_the_source():
    found = CFF_VERSION_RE.findall(CITATION_FILE.read_text(encoding="utf-8"))
    # The two expected hits: the top-level record and `preferred-citation`.
    assert len(found) == 2, (
        "expected exactly 2 `version:` fields in CITATION.cff (record + "
        f"preferred-citation), found {found!r} — a third mirror deserves an "
        "explicit decision, not a silent pass"
    )
    assert set(found) == {_source_version()}, (
        f"CITATION.cff declares {found!r} but the single source says "
        f"{_source_version()!r}; a citation and an export fingerprint would "
        "contradict each other"
    )


def test_bioconda_recipe_mirror_matches_the_source():
    found = META_VERSION_RE.findall(META_FILE.read_text(encoding="utf-8"))
    assert len(found) == 1, f"expected one Jinja `set version` in meta.yaml, got {found!r}"
    assert found[0] == _source_version(), (
        f"recipes/bioconda/meta.yaml pins {found[0]!r} but the single source "
        f"says {_source_version()!r}"
    )


def test_no_undocumented_version_mirror_appears():
    """A new hand-copied literal must be a deliberate, reviewed addition.

    Counting occurrences per packaging file (instead of grepping for *any*
    match) is what turns "we single-sourced this" into a claim a reviewer can
    check in one line: across these files the literal appears 4 times and
    exactly one of them is the source. It used to be 6 hand-written copies.
    """
    version = _source_version()
    expected = {
        INIT_FILE: 1,  # the single source
        CITATION_FILE: 2,  # plain YAML cannot read it
        META_FILE: 1,  # Jinja renders before the package exists
        PYPROJECT_FILE: 0,
        REQUIREMENTS_FILE: 0,
        REQUIREMENTS_DEV_FILE: 0,
        DOCKERFILE: 0,
    }
    total = 0
    for path, want in expected.items():
        if not path.exists():
            assert want == 0, f"{path.relative_to(REPO_ROOT)} vanished unexpectedly"
            continue
        got = path.read_text(encoding="utf-8").count(version)
        assert got == want, (
            f"{path.relative_to(REPO_ROOT)} repeats the version literal {got} "
            f"time(s), expected {want}. Either derive it from "
            "src/prokname/__init__.py or update this whitelist on purpose "
            "the single source of truth."
        )
        total += got
    assert total == 4
