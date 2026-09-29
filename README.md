# prokname

[English](README.md) | [中文](README.zh.md)

Prokaryotic nomenclature assistant: deterministic, auditable decision support
for naming new prokaryotic taxa under the **ICNP** and the **SeqCode**.

> prokname provides decision support only. Name validity is determined solely
> by formal publication under the ICNP or the SeqCode — never by this tool.

The module map below states, for each capability, what this repository actually
ships. A rule's origin is never cited as a section of an undistributed document:
external facts are checkable in `docs/provenance/`, licence terms in
`DATA_LICENSE`, and the rule itself in the shipped data asset.

| Plan module | Status in this repo |
|---|---|
| M0 rule assets (`rules.json`, `person_genitive.json`, `gender_endings.json`, `genus_gender.json`, `stems.json`) | shipped, **expert sign-off pending** — see `prokname data` |
| M1 engine: three-way grammatical categories, dual-mode gender, agreement validation, generation, orthography | implemented |
| Dual-code routing (viable paths + trade-offs, ICNP-preemption-first, GTDB boundary) | implemented |
| Two-tier dedup: local near-match (parahomonym) scan | implemented (demo seed corpus) |
| Dedup: authority adapters (LPSN / SeqCode Registry) | **M0-gated scaffolds** — honestly return `unavailable` (never fabricate); live endpoints/credentials to be recorded at M0 |
| Seed benchmark A/B1/B2/C/D sets (74 / 26 / 17 / 5×3 / 11 cases, holdout-controlled) | implemented (CI-grade seeds; LPSN-derived paper-grade expansion is M1/M2) |
| Project storage (create/add/show/rate/export/delete) | implemented (`prokname project`) |

## Install

```bash
cd prokname
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# optional: enable `prokname check --online` (official lpsn client, httpx, keyring)
pip install -e ".[online]"
```

Full documentation: **[usage guide (USAGE.md)](USAGE.md)** — every command
with examples, exit codes, Python API, benchmark reproduction, and
troubleshooting. 中文版：[USAGE.zh.md](USAGE.zh.md)。

## Usage

```bash
# generate species candidates for a person etymology
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

# higher ranks attach the code-mandated suffix to the genitive stem of the
# type genus (pass the nominative genus name as --stem)
prokname gen --stem Bacillus    --type feature --rank family   # Bacillaceae
prokname gen --stem Clostridium --type feature --rank order    # Clostridiales
prokname gen --stem Pseudomonas --type feature --rank phylum   # Pseudomonadota

# two-tier check (offline by default: near-match scan + honest BLOCKED)
prokname check "Wukomonas beijingensis"
# stem-level caliber (Taxamatch/GNmatcher-style): inflectional endings stripped
prokname check "Wukomonas beijingense" --near-match-mode stem

# dual-code routing with ICNP-preemption awareness
prokname route --source MAG --icnp-occupied no
prokname route --source pure_culture --icnp-occupied no   # both paths + trade-offs
prokname route --source MAG --icnp-occupied yes           # conflict guidance

# engine regression seed (real-name anchored) and data-asset status
prokname bench
prokname data
```

Prefer a window to a terminal? **ProkName Studio** is the desktop front end for
this engine — bilingual (Chinese / English) and light / dark, packaged as its own
project that depends on `prokname`. Install it as you would any other consumer of
this package (see the [Studio repository](https://github.com/ZengZichao/ProkName-Studio))
and start it with `prokname-studio`. It adds no naming logic of its own: every
call goes through the public API below.

The CLI registers **eight** commands (`gen`, `check`, `route`, `project`,
`bench`, `holdout`, `data`, `config`), and all eight take `--json` for
machine-readable output. Exit codes:
`0` ok/warnings · `1` error · `2` usage error (CLI framework) ·
`3` blocked (an authority could not rule; local near-match hits listed as
warnings) · `4` conflict (an authority reports the name).

**What `check` can answer today.** `4` (conflict) is reachable and became more so
on 2026-09-25, when the SeqCode adapter started ruling from a dated occupancy
snapshot: a name registered as validly published under SeqCode now conflicts
even offline, without waiting for LPSN. `3` (blocked) is what every other run
returns, and that is a measured limit rather than an untested placeholder — the
SeqCode Registry's public API has no lookup-by-name, and its `status=SeqCode`
list provably omits names that the Registry itself marks valid, so absence there
cannot be read as "free" (docs/provenance/seqcode-registry-2026-09-25.md).
`0` with a warning, and `0` for "no clear conflict", need BOTH authorities to
answer negatively; LPSN can, once it is reachable with credentials, so these
three verdicts stay unreachable end-to-end until that recording exists. Read `3`
as "the tool could not ask", never as "the name is fine".
("Wukomonas" is an illustrative, synthetic genus, not a published name.)

## Design decisions worth knowing

- **Person epithets are genitive nouns** (indeclinable, ending decided by the
  latinisation *paradigm* the surname was cast into — *Shigella boydii*,
  *Bartonella henselae*, *Borrelia burgdorferi* — not by the honoured person's
  sex, and not by the genus gender). Only true adjectives agree with the genus
  gender. This is the engine's central rule and it is regression-anchored.
  `person_genitive.json` models
  paradigms plus an attested-surname lexicon; compliance is asserted only for
  attested, verified entries, and every default-rule proposal comes back
  `needs_review` — see [Known limitations](#known-limitations).
- **Higher-rank names are built on the genitive stem of the type genus**
  (*Bacillus*, gen. *Bacilli* → `Bacillaceae` / `Bacillales`;
  *Clostridium* → `Clostridiales`; *Pseudomonas* → `Pseudomonadota`), never by
  appending the suffix to the nominative. Conserved names (e.g. class
  `Bacilli`) are preserved rather than "corrected", and names that are
  regularly formed but not attested return `compliant=None` with a warning.
- **`person_genitive.json` never fills a paradigm from memory**: unverified
  paradigms ship as `verified: false` (their output is a flagged proposal) and
  surnames whose paradigm cannot be inferred raise
  `GenitiveCellUnavailable` instead of guessing — closing those cells is an
  M0 sign-off item against the ICNP orthography appendix.
- **Gender determination is dual-mode**: lexicon lookup (high confidence; 58
  curated genera in `genus_gender.json`) or ending/morpheme inference
  (**always** `needs_review`). The engine never defaults to masculine.
- **Authority unavailability blocks adjudication** (`blocked`, exit 3):
  "could not check" must never masquerade as "not found".
- **Routine CI performs no network calls**: the online adapters are pinned by
  stubbed offline clients. `tests/fixtures/` now carries one genuine capture
  from a running service — `gna_verifications_live.json`, recorded by
  `scripts/record_live_gna.py` (GNA needs no credentials) and re-checked for
  upstream drift by the scheduled live-smoke job. LPSN (credentials) and the
  SeqCode REST contract are still unrecorded, so the authority tier is not
  live-verified and `manifest.json` says exactly which is which.
- **Data licensing follows `DATA_LICENSE`**: code MIT (`LICENSE`);
  LPSN-derived data CC BY-SA 4.0 (attribution + ShareAlike); hand-built rule
  files CC0 1.0; SeqCode Registry data CC BY 4.0, quoted from the Registry's
  own API page on 2026-09-25 — so the "SeqCode data: CC-BY 4.0" attribution
  some exports emit is now backed by the source instead of carried over from a
  draft. Two licence questions stay open there and are listed as such: the
  cell-level derivation audit of the mixed rule assets, and whether the EU
  database right is engaged by redistributing the full SeqCode name list.

## Development

```bash
pytest                           # everything this environment collects
pytest --collect-only -q         # authoritative per-module test counts
ruff check src scripts tests     # lint gate (CI-enforced)
prokname bench                   # built-in real-name anchored regression seed
prokname bench --full            # seed benchmark A/B1/B2/C/D, gated (exit 1 on miss)
prokname holdout                 # CI gate: A-INFERENCE subset ∩ lexicon = ∅
```

The number below is generated, not typed: `python scripts/fill_test_counts.py`
writes what `pytest --collect-only` actually collects, and CI fails when it goes
stale (`pytest --collect-only -q` gives the per-module counts behind it).

- This repository's own suite collects 967 tests, with no
  optional extra installed. ProkName Studio's suite lives with Studio and is
  counted there; nothing here is gated on Qt, because nothing here imports it.
- The 3 `taxonkit` cross-check tests are collected everywhere but skipped
  unless that binary is on `PATH`: `.github/workflows/ci.yml` has no `taxonkit`
  installation step, so that cross-validation gate does not run in this
  project's CI.

## Known limitations

Measured boundaries of what this repository demonstrates today (details in
[USAGE.md §10](USAGE.md#10-known-limitations) /
[USAGE.zh.md §10](USAGE.zh.md#10-已知局限)):

- Benchmark suites are **seed-scale** (A 74 = 15 lookup + 59 inference, B1 26
  with only **4 negatives** — none in the `feature` or `thing` branches, B2 17,
  C 5 stems × 3 reps, D 11). All-hit results on n = 3–26 do not establish the
  "≤ 1 % false-negative" or "100 %" targets, and the bootstrap CIs are accuracy
  CIs that degenerate on all-hit samples.
- The near-match corpus is a **16-entry dated demo corpus**
  (`corpus_seed.json`, 2026-08-16; LPSN ×12, synthetic-demo ×2, SeqCode
  Registry ×1, NCBI ×1); no recall/false-positive rate has been measured.
- The **C-set comparison against `gan` has not been run** (`bench --full`
  evaluates it with `skip_gan=True`; CI does not install gan).
- Rule assets await **M0 expert sign-off**. `rules.json` now labels each
  suprageneric suffix with its strength (mandatory ICNP / SeqCode
  recommendation / adopted botanical-code convention for the subranks), but
  the code attributions and their rule citations are themselves flagged
  `verified: false` and still need adjudication.

## Cite

If you use prokname, please cite the software (`CITATION.cff`):

- **Zichao Zeng** (ORCID [0000-0001-6553-970X](https://orcid.org/0000-0001-6553-970X))

Code: MIT (`LICENSE`). Data: see `DATA_LICENSE` — CC BY-SA 4.0 for
LPSN-derived parts, CC0 1.0 for the hand-built rule files, and CC BY 4.0 for
SeqCode Registry data (quoted from the upstream source, 2026-09-25).

## M0 checklist (gates before M1 relies on the rule assets)

1. Expert sign-off on `rules.json` / `person_genitive.json` (fill the
   vowel-stem cells from the ICNP orthography appendix; restructure the
   person-genitive table around latinisation paradigms and re-verify each
   populated cell against real LPSN names).
2. LPSN real-name verification script for every documented example.
3. Live recording of the LPSN endpoint contract (`dedup/lpsn.py` graduates
   from `unavailable`; a recorded VCR cassette for CI replay ships with this
   step). **SeqCode: recorded 2026-09-25** — the contract is documented in
   docs/provenance/seqcode-registry-2026-09-25.md and the adapter now rules
   from a dated occupancy snapshot, positively only. What remains for SeqCode
   is not a recording but a capability the Registry does not offer: a
   complete, stable list or a lookup by name (see item 6).
4. Citation DOI verification (Freese 2026, Ratatoskr, Trüper & de'Clari series).
5. **Done 2026-09-25.** The SeqCode-derived redistribution terms are quoted
   from the Registry's own API page in `DATA_LICENSE` (CC BY 4.0). The
   remaining licence work is listed there: the cell-level derivation audit and
   the database-right question.
6. New, from the same probe: SeqCode's `status=SeqCode` list must become
   complete and stable (or a by-name endpoint must exist) before
   `dedup/seqcode.py` may ever answer "not registered". Until then it returns
   `found_unknown` for a miss, which blocks adjudication exactly as an
   unreachable authority does — deliberately, because an incomplete list is
   not evidence of absence.
