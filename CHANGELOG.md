# Changelog

[English](CHANGELOG.md) | [中文](CHANGELOG.zh.md)

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-29

First public release of **prokname**, a decision-support tool for naming prokaryotic
taxa under the ICNP and the SeqCode. Everything below describes what the tool does; the
limitations section is equally specific about what it does not do yet.

prokname is deterministic and rule-based: no large language model is in the pipeline,
every rule lives in a versioned JSON asset, and every output carries source
attribution. It provides decision support only — name validity is determined solely by
formal publication under the ICNP or the SeqCode, never by this tool.

The desktop front end is a **separate project**, [ProkName Studio][studio]
(`prokname-studio`), that depends on this package the way any other consumer does. The
dependency runs one way: the engine imports no GUI toolkit and ships no GUI extra, so
`pip install prokname` installs typer + rich and nothing else. What the two surfaces
share is the presentation policy described below.

[studio]: https://github.com/ZengZichao/ProkName-Studio

### Added — generation and validation

- **Three-way grammatical categories.** A specific epithet is classified as a true
  adjective, a genitive noun, or an appositive noun, and each category inflects by its
  own rule rather than being forced through one declension path.
- **Deterministic candidate generation** from an etymology input (`stem`, `type`,
  `rank`, optional `genus`, `person_gender`, `gender_override`, `genus_suffix`,
  `adjective_formation`), with Latin orthography rules applied to the result.
- **Dual-mode genus gender determination**: a curated 58-genus lexicon (23 feminine /
  20 masculine / 15 neuter) plus ending inference, whose output is marked `needs_review`
  instead of being presented as settled.
- **Gender-agreement validation** over those categories. A missing genus or epithet is
  rejected with a `ValueError` naming the argument, and higher-rank checks are pointed
  at generation rather than silently mis-analysed.
- **Person-genitive endings modelled as latinisation paradigms**, not as a
  person-gender × stem-letter grid: paradigm definitions plus an attested-surname
  lexicon, so `Burgdorfer → burgdorferi`, `Hensel → henselae`, `Gordon → gordonae` and
  `Boyd → boydii` all come out right. A surname covered only by a default rule returns
  a `needs_review` proposal; one whose paradigm cannot be inferred raises
  `GenitiveCellUnavailable`. The third-declension `-er` branch populates `-i`, so the
  documented `-i`/`-ii` variant tolerance is reachable rather than dead code.
- **Suprageneric names are built on the type genus' genitive stem**, from
  `data/type_genus_stems.json`: `Bacillus → Bacillaceae / Bacillales`,
  `Clostridium → Clostridiales / Clostridiaceae / Clostridia`,
  `Pseudomonas → Pseudomonadota`, `Streptomyces → Streptomycetaceae`. Names that are
  regularly formed but not attested (e.g. `Escherichiales`) come back with
  `compliant=None` and an explicit "not attested" warning, and conserved names such as
  class `Bacilli` are preserved rather than "corrected".
- **`rules.json` states the strength of every suprageneric termination**
  (`rank_suffix_policy`): `-aceae`/`-ales` as mandatory ICNP, `-ota`/`-ia` as SeqCode
  recommendations, `-idae`/`-ineae`/`-oideae`/`-eae`/`-inae` as adopted botanical-code
  conventions. The engine asserts no compliance from the adopted-convention ranks.
- **Latinisation covers more than the German digraphs**: ø→oe, å→aa, đ/ð→d, þ→th,
  ł→l, ı→i are transliterated rather than dropped (*Bjørn → bjoern*, *Århus → aarhus*).

### Added — dual-code routing

- **`prokname route`** returns the viable code paths with their trade-offs, orders them
  ICNP-preemption-first, and discloses the GTDB boundary instead of pretending the two
  codes cover the same ground.
- An explicit `icnp_occupied` finding survives the unknown-source early return as a
  conflict warning, and `pure_culture` combined with `candidatus` — a semantic
  contradiction — emits a warning presenting both readings rather than resolving it
  silently.

### Added — two-tier deduplication

- **Local near-match (parahomonym) scan** with three documented calibers,
  `--near-match-mode whole|stem|both` (default `whole`). `stem` strips one Latin
  inflectional ending per word, using a suffix inventory derived from the shipped rule
  assets, and flags stem matches at distance ≤ 1, collapsing suffix-only variants
  (`-ensis/-ense`, `colii/coli`, `boydii/boydiae`) to stem-identical pairs; `both`
  reports the union with the smallest distance per hit. The chosen caliber is recorded
  in the report payload. The scan builds a cached, length-banded index so the corpus is
  normalised once.
- **Authority adapters** for LPSN, the SeqCode Registry and GNA:
  - *LPSN* is driven through the official client's real contract — `search()` then
    `retrieve()` with `match_mode="exact"`, plus an identity check on every returned
    `full_name` before classification, because the service default is a substring
    search. Substring hits yield an honest `not_found` listing the closest records;
    authentication failure, a rejected query and a genuine zero-result query are
    distinguishable, and the client's stdout is captured so `--json` stays clean.
  - *SeqCode* rules offline from a dated occupancy snapshot built by
    `scripts/build_seqcode_snapshot.py`, so a name registered as validly published under
    SeqCode produces `conflict` with no credentials and no network. A **miss is not a
    ruling**: the list was measured to omit names whose own record says
    `status_name: "Valid (SeqCode)"`, and two identical crawls disagreed on ~4 % of
    their contents, so absence returns `found_unknown`, which blocks adjudication exactly
    like an unreachable authority.
  - *GNA* follows the current GNverifier REST contract (POST with a JSON body,
    `bestResult` / `matchedName` / `isSynonym` / `editDistance`), with a genuine live
    capture recorded as a replay fixture.
- **Status classification is an enum map, not a substring test.** `lpsn_status.json`
  maps the documented `lpsn_taxonomic_status` labels to categories token by token, so
  `Non-validly published name` cannot be read as validly published; the nomenclatural
  and taxonomic vocabularies are kept separate by construction, and a label attributed
  to both classifies as unknown rather than being guessed.
- **`unavailable` is never `not found`.** The verdict set is `conflict`, `blocked`,
  `verify_warning`, `no_clear_conflict`, `parahomonym_warning`, and an adapter that
  could not be consulted blocks adjudication rather than clearing a name. With the
  SeqCode snapshot offline and LPSN gated on credentials, a real `check` today exits 3
  or 4; local near-match hits are surfaced as warnings under `blocked`, marked "warning
  only, not a ruling", and the report discloses corpus size and truncation so that an
  empty hit list is never read as evidence of absence.
- **Stable exit codes**: `0` ok · `1` error · `2` usage/argument error raised by the
  CLI framework · `3` blocked · `4` conflict. An argument typo can no longer be
  mistaken for an occupied name.
- **Dedup cache**: per (name, source, date) with a 7-day TTL and checkpoint/resume for
  batch runs; cache hits are reported as such, and an adapter crash degrades to an
  honest `unavailable` instead of failing the check.

### Added — project storage and exports

- `prokname project` create / add / show / rate / export / delete, with `data_source`
  and `target_code` metadata and in-store candidate ratings.
- Project files carry `schema_version`: a file with no marker is version 0 and still
  loads; a file from a newer build is refused with a reason rather than half-read.
  Malformed JSON (missing or mistyped `name`, non-list `candidates`) raises
  `ProjectLoadError` instead of a raw `KeyError`/`TypeError`.
- JSON writes go through a shared atomic-write helper, and cache and project filenames
  carry a short hash of the raw name, so `Bacillus subtilis` and `Bacillus_subtilis`
  — or `my paper` and `my_project` — no longer collide.
- Exports (JSON / CSV / Markdown) always carry the compliance disclaimer and the
  LPSN / SeqCode attribution; the stored check verdict travels with the candidate;
  `--format` is validated instead of silently emitting JSON under another name; and the
  Markdown export appends a draft etymology table per candidate in the SeqCode
  Registry's required format, with morpheme rows deliberately left blank because
  prokname does not invent etymology it was not given.

### Added — benchmarks and reproducibility

- **Seed benchmark sets A / B1 / B2 / C / D**, every case carrying the full seven-field
  provenance (`source`, `lpsn_id`, label or `grammatical_category`, `annotator`,
  `annotated_at`, `license`, `note`), plus majority-class and naive-ending baselines.
- **Statistical reporting that names its own method**: Clopper–Pearson exact intervals
  for frozen label sets, case-level bootstrap percentiles elsewhere, refusal-aware
  accuracy (an honest `needs_review` refusal on a truly unknown genus counts as
  correct), decidable-class (m/f/n) macro-F1, paired-difference bootstrap and exact
  McNemar for comparisons, and a confusion matrix that keeps every observed label.
  Interval labels are printed verbatim so a reader cannot attach the wrong interval to
  the wrong metric.
- **`prokname bench --full` is a CI gate** on holdout integrity, the B1 false-negative
  target and D-set ICNP-preemption correctness. A **violated** gate always fails; a
  gate whose target the shipped seed **cannot test** is reported as evidence debt and
  fails only under `--require-complete`, the milestone mode. Nothing is certified by
  silence: the line prints as `b1_fnr=pass(NOT CERTIFIED)` and the JSON says so too.
- **Holdout integrity check** (`prokname holdout`) normalises genus names exactly as the
  engine does, so a diacritic-spelled genus cannot slip past the disjointness gate; the
  invariant is the A-inference subset against the published lexicon, and the lexicon
  genera in the A lookup subset overlap by design.
- **GAN comparison (C-set)** runs behind GPL-3.0 isolation with a fixed seed, ≥ 3
  repetitions per stem, and an explicit command spec: the adapter refuses to guess a
  competing tool's flags, so no GAN number exists until a spec is supplied, and the
  report states which of those states it is in. Both sides are judged by the same
  neutral orthographic referee — prokname is not scored by its own `compliant` verdict
  — and that referee's limitation is documented rather than hidden.
- **Example verification** (`scripts/verify_examples.py`) re-runs every documented
  example against the engine, matches conserved names against a structured list rather
  than by substring, and labels each unverified cell with the rule it is waiting on.
- Corpus rebuild (`scripts/rebuild_corpus.py`) for the LPSN / SeqCode / NCBI taxdump
  three-source scaffold, with a rate limiter, cache, checkpoints and atomic exports; a
  failed or partial run exits non-zero and writes nothing.

### Added — interface and configuration

- **Nine commands** (`gen`, `check`, `route`, `project`, `config`, `bench`, `data`,
  `holdout`, with `--version` at the group level), each with dual-mode output:
  human-readable and `--json`, honouring the rule that every command accepts `--json`.
- **Persistent configuration** in `~/.config/prokname/config.json` for taxdump and
  snapshot locations, with environment variables still taking precedence and no
  credential ever stored in that file; LPSN credentials live in the OS keyring or the
  environment.
- **Diagnostics**: `--debug` and `PROKNAME_DEBUG=1` echo the third-party client output
  that `check --online` normally swallows, so an operator can tell "answered: not found"
  from "the request never succeeded". No verdict and no exit code changes.
- **`prokname data` reports the state of every rule asset** — version, gating status,
  which module consumes it (`stems.json` is labelled NOT CONNECTED,
  `rate_limit_budget.json` PARTIAL), and how many assertions still await expert
  verification. Shipping an asset therefore no longer implies it has an effect.
- **Read-only packaged data assets**: mutating one raises `TypeError` at the offending
  line instead of rewriting rules that every other module reads.
  `prokname.reload_data()` re-reads an asset and drops every derived cache with it, so
  the expert-review workflow no longer needs a restart.
- **`prokname.presentation`** — a Qt-free shared presentation layer holding the semantic
  colour tokens and the verdict / role mapping, so the terminal and the desktop front
  end render the same meaning and one verdict cannot look urgent in one window and calm
  in the other. An unknown role fails safe to a muted token instead of raising.
- Performance tests pin the non-functional targets: generation under 100 ms, routing
  under 10 ms, a 50 000-name near-match scan under 3 s.

### Added — packaging and documentation

- `pyproject.toml` (hatchling) with the version declared in exactly one place —
  `prokname.__version__` — and read dynamically by the build backend, so a wheel,
  `prokname --version` and an export fingerprint cannot disagree.
- `Dockerfile` (multi-stage, non-root) with a matching `.dockerignore`;
  `recipes/bioconda/meta.yaml` (noarch Python); `requirements*.txt` documented as
  hand-maintained mirrors of `pyproject.toml` and explicitly **not** lockfiles.
- `LICENSE` (MIT), `DATA_LICENSE` (data terms), `CITATION.cff` with author and ORCID.
- **Bilingual documentation, English primary**: `README`, `USAGE`, `CONTRIBUTING`,
  `CHANGELOG` and `DATA_LICENSE` each ship as an English original plus a Chinese
  companion, with a language switch line listing English first. `USAGE` covers every
  command with examples, exit codes, the Python API, benchmark reproduction and
  troubleshooting. Tests enforce the pairing, so a document cannot drift into one
  language.
- **`docs/provenance/`** records every external fact the code relies on with its
  repository, commit SHA, file:line, retrieval date and a re-check command, in both
  languages. Code comments cite that register instead of paths on the author's disk, and
  the register states with a measured 404 that branch names are not pins.
- Each licence obligation is stated once, in `prokname.LPSN_ATTRIBUTION`,
  `prokname.SEQCODE_ATTRIBUTION` and `source_attribution()`. A test fails if the wording
  reappears in another module, because an auditor finding the same obligation worded two
  ways in two exports is the failure mode this tool has to design out.

### Added — quality gates

- Test matrix across ubuntu / windows / macos and Python 3.11–3.14; ruff lint as its own
  job; a coverage floor; a wheel build plus a clean-venv install smoke test; the holdout
  integrity check blocking.
- Routine CI performs **no** network calls: the online adapters are covered by
  contract-pinned offline stubs. A weekly scheduled job re-verifies the citation register
  and probes the SeqCode endpoint contract, so upstream drift is detected without letting
  an upstream outage redden an unrelated pull request.
- Guards that keep the architecture honest: the import-order contract (every module
  importable as a fresh interpreter's first action, package cycles kept inside their
  package), the presentation-layer boundary, the single-source licence wording, the
  provenance-path rule, the bilingual document pairing, and a generated test count
  verified by CI — measured from `pytest --collect-only`, never typed by hand.
- The suite is hermetic: it neither reads nor writes the developer's cache or the working
  directory, and the opt-in `taxonkit` cross-validation of the built-in NCBI taxdump
  parser is collected and skipped rather than silently absent, because the shipped images
  install neither the binary nor a cassette store.

### Data and licensing

- Code: MIT. Data: **CC BY-SA 4.0** for LPSN-derived parts, **CC0 1.0** for the
  hand-built rule files, **CC BY 4.0** for the SeqCode Registry snapshot — the last
  quoted from the Registry's own page and recorded with its retrieval date.
  `DATA_LICENSE` is the authoritative text and lists what it does *not* settle.

### Known limitations

- **Expert sign-off on the rule assets is pending.** `rules.json` and
  `person_genitive.json` ship with cells flagged `verified: false`, including the
  suprageneric rank attributions and their rule citations. Nothing in the tool asserts
  validity on their behalf.
- **Person-genitive paradigms are incomplete.** The `-iae`, `-is` and `-ae`-by-default
  paradigms are proposals awaiting adjudication against the ICNP orthography appendix,
  and the vowel-stem cells are not fully populated.
- **`check` cannot yet rule in the field.** SeqCode publishes no lookup-by-name, so the
  offline snapshot answers occupancy positively but never absence; LPSN needs
  credentials. Until then `check` returns `conflict` or `blocked`, and the three clean
  verdicts stay gated. Recorded live cassettes for every endpoint actually used are
  outstanding.
- **Benchmark sets are seed-level** (135 annotated cases across A / B1 / B2 / C / D,
  against a target of ≥ 1 920 LPSN-derived, holdout-controlled cases). The B1
  false-negative target is not testable at that size, which the report states as
  evidence debt rather than a pass.
- **Two licence questions stay open**: the cell-level derivation audit of
  mixed-provenance rule assets before any of them is mirrored into a public dataset or
  release archive, and whether the European sui generis database right is engaged by
  redistributing the full SeqCode name list.
- **Registration entries still pending**: a Zenodo DOI and a bio.tools record.
- The cited references' DOIs (Freese 2026, Ratatoskr, Trüper & de'Clari) are still to be
  verified.
