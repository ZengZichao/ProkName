# prokname Usage Guide

[English](USAGE.md) | [中文](USAGE.zh.md)

This guide covers every prokname command in detail: installation, daily use,
machine-readable output, configuration, the Python API, benchmarking, and
troubleshooting. For a short overview see the [README](README.md).

> **Disclaimer.** prokname provides decision support only. Name validity is
> determined solely by formal publication under the ICNP or the SeqCode —
> never by this tool.

---

## Table of contents

1. [Installation](#1-installation)
2. [Concepts](#2-concepts)
3. [Command reference](#3-command-reference)
   - [`prokname gen`](#prokname-gen)
   - [`prokname check`](#prokname-check)
   - [`prokname route`](#prokname-route)
   - [`prokname project`](#prokname-project)
   - [`prokname bench` / `prokname holdout`](#prokname-bench--prokname-holdout)
   - [`prokname data`](#prokname-data)
   - [`prokname config`](#prokname-config)
   - [ProkName Studio](#prokname-studio)
4. [Exit codes and output modes](#4-exit-codes-and-output-modes)
5. [Python API](#5-python-api)
6. [Benchmarks](#6-benchmarks)
7. [Data assets and licensing](#7-data-assets-and-licensing)
8. [Environment variables and configuration files](#8-environment-variables-and-configuration-files)
9. [Troubleshooting and FAQ](#9-troubleshooting-and-faq)
10. [Known limitations](#10-known-limitations)

---

## 1. Installation

Requirements: Python ≥ 3.11 (3.11–3.14 tested), pip.

```bash
cd prokname
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # runtime + test toolchain
pip install -e ".[online]"       # optional: `prokname check --online` clients
```

Verify the installation:

```bash
prokname --version               # → prokname 0.1.0
prokname bench                   # must exit 0 (prints its own "passed n/n" line)
pytest                           # everything this environment can collect
pytest --collect-only            # total: its trailing "N tests collected" line
pytest --collect-only -q         # the authoritative per-module test counts
```

**The count below is generated, not typed.** `python scripts/fill_test_counts.py`
writes what `pytest --collect-only` actually collects, and CI's
`--verify` step fails when the written figure stops matching. What the suite
covers:

- it collects 967 tests with `.[dev]` installed, and there
  is no second tier any more: nothing in this package imports Qt, so nothing is
  gated on a GUI extra. ProkName Studio's own suite is counted with Studio;
- `tests/test_property.py` gates on `hypothesis` and
  `tests/test_taxonkit_crosscheck.py` (3 tests) is collected everywhere but
  *skipped* unless the `taxonkit` binary is on `PATH` — the shipped CI images
  do not install it, so that cross-check never runs in this project's CI.

Use `pytest --collect-only` (total) and `pytest --collect-only -q` (per-module
counts) as the two authoritative commands; every total quoted in these docs is
a snapshot, and the suite is still growing.

> If `prokname: bad interpreter` appears after moving the project directory,
> the editable install still points at the old path. Re-run
> `pip install -e ".[dev]"` inside the virtual environment to repair the
> entry point (see [Troubleshooting](#9-troubleshooting-and-faq)).

Docker:

```bash
docker build -t prokname .
docker run --rm prokname gen --stem Boyd --type person --rank species \
    --genus Shigella --person-gender male
```

---

## 2. Concepts

prokname turns an **etymology description** into compliant candidate names,
checks them for conflicts, and helps choose the correct **nomenclatural code**.
Four etymology types drive the engine; each maps to one or two *grammatical
categories* of the specific epithet (the mapping lives in
`src/prokname/data/rules.json` and is expert-reviewable):

| Etymology type | `--type` | Grammatical category | Declines with genus gender? | Example |
|---|---|---|---|---|
| place name | `place` | adjective (place formation) | **yes** | *Klebsiella beijingensis* / *Rhizobium beijingense* |
| person name | `person` | genitive noun | **no** (honoured-person gender × stem ending) | *Shigella boydii* (not \**boydiae*) |
| thing name | `thing` | genitive noun | **no** (source-noun declension) | *Vibrio cholerae* |
| feature | `feature` | adjective **or** appositive (both emitted) | adjective: yes; appositive: no | *Thermus thermophilus* |

**Gender determination is dual-mode** and never guesses silently:

- **lookup** — the genus is in `genus_gender.json`; high confidence;
- **inference** — ending/morpheme heuristics; **always** flagged
  `needs_review` in the output;
- **unknown** — nothing applicable: the engine refuses to guess and returns
  a blocked candidate instead of defaulting to masculine.

**Person epithets are genitive nouns.** In the ICNP the ending is decided by
the *latinisation paradigm* the honoured surname is forced into (1st-declension
`-a` → `-ae`, 2nd-declension `-ius/-us` → `-ii`, 3rd-declension `-er` → `-i`),
not by the person's biological sex and not by the last letter of the stem
alone. `person_genitive.json` therefore models paradigms, an **attested
surname lexicon** (`boyd → boydii`, `burgdorfer → burgdorferi`,
`hensel → henselae`, `gordon → gordonae`) and a set of *default* rules.
Compliance is asserted only for attested surnames whose paradigm is
`verified: true`; a default-rule proposal is emitted but always comes back
`needs_review` / `compliant=None`, and a surname whose paradigm cannot be
inferred at all (typically vowel-final and unattested) raises
`GenitiveCellUnavailable` rather than being guessed. Filling the remaining
`verified: false` paradigms from the ICNP orthography appendix and LPSN is an
open M0 expert item — see [Known limitations](#10-known-limitations).

**Higher ranks are built on the genitive stem of the type genus.** The
code-mandated suffixes (`family → -aceae`, `order → -ales`, `class → -ia`,
`phylum → -ota`, …, including subranks) attach to the *genitive singular*
stem, so the nominative ending is removed or rewritten first: *Bacillus*
(gen. *Bacilli*) → **Bacill** + `-aceae` = `Bacillaceae`; *Clostridium* →
*Clostridi-* + `-ales` = `Clostridiales`; *Pseudomonas* → *Pseudomonad-* +
`-ota` = `Pseudomonadota`; *Streptomyces* (gen. *Streptomycetis*) →
*Streptomycet-* + `-aceae` = `Streptomycetaceae`. Passing the genus name in the
nominative to `--stem` is therefore the intended usage at these ranks. The
derivation reads the declension stems from `data/type_genus_stems.json`, and
compliance is asserted **only** when the derived name is also attested there
(`attested_higher_rank_names`): an unattested but regularly formed name such
as `Escherichiales` is returned with `compliant=None` and an explicit warning
instead of being presented as a published name.

---

## 3. Command reference

`src/prokname/cli.py` registers **eight** commands: `gen`, `check`, `route`,
`project`, `bench`, `holdout`, `data` and `config`. All eight are the core
headless CLI: nothing here imports a GUI toolkit. The desktop front end, **ProkName
Studio**, is a separate project that depends on this package — see
[ProkName Studio](#prokname-studio).

Every command accepts `--json` for machine-readable output
(all examples below also work with `--json`). Exit codes are documented in
[section 4](#4-exit-codes-and-output-modes).

### `prokname gen`

Generate candidate names from an etymology stem.

```bash
# person etymology → genitive noun, independent of the genus gender
prokname gen --stem Boyd --type person --rank species \
    --genus Shigella --person-gender male
# → Shigella boydii   (genitive noun: genus gender is irrelevant)

# place adjectives decline with the genus gender
prokname gen --stem Beijing --type place --rank species --genus Rhizobium
# → Rhizobium beijingense        (neuter → -ense)
prokname gen --stem Beijing --type place --rank species --genus Klebsiella
# → Klebsiella beijingensis      (feminine → -ensis)

# feature etymologies emit both the adjective and the appositive form
prokname gen --stem Wukong --type feature --rank species --genus Bacillus
# → Bacillus wukongus (adjective) and Bacillus wukong (appositive)

# higher ranks attach the code-mandated suffix to the GENITIVE STEM of the
# type genus (pass the nominative genus name as --stem; see §2)
prokname gen --stem Bacillus     --type feature --rank family    # Bacillaceae
prokname gen --stem Bacillus     --type feature --rank order     # Bacillales
prokname gen --stem Clostridium  --type feature --rank order     # Clostridiales
prokname gen --stem Clostridium  --type feature --rank class     # Clostridia
prokname gen --stem Streptomyces --type feature --rank family    # Streptomycetaceae
prokname gen --stem Pseudomonas  --type feature --rank phylum    # Pseudomonadota

# genus names have no mandatory suffix; suggestions are optional
prokname gen --stem Wukong --type feature --rank genus --genus-suffix monas
# → Wukongomonas (connecting vowel -o- inserted at the consonant cluster)
```

> Two caveats on the higher-rank block above. (1) A **conserved** name is not
> "fixed" by the rule: `--stem Bacillus --rank class` returns `Bacilli` with a
> `conserved name` warning, not the regular formation `Bacillia`. (2) Which
> code mandates which suffix is *not* uniform across the table: `rules.json`
> flags `-aceae`/`-ales` as mandatory ICNP terminations but `-idae`,
> `-ineae`, `-oideae`, `-eae`, `-inae` as adopted botanical-code conventions
> (subrank output comes back `compliant=None`), and `phylum → -ota` /
> `class → -ia` as SeqCode recommendations. Those attributions still need
> expert adjudication — see [Known limitations](#10-known-limitations).

Options:

| Option | Values | Notes |
|---|---|---|
| `--stem` | text (any script; transliterated) | required; latinized before use (German ü→ue/ä→ae/ö→oe/ß→ss, Nordic ø→oe/å→aa, æ→ae, þ→th, ł→l, …; hyphens removed; final table pending M0 expert sign-off) |
| `--type` | `place` \| `person` \| `thing` \| `feature` | default `feature` |
| `--rank` | `phylum` `class` `subclass` `order` `suborder` `family` `subfamily` `tribe` `subtribe` `genus` `species` `subspecies` | default `species` |
| `--genus` | genus name or full binomial | required for species/subspecies; for subspecies a binomial (`"Bacillus subtilis"`) is accepted |
| `--person-gender` | `male` \| `female` | gender of the **honoured person** (not the genus); required for `person` |
| `--gender` | `m` \| `f` \| `n` | override the genus gender after expert review |
| `--genus-suffix` | e.g. `monas` | optional suggestive ending for genus-rank names |
| `--adjective-formation` | `place` \| `second_declension` \| `third_declension` \| `loving` \| `nourishing` | select the adjective paradigm explicitly |
| `--json` | flag | machine-readable output |

Each candidate row reports: full name, grammatical category, genus gender,
compliance (`yes` / `no` / `review`), derivation, and warnings. A candidate
whose epithet cannot be formed safely (e.g. missing `--person-gender`, or
unknown genus gender) is emitted as `Genus [?]` with an explanation — never
silently dropped.

### `prokname check`

Two-tier deduplication: authority status + local near-match (parahomonym) scan.

```bash
# "Wukomonas" is an illustrative, synthetic genus (it is tagged
# `synthetic-demo` in corpus_seed.json) — it is not a published name.
prokname check "Wukomonas beijingensis"
prokname check "Wukomonas beijingensis" --json
prokname check "Escherichia colii" --max-distance 1   # tighten the scan
prokname check "Wukomonas beijingense" --near-match-mode stem   # stem-level caliber (v2)
prokname check "Some name" --no-near-match            # authorities only
prokname check "Some name" --online                   # query authority APIs (needs credentials, M0-gated)
```

Behaviour you can rely on:

- **Offline by default.** All four sources (LPSN, SeqCode Registry, GNA
  GNverifier, NCBI) report `unavailable` unless `--online` is given and
  credentials/configuration exist.
- **`unavailable` ≠ `not found`.** If an authority could not be consulted the
  verdict is `blocked` (exit 3): "could not check" never masquerades as
  "not found".
- **Reference sources only warn.** GNA/NCBI hits produce `verify_warning`,
  never a conflict ruling.
- The local near-match scan reports every corpus entry within
  `--max-distance` edits (default 2 — the classic `-ensis/-ense` confusable
  pair sits at distance 2), together with the corpus date and provenance; a
  corpus older than 180 days adds a staleness warning. The shipped corpus is a
  **dated demo corpus**: 16 names in `src/prokname/data/corpus_seed.json`
  (`corpus_date` 2026-08-16; source tags LPSN ×12, synthetic-demo ×2,
  SeqCode Registry ×1, NCBI ×1). Scan hits/misses on it are demonstrations,
  not measurements of recall or false-positive rate.
- **Two scan calibers.** `--near-match-mode whole` (default) compares full
  names; `stem` strips inflectional endings first (Taxamatch/GNmatcher-style)
  and flags stem matches at distance ≤ 1 — suffix-only variants such as
  `-ensis/-ense` collapse to stem-identical pairs; `both` reports the union,
  keeping the smallest distance per hit.
- **No network in routine CI.** The online adapters are exercised offline in
  `tests/`: stubbed adapter clients (see `tests/test_online_adapters.py`)
  pin the upstream request/response contracts field by field, and
  `tests/test_api_fixtures.py` covers the offline/degradation behaviour.
  Replaying *recorded* HTTP responses (VCR cassettes under `tests/fixtures/`)
  is **not** part of the routine CI run at present — `tests/conftest.py`
  records that no cassettes have been captured yet (recording needs live LPSN
  credentials). See [Known limitations](#10-known-limitations).

Five-state verdicts: `conflict`, `parahomonym_warning`, `verify_warning`,
`blocked`, `no_clear_conflict`. Until the SeqCode authority contract is
recorded (M0), only `conflict`, `blocked` and the near-match warnings are
reachable in practice — see the FAQ.

### `prokname route`

Route between the ICNP and the SeqCode; outputs viable paths with trade-offs,
never a forced single pick.

```bash
prokname route --source MAG --icnp-occupied no
# → SeqCode (only-viable): genome as type, Registry DOI, quality thresholds
prokname route --source pure_culture --icnp-occupied no
# → ICNP (default: IJSEM + type-strain deposition) AND SeqCode (alternative)
prokname route --source MAG --icnp-occupied yes
# → conflict guidance: SeqCode recognises ICNP priority
prokname route --source MAG --candidatus
# → SeqCode only-viable + Candidatus formatting rules
```

| Option | Values | Notes |
|---|---|---|
| `--source` | `pure_culture` \| `MAG` \| `SAG` \| `unknown` | required; `unknown` makes prokname ask you to specify first |
| `--candidatus` | flag | Candidatus formatting guidance |
| `--icnp-occupied` | `auto` \| `yes` \| `no` | `auto` (default) = not checked → paths marked provisional |

GTDB placeholder labels (e.g. JABL01-style) are explicitly out of scope: they
are not names under either code and are neither parsed nor mapped.

### `prokname project`

Candidate management with local persistence (FR-08). Projects are JSON files
under `~/.config/prokname/projects/` (override the directory with
`XDG_CONFIG_HOME`).

```bash
# create a named project with metadata
prokname project create my-paper --data-source MAG --target-code SeqCode

# generate candidates and add them to the project
prokname project add my-paper --stem Wukong --type feature --rank species --genus Wukomonas
prokname project add my-paper --stem Beijing --type place --rank species --genus Wukomonas

# inspect
prokname project list
prokname project show my-paper

# rate a candidate 0-5
prokname project rate my-paper --candidate "Wukomonas beijingensis" --score 5

# export (json | csv | markdown) — exports carry the compliance disclaimer
# and the LPSN/SeqCode attribution statements
prokname project export my-paper --format markdown
prokname project export my-paper --format csv --json     # wrapped, machine-readable

# delete
prokname project delete my-paper
```

Notes:

- `add` runs the same generation pipeline as `prokname gen` (pass
  `--person-gender` for person etymologies) and appends every generated
  candidate; invalid etymology arguments exit 1 with the engine's error.
- `rate` clamps scores to 0–5 and exits 1 if the project or candidate does
  not exist.
- `export --format` accepts only `json`, `csv` or `markdown`; anything else
  exits 1 instead of silently falling back to JSON.
- Project names may contain spaces; the on-disk filename adds a collision
  guard so `my paper` and `my_paper` remain distinct projects. A corrupt
  project file raises a clear error (`ProjectLoadError`) rather than looking
  like a missing project.
- Exports are annotated with: tool version, export timestamp, disclaimer,
  and the data attribution statements the code emits verbatim ("LPSN data:
  CC BY-SA 4.0", "SeqCode data: CC-BY 4.0"). Note that `DATA_LICENSE` — the
  authoritative licence text in this repository — currently grants only two
  terms: **CC BY-SA 4.0** for LPSN-derived parts and **CC0 1.0** for the
  hand-built rule files; the SeqCode-derived clause is an open question there
  (see [Data assets and licensing](#7-data-assets-and-licensing)).
  Markdown exports additionally carry a **draft SeqCode Registry etymology
  table** per candidate (full word + grammar pre-filled from the stored
  derivation; the morpheme rows are deliberately left for author completion —
  prokname does not invent etymology it was not given).

### `prokname bench` / `prokname holdout`

```bash
prokname bench            # built-in engine regression seed (25 real-name anchored cases)
prokname bench --full     # A/B1/B2/C/D benchmark suites with baselines and bootstrap CIs
prokname holdout          # CI gate: A-INFERENCE subset ∩ published lexicon must be empty
```

`prokname holdout` enforces exactly one thing (`src/prokname/benchmark/holdout.py`):
no genus of the **A-inference** subset may resolve through the lexicon. It
does **not** — and must not — require `genus_gender.json ∩ A-set = ∅`: the 15
lexicon genera that appear in the A **lookup** subset are in the lexicon *by
design* (they are what lookup coverage is measured on), so the full A-set
overlaps the lexicon legitimately. Do not "fix" the overlap by deleting
lexicon entries or lookup cases.

`bench --full` reports, per suite: lookup coverage, macro-F1/accuracy with
bootstrap 95 % CIs for the engine vs the majority-class and naive-ending
baselines (A), agreement accuracy and false-negative rate (B1, target ≤ 1 %),
generation exact-match and top-3 rates (B2), routing accuracy with the
ICNP-preemption sub-class (D, target 100 %), and the C-set scaffold — which
runs with `skip_gan=True`, i.e. only the prokname side is exercised; the
two-sided comparison against `gan` has not been run. It is also a **CI gate**:
if the holdout check fails, the B1 false-negative rate exceeds its target, or
the D-set preemption sub-class misses 100 %, the command exits 1.
Every result also carries a refusal-aware accuracy (with its own bootstrap
CI): an honest `needs_review` refusal on a truly unknown genus counts as
**correct**, because refusing to guess is the designed behaviour. The bootstrap
intervals are intervals of the per-case accuracy vector, not of macro-F1, and
they degenerate on all-hit samples — see
[Known limitations](#10-known-limitations).

### `prokname data`

```bash
prokname data          # version + gating status of every packaged data asset
prokname data --json
```

### `prokname config`

LPSN credential status and offline data configuration (FR-10).

```bash
prokname config                                        # show status
prokname config --taxdump-dir ~/data/taxdump           # persist NCBI taxdump location
prokname config --offline-snapshot ~/data/lpsn_export  # persist LPSN snapshot location (reserved: no adapter consumes it yet — M0)
prokname config --clear                                # drop persisted paths
prokname config --json
```

Persisted paths are stored in `~/.config/prokname/config.json` and survive
across invocations; environment variables override them. Passwords are never
written to the config file — LPSN credentials belong in the OS keyring
(service `prokname`) or the `PROKNAME_LPSN_USER` / `PROKNAME_LPSN_PASSWORD`
environment variables. Free LPSN API registration:
<https://api.lpsn.dsmz.de/>.

### ProkName Studio

The desktop front end is its own project: **ProkName Studio**, installed as
`prokname-studio`, bilingual (Chinese / English) with a light and a dark
appearance. It depends on this package the way any other consumer does and adds no
naming logic of its own — every result on screen comes from the API documented
above. Install and run it from its
[repository](https://github.com/ZengZichao/ProkName-Studio); that project's
`docs/STUDIO_PLAN.md` specifies its design.

What holds from this side:

- Studio writes candidate projects through the same `ProjectStore` as
  `prokname project`, so a project started in one surface opens in the other.
- Studio performs no network call. The offline boundary documented for `check`
  above is the same boundary the GUI reports, including which verdicts stay
  unreachable without LPSN credentials.
- Both surfaces colour a verdict through `prokname.presentation.decision`, so
  "blocked" cannot look urgent in one window and calm in the other.

---

## 4. Exit codes and output modes

### What each verdict means in operational terms

| Verdict | Exit | Says | Does NOT say |
|---|---|---|---|
| `conflict` | 4 | an authority holds this name as occupied | that publication was valid — that is a judgement for the Code, not the tool |
| `blocked` | 3 | an authority could not be consulted, or answered with something unclassifiable | "the name is free" |
| `verify_warning` | 0 | authorities found nothing ruling, but reference sources flag usage | "usable" |
| `parahomonym_warning` | 0 | a near-name exists within the edit distance you set | conflict; see also the corpus-size warning |
| `no_clear_conflict` | 0 | both authorities answered and neither occupies | validity, and never reachable end-to-end yet |

The last two rows are produced by the same code paths the tests exercise with a
stubbed authority pair; with today's live sources `check` returns `conflict` or
`blocked`. `--debug` prints the swallowed third-party client output to stderr so
you can tell "answered not found" from "request never succeeded"; it changes no
verdict and no exit code.

| Code | Meaning |
|---|---|
| `0` | success (possibly with warnings) |
| `1` | runtime error handled by a command (bad input, missing project, corrupt project file, …) |
| `2` | usage / argument error raised by the CLI framework (unknown flag, missing required option, out-of-range value) |
| `3` | `check` verdict: blocked — an authority was unavailable **or answered with something that cannot rule** (`found_unknown`); local near-match hits are listed as warnings alongside, never promoted to a ruling (see the FAQ) |
| `4` | `check` verdict: conflict — an authority reports the name as occupied |

Only `3` and `4` are non-zero, and only `4` means "occupied". Both warning
verdicts and "no clear conflict" exit `0` — "checked, with caveats" is a
success — so a script must read the verdict, not just the exit code. See the
FAQ for which verdicts the current data sources can actually produce.

Exit 2 belongs to the CLI framework itself (typer/click convention), so the
conflict verdict deliberately uses **4**: a pipeline can tell "typo" from
"name already published" by exit code alone.

Every command prints a one-line disclaimer in human mode and
embeds a `disclaimer` field in `--json` mode, so downstream pipelines can
carry the attribution forward.

---

### Maintaining the shipped data snapshots

Two assets are derived from live services and go stale by design:

```bash
python scripts/build_seqcode_snapshot.py     # SeqCode occupancy list (112+ pages)
python scripts/record_live_gna.py            # GNA contract capture (no credentials)
python scripts/record_live_gna.py --check    # detect upstream schema drift
python scripts/probe_seqcode_api.py          # re-derive the SeqCode endpoint record
python scripts/rebuild_corpus.py             # near-match corpus (LPSN credentials)
```

After replacing an asset on disk, `prokname.reload_data()` applies it in-process
— no restart, and every derived index is dropped with it. Assets are returned
read-only, so a caller that tries to write one gets `TypeError` at the offending
line instead of silently rewriting the rules for the whole process.
`prokname data` reports each asset's version, who consumes it (including the
ones nothing reads any more), and how many of its assertions are still awaiting
expert verification.

---

## 5. Python API

```python
from prokname.engine import generate_candidates, validate_agreement, gender_of, Gender
from prokname.routing import route
from prokname.dedup import check_name

# 1. generate candidates
candidates = generate_candidates(
    "Beijing", "place", "species", genus="Klebsiella")
for c in candidates:
    print(c.name, c.grammatical_category, c.compliant, c.warnings)

# 2. validate an existing binomial
result = validate_agreement("Rhizobium", "beijingense", "place")
print(result.compliant, result.expected_ending, result.warnings)

# 3. gender determination (dual-mode)
gr = gender_of("Treponema")            # lexicon hit → high confidence
gr = gender_of("Mesoplasma")           # inference (Greek -ma neuter) → needs_review=True
gr = gender_of("Zzz")                  # unknown → refuses to guess
gr = gender_of("Somegenus", override=Gender.F)

# 4. dual-code routing
res = route("MAG", icnp_occupied=False)
print([p.code for p in res.viable_paths])

# 5. two-tier dedup check (offline; "Wukomonas" is a synthetic demo genus)
report = check_name("Wukomonas beijingensis")
print(report.verdict, [s.status for s in report.sources])
```

All rule tables are plain versioned JSON under `src/prokname/data/`; load
them read-only via `prokname.engine.data` (`rules()`, `gender_lexicon()`,
`gender_heuristics()`, `person_genitive()`, `stems()`, `corpus_seed()`).

---

## 6. Benchmarks

The shipped seed benchmark (v0.1, expanded 2026-09-04) covers:

| Suite | Cases | Metric | Seed result |
|---|---|---|---|
| A — gender determination | 74 (15 lookup against the published lexicon + 59 inference) | lookup coverage; inference macro-F1/accuracy vs majority-class & naive-ending baselines; refusal-aware accuracy | refusal-aware accuracy **1.00**; raw accuracy 0.98; needs_review rate 1.7 % |
| B1 — agreement validation | 26, of which only **4 negative** (person 2, place 2, feature 0, thing 0) | accuracy; false-negative rate (target ≤ 1 %) | accuracy **1.00**, FNR **0.00** |
| B2 — generation exact-match | 17 | exact-match / top-3 hit rate | **1.00 / 1.00** |
| C — GAN comparison | 5 stems × 3 reps | scaffold: coverage/feasibility vs GAN, CLI-isolated (GPL-3.0) | not executed — `bench --full` runs this suite with `skip_gan=True`; the two-sided comparison against `gan` has not been run |
| D — dual-code routing | 11 (3 ICNP-preemption) | accuracy; preemption sub-class (target 100 %) | **1.00**, preemption **100 %** |

Annotator provenance in these files takes exactly two values, `seed` and
`curated-seed`; there is no third-party annotation and no inter-rater measure.
Reproduce with `prokname bench --full --json > results.json`; the holdout gate
(`prokname holdout`) enforces that the **A-inference** subset stays disjoint
from the published lexicon (the 15 lexicon genera in the A lookup subset
overlap by design — see [`prokname holdout`](#prokname-bench--prokname-holdout)).
All seed cases added in the 2026-09-04 expansion are labelled
`curated-seed`; the explicit *pending M0 LPSN API batch verification* note is
carried per case in `a_set.json` (55 cases) and at file level
(`_meta.expanded`) in the other suites — i.e. it is not attached to every case
of every suite. The paper-grade expansion to LPSN-derived, holdout-controlled
sets (≥ 1 920 cases) is the M1/M2 deliverable. Confidence intervals reported
by `bench --full` are **bootstrap** intervals on the per-case correctness
vector (an accuracy CI, not a macro-F1 CI) and degenerate to a point on
all-hit samples; exact binomial bounds for the small samples are given in
[Known limitations](#10-known-limitations).

---

## 7. Data assets and licensing

| Asset | Purpose | Status |
|---|---|---|
| `rules.json` | rank suffixes, adjective paradigms, type→category map | M0 draft, expert sign-off pending |
| `person_genitive.json` | latinisation-paradigm table + attested-surname lexicon + default rules | only attested/verified entries assert compliance; defaults are `needs_review`; 3 paradigms `verified: false` |
| `genus_gender.json` | lookup lexicon (**58** curated genera: 23 f / 20 m / 15 n) | curated seed |
| `type_genus_stems.json` | type-genus genitive stems + attested higher-rank names for suprageneric derivation | M0 draft; drives the `compliant=None` attestation gate |
| `gender_endings.json` | inference heuristics (morphemes → exceptions → generic endings) | heuristic, always `needs_review` |
| `stems.json` | user-extensible stem library | illustrative seeds (not yet wired into the CLI) |
| `corpus_seed.json` | near-match **demo** corpus: 16 names, `corpus_date` 2026-08-16 (LPSN ×12, synthetic-demo ×2, SeqCode Registry ×1, NCBI ×1) | tests/demos only |
| `lpsn_status.json` | expert-reviewable LPSN `lpsn_taxonomic_status` label → category map (replaces the old substring test) | consumed by `dedup/lpsn.py`; **not** listed by `prokname data` yet |
| `rate_limit_budget.json` | API rate-limit budget table for corpus rebuilds | M2 deliverable |

`prokname data` reports the eight assets wired through
`engine/data.py::asset_status()`; `lpsn_status.json` is loaded directly by the
LPSN adapter and is therefore missing from that listing.

58 (lexicon size) and 59 (A-inference subset size) are unrelated counts; the
A-set itself is 74 = 15 lookup + 59 inference.

**`DATA_LICENSE` is the single source of truth for data licensing, and it
grants exactly two terms:** CC BY-SA 4.0 for the LPSN-derived parts
(attribution + ShareAlike) and CC0 1.0 for the hand-built rule files
(`rules.json`, `gender_endings.json`, `person_genitive.json`, `stems.json`).
Code itself is MIT (`LICENSE`). There is **no** SeqCode-derived licence clause
in `DATA_LICENSE`: the "SeqCode data: CC-BY 4.0" strings emitted by
`storage/store.py` and declared in `corpus_seed.json`'s `_meta.provenance` are
prose, not a granted licence, and the redistribution terms for SeqCode-derived
data are recorded as an open question in `DATA_LICENSE`'s status notice. Every
export and JSON output carries the attribution statements as described above —
treat them as attribution to the upstream sources, and re-check the
`DATA_LICENSE` notice before re-publishing derived assets.

---

## 8. Environment variables and configuration files

| Variable | Effect |
|---|---|
| `PROKNAME_LPSN_USER` / `PROKNAME_LPSN_PASSWORD` | LPSN API credentials (alternatively OS keyring, service `prokname`) |
| `PROKNAME_TAXDUMP_DIR` | NCBI taxdump directory for offline NCBI checks / corpus building |
| `PROKNAME_LPSN_SNAPSHOT` | local LPSN official export directory — **reserved** (FR-10); no adapter consumes it yet (M0) |
| `PROKNAME_CACHE_DIR` | dedup cache directory (default `~/.cache/prokname`) |
| `XDG_CONFIG_HOME` | root for projects (`…/prokname/projects`) and config (`…/prokname/config.json`) |

Precedence: environment variable → persisted config file → built-in default.
`prokname config --taxdump-dir …` / `--offline-snapshot …` write the config
file; `prokname config --clear` removes it.

---

## 9. Troubleshooting and FAQ

**`prokname: bad interpreter: …/prokname/.venv/bin/python3.14: no such file or directory`**
The project directory was moved after `pip install -e .`. Repair:
`.venv/bin/python -m pip install -e ".[dev]"` (regenerates the entry-point
shebangs and the editable path).

**`prokname --version` prints "Missing command."**
Fixed since 2026-09 (`invoke_without_command=True`). If you still see it,
you are running a stale install — re-install as above.

**`import prokname` works but has no `__version__`, `prokname.cli` not found**
The editable `.pth` points to a non-existent path, so Python falls back to a
namespace package created by a leftover `site-packages/prokname` folder.
Re-install (`pip install -e ".[dev]"`) or recreate the virtual environment.

**`gen` says the candidate is `Genus [?]`**
The engine could not form the epithet safely: for `person` etymologies pass
`--person-gender`; for adjectives against an unknown genus pass `--gender m|f|n`
after expert review. This is by design — see [Concepts](#2-concepts).

**`check` exits 3 (blocked) for names that are not occupied**
Two things are true at once, and both are measured rather than assumed:

* **`4` (conflict) is reachable, and covers more than it used to.** Since
  2026-09-25 the SeqCode adapter rules from a dated occupancy snapshot, so a
  name registered as validly published under SeqCode is reported as a
  conflict even with no network at all.
* **`3` is what you get for everything else, because no authority can yet
  answer "no".** SeqCode's public API has no lookup-by-name, and its
  `status=SeqCode` list was measured to omit names that the Registry itself
  marks `Valid (SeqCode)` — so absence from that list is not evidence of
  absence in the registry, and `dedup/seqcode.py` deliberately returns
  `found_unknown`, which blocks. LPSN could settle it, but only reachable and
  with credentials.

Treat `3` as "the tool could not ask", never as "the name is fine". The
intermediate verdicts (`0` with a warning, `0` for "no clear conflict", the
parahomonym warning) become reachable end-to-end as soon as LPSN can answer
negatively; see the live-capture list in `tests/fixtures/manifest.json`.
Local near-match hits are still reported as warnings alongside `3`.
Offline mode reports every authority as `unavailable`, which blocks
adjudication on purpose. Install the optional online extra
(`pip install -e ".[online]"` — the official `lpsn` client, `httpx` and
`keyring`), store credentials (keyring or `PROKNAME_LPSN_USER`/
`PROKNAME_LPSN_PASSWORD`), then run with `--online`; or interpret the local
near-match results knowing the authorities were not consulted.

**Person genitive raises `GenitiveCellUnavailable`**
The surname is not attested in `person_genitive.json` **and** no documented
default rule covers it — typically because it is vowel-final, so its
latinisation paradigm cannot be inferred from spelling. The paradigm cells behind
such defaults are deliberately left `verified: false` pending M0 expert
sign-off against the ICNP orthography appendix. The engine refuses to guess.

**Are GTDB labels supported?**
No — GTDB placeholder labels are not names under the ICNP or the SeqCode and
are neither parsed nor mapped (by design).

---

## 10. Known limitations

Honest boundaries of what this repository demonstrates today. Each item is a
property of the shipped seed data, not a usage mistake.

- **Seed-scale benchmarks only.** A 74 / B1 26 / B2 17 / C 5×3 / D 11 cases.
  Every "1.00" is an all-hit result on a small sample: the exact (Clopper–
  Pearson) 95 % intervals are [0.87, 1.00] for B1 (26/26), [0.80, 1.00] for B2
  (17/17), [0.72, 1.00] for D (11/11) and [0.29, 1.00] for the D-preemption
  subclass (3/3), so "meeting its 100 % target" is not supported by 3/3. The
  bootstrap CIs `bench --full` prints are accuracy CIs and degenerate to a
  point on all-hit samples.
- **The B1 ≤ 1 % false-negative target is not testable as shipped.** B1 has
  exactly **4** negative cases, concentrated in two of four etymology branches
  (person 2, place 2, **feature 0, thing 0**); 0/4 has a one-sided 95 % upper
  bound of **52.7 %**, and a branch with no negatives is unconstrained by the
  gate.
- **Annotator provenance is self-supplied.** The suites record only `seed` and
  `curated-seed`; there is no independent annotator and no inter-rater measure.
- **Expert sign-off is pending (M0).** `rules.json`, `person_genitive.json`
  and the orthographic transliteration table are labelled M0 draft; claims that
  depend on them are "expert-reviewable, pending sign-off", not verified.
- **Higher-rank suffix→code attribution needs adjudication.** `rules.json`
  does label each suprageneric suffix with a strength now
  (`rank_suffix_policy`: `-aceae`/`-ales` mandatory ICNP, `-ota`/`-ia`
  SeqCode recommendations, `-idae`/`-ineae`/`-oideae`/`-eae`/`-inae` adopted
  botanical-code conventions), and the engine refuses to assert compliance
  from the adopted-convention ranks. But the attributions and their rule
  citations are themselves flagged `verified: false`
  (`rule_citation_status`), so "which code mandates which termination" is
  still an expert question, not a settled one. `conserved_names` covers 16
  legacy names, which is far short of the code's appendix lists.
- **The near-match corpus is a 16-entry demo** (`corpus_seed.json`,
  `corpus_date` 2026-08-16; LPSN ×12, synthetic-demo ×2, SeqCode Registry ×1,
  NCBI ×1). No false-positive rate, recall or threshold sensitivity has been
  measured on it; `Wukomonas` is a synthetic illustrative genus.
- **The C-set comparison has not been run.** `bench --full` evaluates the
  C suite with `skip_gan=True`; the gan CLI adapter and the fairness protocol
  exist but produce no comparative numbers, and gan is not installed in CI.
- **Recorded HTTP replay is not part of routine CI.** The online adapters are
  pinned by stubbed offline clients; cassette recording under
  `tests/fixtures/` requires live LPSN credentials and is an M0/M2 item
  (`tests/conftest.py` states the status).
- **The `taxonkit` cross-validation gate is opt-in and skipped in the shipped
  CI images**, which install `.[dev]` only and no `taxonkit` binary; the 3
  cross-check tests are collected and skipped unless you provide it.
- **`check` cannot rule in the field today.** With SeqCode M0-gated to
  `unavailable`, real checks exit 3 (blocked) or 4 (conflict) only.

Chinese counterpart: [USAGE.zh.md §10](USAGE.zh.md#10-已知局限).
