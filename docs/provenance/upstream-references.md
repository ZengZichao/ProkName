# Upstream citation register

[English](upstream-references.md) | [中文](upstream-references.zh.md)

Every external fact prokname's code relies on, in one place, inside the
repository.

## Why this file exists

Code comments across this project cited sources by **path outside the repo** —
`ProkName-参考项目/gnverifier/fuzzy-matching.md:11-23`,
`/tmp/prokname_fix/nomenclature_facts.md §8`, `审阅报告-项目代码 §2 M5`. For someone
with this repository checked out on a laptop next to those directories, that
works. For anyone who installs `prokname` from a wheel, reads it on a CI runner,
or reviews it eight months from now, those pointers resolve to nothing, and
"`/tmp`" resolves to a directory that is by design already gone.

That is not a cosmetic problem for a tool whose stated product is *auditable*
nomenclature decisions: an assertion about a name that cannot be traced to its
source is not auditable, it is just confident.

So: the facts move here, with the upstream **commit** they were read at, and
code comments point to a section of this file. The rule for a new citation:

> A code comment may cite an upstream fact only if this file records the
> repository, the commit SHA, the file, the line range, what the passage
> establishes, and the date it was last re-read.

Branch names are not pins. Demonstrated while writing this file:
`LeibnizDSMZ/lpsn-api@main/README.md` returns **404**, while the commit the code
already carried, `@c15229e7`, serves the exact cited lines. A citation by branch
quietly rots; a citation by SHA does not.

Re-check every entry, and fail if a cited passage no longer exists:

```bash
.venv/bin/python scripts/check_upstream_citations.py    # exit 1 on drift
```

---

## L1 — LPSN client response shape

| | |
| --- | --- |
| Establishes | `client.retrieve()` yields one record per name; the fields are `full_name` (bare name, no author citation) and `lpsn_taxonomic_status` (a single string label); records carry an `id`. |
| Source | `LeibnizDSMZ/lpsn-api` @ **`c15229e7`**, `README.md:81-96` |
| URL | `https://raw.githubusercontent.com/LeibnizDSMZ/lpsn-api/c15229e7/README.md` |
| Verified | 2026-09-25 by `scripts/check_upstream_citations.py` — the printed example is literally `{782310: [{'full_name': 'Sulfolobus acidocaldarius'}, {'lpsn_taxonomic_status': 'correct name'}], ...}` |
| Consumed by | `dedup/lpsn.py` (identity guard + status classification), `tests/fixtures/lpsn_retrieve_entries.json` |
| Consequence if wrong | A renamed field makes the identity guard stop matching, so a published name degrades to `not_found`; the always-unavailable SeqCode adapter then turns that into `BLOCKED` rather than a false clean verdict. This is the drift the scheduled live-smoke exists to catch. |

**Corollary that is *not* upstream-documented, and is therefore still marked
`verified: false` in the data asset:** real LPSN pages and the API sometimes
return several nomenclatural facts joined into one string, e.g.
`"correct name (and explicitly recommended for medical use)"`. The
comma/parenthetical handling in `dedup/lpsn.py` is built for that shape. It was
originally justified by a note kept in `/tmp/prokname_fix/`, which is
unrecoverable; until a live LPSN response carrying a joined label is recorded as
a cassette (`outstanding_live_captures` in `tests/fixtures/manifest.json`), that
handling rests on observation rather than on a citable source, and the affected
status rows stay unverified.

## L2 — GNmatcher stemmed-canonical calibre

| | |
| --- | --- |
| Establishes | GNA's fuzzy matching compares *stemmed canonical* forms "where suffixes of specific epithets are removed", and reduces edit distance to 1 over the stemmed strings. |
| Source | `gnames/gnverifier`, `fuzzy-matching.md:11-39` |
| Verified | 2026-09-25 — line 16 reads: `using "stemmed canonical forms" where suffixes of specific epithets are` |
| Consumed by | `dedup/nearmatch.py` (the `stem` scan mode) |
| Note | Cited from the default branch because this file is documentation, not code, and the behaviour it describes is stable. Re-pin to a SHA before the near-match calibre is used as a citable claim anywhere (a release note, a data-availability statement). |

## L3 — SeqCode curation and pre-emption advice

| | |
| --- | --- |
| Establishes | A curator confirms the parent genus is "validly published under the SeqCode, ICNP, or ICNafp"; higher ranks are formed from the type genus; the documentation repository carries curation process and licensing but **no REST API contract**. |
| Source | `seq-code/documentation` @ **`10d08dec`**, `guide/curation.md:88` and `:175` |
| Verified | 2026-09-25 — line 88 reads: `a validly published genus under the SeqCode, ICNP, or ICNafp, only page/s for` |
| Consumed by | `routing/router.py` (the pre-emption advice text and its documented negative scope) |

## L4 — SeqCode Registry REST contract and licence

| | |
| --- | --- |
| Establishes | Base `https://api.seqco.de/v1/`; 17 GET routes; `names.json` accepts only `page` and `status` (`public` / `automated` / `SeqCode` / `ICNP` / `ICNafp` / `valid`), 30 rows per page, unknown parameters silently ignored; `names/{id}.json` serves HTML for some ids; no published rate limit; all contributed data is CC BY 4.0. |
| Source | Live service + `https://registry.seqco.de/page/api` |
| Verified | 2026-09-25, probed directly. Full record: [`seqcode-registry-2026-09-25.md`](seqcode-registry-2026-09-25.md) |
| Consumed by | `dedup/seqcode.py` (occupancy snapshot semantics), `DATA_LICENSE`, `data/rate_limit_budget.json` |

## L5 — NCBI taxdump `name_class` vocabulary

| | |
| --- | --- |
| Establishes | Which `name_class` values exist and what they mean — in particular that `authority` is an author citation rather than a synonym, and that `misspelling` is the parahomonym signal. |
| Source | `https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump_readme.txt` — **not vendored**, and the vendored taxonkit snapshot parses `names.dmp` without documenting the vocabulary. |
| Verified | Not verified against a live download. Every row of the resulting semantics table in `dedup/ncbi.py` and `data/lpsn_status.json`-adjacent structures carries `verified: false` for exactly this reason. |
| Consumed by | `dedup/ncbi.py`, `tests/fixtures/ncbi_names_tiny.dmp` |
| What would close it | Download the README, quote the value list here with a retrieval date, and flip the table's `verified` flags. |

## L6 — Design documents that are not distributed with this source

This project was planned against a design plan and a benchmark design document,
and an architecture review recorded numbered findings. None of those documents ships
with this repository, so none of them may be cited as evidence here.

Two rules follow, and both are enforced rather than asserted:

1. A code comment states what the code guarantees, inside the comment. A finding
   number is not reasoning a reader can check, so comments carry the substance of a
   finding and no reference to the report it came from.
2. Where an origin would otherwise be written as `plan §x.x`, the citation is either
   dropped or replaced by something a reader *can* check: a commit-pinned entry in
   this file for an external fact, `DATA_LICENSE` for a licence term, or the data
   asset itself for a rule. The module map in `README` is written the same way — it
   states what this repository ships, not what a plan promised.

## L7 — Vendored reference snapshot

`ProkName-参考项目/` is a read-only local mirror of the upstream projects
(lpsn-api, gnverifier, seqcode-documentation, taxonkit), downloaded 2026-09-07,
with commit pins and licences recorded in its own README. `tests/fixtures/
manifest.json` names it as `vendored_snapshot_root`.

It is **not** part of this repository, so it may not be the only place a
citation resolves. Its role is convenience (offline reading, grep); the
authority for what a source says is the commit-pinned entry above, re-checkable
by `scripts/check_upstream_citations.py`.
