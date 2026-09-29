"""prokname — prokaryotic nomenclature assistant (ICNP / SeqCode).

Implements the deterministic core of the engine:
three-way grammatical-category engine, dual-mode genus-gender determination,
dual-code routing, and a two-tier (exact-authority + local near-match) dedup
orchestration scaffold.

Version handling
----------------------------------
The ``__version__`` literal below is the **single source of truth** for the
version string. The tool stamps it into every export fingerprint
(``storage/store.py``), the benchmark report and the Studio About box, so a
hand-copied second declaration could make one delivery contradict another.

How the other declarations obtain it:

* ``pyproject.toml`` — ``dynamic = ["version"]`` plus
  ``[tool.hatch.version] path = "src/prokname/__init__.py"``, so wheel/sdist
  metadata is *generated* from this line instead of repeating it;
* ProkName Studio (a separate project that depends on this one) — it reads
  ``prokname.__version__`` at runtime for its About box, so it can never stamp a
  version this package did not declare;
* ``recipes/bioconda/meta.yaml`` and ``CITATION.cff`` — plain Jinja/YAML that
  is rendered before (or without) any Python of this package running, so they
  cannot read this file and still carry literals. They are guarded by
  ``tests/test_version_consistency.py``, which fails if a mirror drifts.

At runtime the installed distribution's own metadata
(``importlib.metadata.version("prokname")``) wins, because that is what the
artifact the user actually installed *is*. When no distribution metadata is
visible — the normal case for a source checkout used via ``PYTHONPATH=src``,
which is how this repository's own tests run — ``_resolve_version()`` returns
:data:`SOURCE_VERSION` explicitly rather than silently. ``__version_source__``
records which of the two branches produced ``__version__``, so tests and bug
reports can tell an authoritative metadata version from the source literal,
and a metadata/source mismatch is surfaced as a warning instead of a silent
rewrite.
"""

from __future__ import annotations

# Single source of truth for the package version. Bump this one line:
# hatchling ([tool.hatch.version]) reads exactly this assignment, and
# tests/test_version_consistency.py checks the mirrors that cannot. Keep it a
# plain string assignment — the resolver below rebinds `__version__` through
# tuple unpacking, which their regex does not match.
__version__ = "0.1.0"

#: Explicit copy of what *this source tree* declares. Never rebound.
SOURCE_VERSION: str = __version__

#: How ``__version__`` was obtained: ``"distribution-metadata"`` or
#: ``"source-literal (<reason>)"``. Set once at import by `_resolve_version()`.
__version_source__: str = "source-literal (not yet resolved)"


def _resolve_version(distribution: str = "prokname") -> tuple[str, str]:
    """Return ``(version, origin)`` for this import of :mod:`prokname`.

    ``origin`` is ``"distribution-metadata"`` when the installed distribution
    answered, else ``"source-literal (<reason>)"`` — the documented fallback
    that keeps a bare checkout (``PYTHONPATH=src``, no install step) usable.
    The fallback never guesses: it returns :data:`SOURCE_VERSION`, i.e. the
    literal this very file declares and the build backend reads.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - interpreter has no importlib.metadata
        return SOURCE_VERSION, "source-literal (importlib.metadata unavailable)"
    try:
        return version(distribution), "distribution-metadata"
    except PackageNotFoundError:
        return SOURCE_VERSION, "source-literal (prokname is not installed)"
    except Exception as exc:  # pragma: no cover - corrupt/foreign metadata
        return SOURCE_VERSION, f"source-literal ({type(exc).__name__}: {exc})"


__version__, __version_source__ = _resolve_version()

if __version_source__ == "distribution-metadata" and __version__ != SOURCE_VERSION:
    # An installed distribution and this source tree disagree. Report the
    # installed one (it is what the user runs) but say so loudly: this is
    # exactly the silent-divergence failure mode this guard is about.
    import warnings

    warnings.warn(
        f"prokname: the source at {__file__} declares version "
        f"{SOURCE_VERSION!r} but the installed distribution reports "
        f"{__version__!r}, which is the version being reported. The mismatch "
        "usually means an editable install points at a different tree or a "
        "release bumped only one of the two.",
        RuntimeWarning,
        stacklevel=2,
    )

def reload_data() -> int:
    """Re-read every versioned rule asset and drop all derived caches.

    Returns how many derived caches were dropped. This is the M0 workflow
    entry point: an expert reviews a candidate ``rules.json`` /
    ``lpsn_status.json`` / ``seqcode_registered.json``, it is replaced on disk,
    and one call takes effect — no process restart, and no half-reloaded state
    where the raw table is new but an index built from it is old.

    Lazy by design: importing ``prokname`` must not drag in the engine, so the
    hooks are whatever the caller has already loaded. Assets are always
    re-read; derived caches register themselves when their module is imported.
    """
    from .engine import data as _data

    return _data.invalidate()


def data_status() -> dict:
    """Per-asset version, gating status and whether any code consumes it."""
    from .engine import data as _data

    return _data.asset_status()


DISCLAIMER = (
    "prokname provides decision support only; name validity is determined "
    "solely by formal publication under the ICNP or the SeqCode."
)

#: The two licence statements every report, export and deposit must carry.
#:
#: They used to be typed out in four places with three different wordings — the
#: JSON `attribution` block, the Markdown/CSV footer, and the deposit README —
#: and a nomenclature tool's attribution line is a compliance statement, not
#: decoration: an auditor reading two exports of the same project should never
#: find two obligations. Spelling of the SeqCode identifier follows the
#: historical "CC-BY 4.0" (the Registry's own page writes "CC BY"); the token is
#: asserted verbatim by the export tests, so it is recorded here rather than
#: silently normalised. See DATA_LICENSE.
LPSN_ATTRIBUTION = (
    "CC BY-SA 4.0 — cite the current LPSN reference and the access date; "
    "link back to lpsn.dsmz.de"
)
SEQCODE_ATTRIBUTION = "CC-BY 4.0 — attribute seqco.de"


def source_attribution() -> dict[str, str]:
    """A fresh attribution mapping, safe to hand to a report or serialise."""
    return {"LPSN": LPSN_ATTRIBUTION, "SeqCode Registry": SEQCODE_ATTRIBUTION}
