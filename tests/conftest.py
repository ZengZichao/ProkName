"""Pytest configuration: fixture loading and shared test infrastructure.

Honest status of the "recorded live responses" story:

- `tests/fixtures/` carries source-derived fixtures for the LPSN client
  contract, the LPSN status enumeration, the GNA GNverifier response schema
  and a synthetic NCBI names.dmp. Each file states which vendored snapshot
  it was derived from, and `manifest.json` records `live_capture` per file.
- Exactly one fixture is now a genuine capture from a running service:
  `gna_verifications_live.json`, recorded by `scripts/record_live_gna.py`
  (the GNverifier service needs no credentials). It closes the M2 "confirm one
  live response still matches this schema" gate for GNA only.
- Live captures for LPSN (credentials required) and for the SeqCode REST
  contract remain outstanding M0/M2 deliverables, so no test may treat an
  authority-tier contract as live-verified. The `live_capture_available`
  fixture below lists whatever is genuinely captured, so no test can pretend
  to verify a live contract that was never recorded.

To (re-)record the credential-free capture and check for upstream drift:
    python scripts/record_live_gna.py          # write
    python scripts/record_live_gna.py --check  # diff, no writes
To record a VCR cassette once credentials exist:
    pytest tests/test_api_fixtures.py --vcr-record=new
"""

# Make src importable when running pytest without installation
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture loading (source-derived; see tests/fixtures/manifest.json)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def hermetic_home(tmp_path_factory):
    """Keep the whole suite out of the developer's real ~/.cache and ~/.config.

    This was not always true, and the consequences were all real. Tests that
    called `check_name()` without the per-test `isolated_cache` fixture wrote
    genuine query records into `~/.cache/prokname/`, and the NCBI sidecar writer
    left pickles named after long-deleted pytest tmp dirs
    (`ncbi_index/names_index._private_var_folders_…_pytest-1051_….pkl`) there by
    the dozen. Two symptoms: the suite polluted and was polluted by user state —
    a run could read a cached SeqCode answer left behind by an earlier run or by
    `prokname check` on the command line, so a test asserting what an adapter
    returned could pass or fail depending on history — and the cache directory
    grew on every test run, forever.

    Session-scoped and autouse because per-test fixtures can only redirect what
    a test knows to redirect; the point is that no test has to remember.
    `cache_dir()` resolves the environment on every call, so this takes effect
    without any import-time cooperation.
    """
    root = tmp_path_factory.mktemp("prokname-home")
    cache = root / "cache"
    config = root / "config"
    cache.mkdir()
    config.mkdir()
    previous = {k: os.environ.get(k) for k in
                ("PROKNAME_CACHE_DIR", "PROKNAME_TAXDUMP_INDEX_DIR",
                 "XDG_CONFIG_HOME", "PROKNAME_TAXDUMP_DIR")}
    os.environ["PROKNAME_CACHE_DIR"] = str(cache)
    os.environ["PROKNAME_TAXDUMP_INDEX_DIR"] = str(root / "taxdump-index")
    os.environ["XDG_CONFIG_HOME"] = str(config)
    os.environ.pop("PROKNAME_TAXDUMP_DIR", None)
    yield root
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def load_fixture():
    """Load tests/fixtures/<name> (JSON) — KeyError names the missing file."""
    cache: dict[str, dict] = {}

    def _load(name: str) -> dict:
        if name not in cache:
            path = FIXTURES_DIR / name
            if not path.exists():
                raise KeyError(
                    f"missing test fixture {path}; see tests/fixtures/manifest.json"
                )
            cache[name] = json.loads(path.read_text(encoding="utf-8"))
        return cache[name]

    return _load


@pytest.fixture
def fixture_manifest(load_fixture) -> dict:
    return load_fixture("manifest.json")


@pytest.fixture
def ncbi_taxdump_dir(tmp_path, fixtures_dir):
    """A taxdump-shaped directory carrying the synthetic names.dmp sample.

    The adapter looks for `names.dmp` (or `names.dmp.gz`) inside the dump
    directory, so the fixture file is materialised under that name.
    """
    import shutil

    target = tmp_path / "taxdump"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixtures_dir / "ncbi_names_tiny.dmp", target / "names.dmp")
    return target


# ---------------------------------------------------------------------------
# Cache isolation — every test that touches the dedup cache gets a tmp dir
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Point PROKNAME_CACHE_DIR at a throwaway directory."""
    root = tmp_path / "cache"
    monkeypatch.setenv("PROKNAME_CACHE_DIR", str(root))
    return root


@pytest.fixture
def no_lpsn_credentials(monkeypatch):
    """Deterministically remove LPSN credential env vars from the process."""
    monkeypatch.delenv("PROKNAME_LPSN_USER", raising=False)
    monkeypatch.delenv("PROKNAME_LPSN_PASSWORD", raising=False)
    monkeypatch.delenv("PROKNAME_TAXDUMP_DIR", raising=False)


# ---------------------------------------------------------------------------
# vcrpy integration
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vcr():
    """Return a VCR instance for recording/replaying HTTP interactions."""
    try:
        import vcr
        return vcr
    except ImportError:
        pytest.skip("vcrpy not installed; run pip install -e '.[dev]'")


@pytest.fixture
def vcr_cassette_dir():
    """Directory where VCR cassettes are stored."""
    return FIXTURES_DIR


@pytest.fixture
def live_capture_available():
    """Paths of the fixtures genuinely captured from a running service.

    Skips when none exists yet. As of 2026-09-25 the GNA GNverifier responses
    are live-captured (scripts/record_live_gna.py); the LPSN ones still need
    credentials and remain an M0/M2 deliverable.
    """
    recorded = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        provenance = payload.get("_provenance")
        if isinstance(provenance, dict) and provenance.get("live_capture"):
            recorded.append(path)
    if not recorded:
        pytest.skip(
            "no live_capture: true fixture recorded yet — run "
            "scripts/record_live_gna.py (GNA needs no credentials); LPSN "
            "captures still need PROKNAME_LPSN_USER/PASSWORD. "
            "See tests/fixtures/manifest.json"
        )
    return recorded


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project_dir(tmp_path):
    """A temporary directory for project storage tests."""
    return tmp_path / "projects"


@pytest.fixture
def sample_genus_gender():
    """A minimal genus_gender.json for testing."""
    return {
        "Escherichia": {"gender": "f", "source": "test"},
        "Bacillus": {"gender": "m", "source": "test"},
        "Rhizobium": {"gender": "n", "source": "test"},
    }


@pytest.fixture
def sample_a_set():
    """A minimal A-set for holdout testing (genera NOT in the lexicon)."""
    return [
        {"genus": "SyntheticumA", "label": "n", "in_lexicon": False},
        {"genus": "SyntheticumB", "label": "n", "in_lexicon": False},
    ]
