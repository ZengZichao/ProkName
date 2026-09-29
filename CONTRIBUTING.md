# Contributing to prokname

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING.zh.md)

Thank you for your interest in improving prokname! This document covers the
essential rules for contributors.

## Core principle

prokname is a **deterministic, auditable** nomenclature tool. Every rule is
encoded in versioned JSON data assets, and every output carries source
attribution. The following rules are non-negotiable.

## GAN code isolation (GPL-3.0 containment)

The tool `gan-nomenclature` (telatin/gan) is licensed under GPL-3.0, while
prokname is MIT-licensed. To prevent license contamination:

- **NEVER** copy any source code from the GAN repository into this codebase.
- The C-set benchmark comparison uses GAN **only via its CLI** (subprocess),
  never as an imported library.
- If you need to reference GAN's behaviour, cite its output, not its code.

## Data rules

- **`DATA_LICENSE` is the single source of truth** for data licensing, and it
  grants three terms: **CC BY-SA 4.0** for LPSN-derived parts, **CC0 1.0** for
  the hand-built rule files (`rules.json`, `gender_endings.json`,
  `person_genitive.json`, `stems.json`), and **CC BY 4.0** for the SeqCode
  Registry snapshot (`seqcode_registered.json`).
- **LPSN data** (CC BY-SA 4.0): only obtain via the official API or downloads
  channel. **Scraping is strictly forbidden.** Derivative data (e.g.,
  `genus_gender.json`) must carry the ShareAlike obligation and attribution.
  ShareAlike means a *rule table derived from LPSN text* would have to be
  re-published under CC BY-SA 4.0, not CC0 — check the derivation of every
  asset you touch before changing its licence.
- **SeqCode data**: read `DATA_LICENSE` § *"SeqCode Registry data — resolved
  2026-09-25"*. The Registry's own page releases contributed information under
  **CC BY 4.0**, so `seqcode_registered.json` is redistributed under that
  licence. The obligation is attribution: keep the `_meta.licence` /
  `_meta.attribution` fields, and cite the SeqCode Initiative plus the
  per-name `https://seqco.de/i:<id>` URIs when republishing. CC BY carries no
  ShareAlike — but where one file mixes LPSN-derived and SeqCode-derived
  content, the stricter BY-SA obligation governs the whole file. What
  `DATA_LICENSE` still records as unresolved is not the licence but the
  European sui generis database right, on which the Registry has never
  published a statement.
- **NCBI taxdump**: follows NCBI usage terms; the taxdump is redistributable.
- **Never** commit API credentials, passwords, or tokens to the repository.
  Use OS keyring or environment variables (see `prokname config`).
- Dependency ranges live in `pyproject.toml` (authoritative).
  `requirements.txt` / `requirements-dev.txt` are hand-maintained convenience
  mirrors that also list some transitive dependencies of `typer`/`rich`; they
  are **not** lockfiles and drift if not updated together with
  `pyproject.toml`.

## Rule changes

All rule tables (`rules.json`, `person_genitive.json`, `gender_endings.json`,
`genus_gender.json`) require **domain expert sign-off** before they can be
relied upon (M0 gate). Do not fill in unverified cells from memory — raise
`GenitiveCellUnavailable` or equivalent instead, exactly as the existing code
does for the vowel-stem person-genitive cells.

## Testing

- Every code change must pass `prokname bench` (the real-name anchored
  regression seed).
- The holdout integrity check (`prokname holdout`) must pass: the published
  lexicon must be disjoint from the **A-inference** subset. The rule is
  deliberately narrower than "lexicon ∩ A-set = ∅" — the 15 lexicon genera
  that appear in the A **lookup** subset overlap the lexicon by design, and
  are not leakage. Do not "fix" that overlap.
- Property tests (hypothesis) cover orthographic invariants — do not weaken
  them.
- **Routine CI performs no network calls**, and it does **not** replay
  recorded HTTP cassettes today: the online adapters are exercised offline
  against stubbed adapter clients that pin the upstream contracts field by
  field (`tests/test_online_adapters.py`, plus the offline/degradation cases
  in `tests/test_api_fixtures.py`; `tests/conftest.py` records honestly that
  no cassettes have been recorded). Cassette-based replay becomes available
  only once a fixture has actually been recorded — record against live LPSN
  credentials under `tests/fixtures/` (requires M0/M2 credentials), and until
  then never describe CI as "replaying recorded responses". Tests must never
  hit the network: mock or replay instead.
- The test count is generated, not typed: `python scripts/fill_test_counts.py`
  writes what `pytest --collect-only` collects (the suite collects
  967 tests), and CI's `--verify` step fails when it goes
  stale. There is one number now — nothing in this package imports
  Qt, so no module is gated on a GUI extra. ProkName Studio's suite is counted
  with Studio. The 3 `taxonkit` cross-check tests are collected but **skipped**
  unless the `taxonkit` binary is on `PATH`; the CI jobs that run pytest install
  neither (`.github/workflows/ci.yml` has no `taxonkit` step), so that
  cross-validation gate never runs in this project's CI.
- Lint must pass: `ruff check src scripts tests` (CI-enforced).

## CI gates (blocking)

1. `ruff check src scripts tests` passes.
2. All unit + property tests pass on Linux, Windows, macOS (Python 3.11+).
   The matrix installs `.[dev]`: the engine has no GUI extra to skip.
3. `prokname bench` (regression seed) passes.
4. `prokname bench --full` (gated benchmark) exits 0.
5. `prokname holdout` (holdout integrity) passes.
6. `python scripts/verify_examples.py` (M0 example verification) passes.
7. Wheel build + clean-venv install smoke passes (package data included).
8. Benchmark degradation >2pp relative to baseline blocks the merge.

The weekly `live-smoke` job is scheduled separately and is currently wrapped
in `continue-on-error` + `|| true`, i.e. it cannot fail CI; treat its output as
a drift report, not as a gate.
