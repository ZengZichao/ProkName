# SeqCode Registry — endpoint and licence record (2026-09-25)

[English](seqcode-registry-2026-09-25.md) | [中文](seqcode-registry-2026-09-25.zh.md)

Resolves two things that `src/prokname/dedup/seqcode.py` and `DATA_LICENSE`
recorded as open questions:

1. the M0 flip checklist item 1 ("record the endpoint path/method/parameters
   from https://registry.seqco.de/page/api") and item 3 ("confirm the field
   names that answer 'registered / valid under SeqCode'");
2. `DATA_LICENSE`'s open question on SeqCode-derived redistribution terms — the
   section now headed "SeqCode Registry data — resolved 2026-09-25", which had
   stated that the repository had never read or quoted the Registry's own
   published terms.

Everything below is dated by the pass that measured it. The record was read from
the live service on **2026-09-25 (UTC)** during this repository's own
optimization pass, and the section **"Re-probe — 2026-11-13"** repeats the same
measurements seven weeks later. No endpoint was invented; the
`prokname` policy that forbids guessing URLs still holds, and this file exists
precisely so the policy's evidence lives inside the repository instead of in a
`/tmp` directory or a maintainer's memory.

## Sources consulted

| What | URL | Notes |
| --- | --- | --- |
| REST API documentation page | `https://registry.seqco.de/page/api` | HTTP 200, `<title>API Documentation \| SeqCode Registry</title>`; the page is server-rendered Rails HTML, no machine-readable schema |
| API root | `https://api.seqco.de/v1/` | HTTP 200, `text/html` (an index page, not JSON) |
| Health probe | `https://api.seqco.de/v1/page/status.json` | HTTP 200, `application/json`, body exactly `{"status":"ok"}` |
| Names list | `https://api.seqco.de/v1/names.json` | HTTP 200, `application/json` |

Response headers on `api.seqco.de`: `server: nginx/1.24.0 + Phusion Passenger(R) 6.1.1`,
`x-content-type-options: nosniff`, `content-type: application/json; charset=utf-8`.
**No `Retry-After`, no `X-RateLimit-*`, no `Link: rel="rate limit"` headers were
returned.**

## Documented routes (verbatim from the API page)

All `GET`, all under `https://api.seqco.de/v1/`:

```
/page/status.json     names.json        names/(id).json     genomes.json
genomes/(id).json     type-genomes.json registers.json      registers/(acc).json
authors.json          authors/(id).json journals.json       journals/(name).json
publications.json     publications/(id).json  subjects.json  subjects/(id).json
strains/(id).json
```

Models (abridged to what a name-occupancy ruling needs):

```
name_item       {id:int, name:str, url:str, uri:str}
response        {status:"ok", message_type:str}
response_paginated
                {status, message_type, count:int, current_page:int,
                 total_pages:int, next:str}
names/(id).json additionally documents rank, status_name, priority_date,
                etymology, nomenclatural_type, classification, children,
                register, proposed_in, not_validly_proposed_in, corrigendum_in,
                emended_in, qc_warnings, created_at, updated_at
```

## What the live service actually does

| Probe | Documented | Observed on 2026-09-25 |
| --- | --- | --- |
| `page/status.json` | `{"status": "ok"}` | ✅ as documented |
| `names.json` parameters | `page`, `status` only | ✅ both honored; **no other parameter is documented and none works** |
| `names.json?status=…` vocabulary | `public`, `automated`, `SeqCode`, `ICNP`, `ICNafp`, `valid` | ✅ all six return data; **an unknown value returns `count: 0` rather than an error** (silently empty — a typo reads as "nothing registered"). *This behaviour no longer holds; see "Re-probe — 2026-11-13".* |
| `names.json?status=public` | — | `count: 48407`, `total_pages: 1614` |
| `names.json?status=valid` | — | `count: 39339` |
| `names.json?status=ICNP` | — | `count: 34951` |
| `names.json?status=SeqCode` | — | `count: 3350`, `total_pages: 112` |
| `names.json?status=automated` | — | `count: 9068` |
| `names.json?status=ICNafp` | — | `count: 1038` |
| page size | not documented | **fixed at 30 items; `per_page` / `limit` / `pp` / `page_size` are all ignored** |
| by-name lookup | not documented | **does not exist.** `?name=`, `?q=`, `?search=` are all silently ignored and return the unfiltered 48 407-name list |
| `names/1.json` | documented, with `https://api.seqco.de/v1/names/1.json` as the example | ❌ **returns `text/html` (the name's web page), not JSON** — the documented JSON detail endpoint is not served at the documented path |

## Consequences for ProkName (this is the finding)

**1. `check` cannot answer "registered under SeqCode?" through this API online.**
There is no by-name lookup. The only way to test one name against
`status=SeqCode` is to walk all 112 pages; against the whole registry, 1 614
pages. Per-query that is neither polite nor fast nor compatible with the
request budget `data/rate_limit_budget.json` is meant to express. The adapter
therefore must NOT be read, on the strength of this record, as entitled to
answer negatively: the only flag this record justifies is
`_POSITIVE_RULINGS_VERIFIED = True` in `dedup/seqcode.py`, which covers the
occupancy findings actually observed, while a name missing from the snapshot
returns `FOUND_UNKNOWN` and never `not_found`. The two items that only capture
can close — a committed live JSON cassette per endpoint *used*, and a green
scheduled live-smoke — still stand, and what remains outstanding before this
source may answer negatively is kept as data in `M0_FLIP_CHECKLIST` in the same
module; the answer recorded there for the lookup-by-name item is that the
endpoint which would answer this question does not exist.

**2. `status=valid` must never be read as "validly published under SeqCode".**
It returns 39 339 names, twelve times the `status=SeqCode` count of 3 350. This
is exactly the trap checklist item 3 warns about ("do NOT reuse LPSN's
`full_name` / `lpsn_taxonomic_status` naming"): the intuitive filter name means
something else upstream. Any future implementation must key on
`status=SeqCode`, and must treat `count: 0` on an unrecognized status value as
`unavailable`, never as `not_found`.

**3. The offline-snapshot route is the only viable one, and it is cheap.**
3 350 SeqCode names × 112 pages at 30/page is a single bounded crawl, the same
shape as `scripts/rebuild_corpus.py` already does for the LPSN-derived near-name
corpus. A versioned `seqcode_registered.json` snapshot lets `check` rule from
local data instead of spending 112 requests on a single question, which is also
what M0's "expert-signable, auditable" posture wants: a dated, attributed
snapshot beats a live call whose answer changes between runs.

That is a design change (new shipped data asset, new refresh cadence, one
licence obligation now settled — see below), and it has since been made:
`scripts/build_seqcode_snapshot.py` builds
`src/prokname/data/seqcode_registered.json` and `dedup/seqcode.py` rules from it.
What that bought is the positive ruling and nothing more — a name found under
`status=SeqCode` is occupancy evidence, so a genome-based name that SeqCode
published and LPSN has never heard of now yields `CONFLICT` instead of `BLOCKED`.
The three clean verdicts (`NO_CLEAR_CONFLICT`, `VERIFY_WARNING`,
`PARAHOMONYM_WARNING`) remain unreachable through this adapter, and it was the
same crawl that produced the snapshot which showed why: absence cannot be ruled,
so a miss answers `FOUND_UNKNOWN` and adjudication stays blocked. Unblocking them
needs the other authority to answer negatively as well, which is kept as data in
`M0_FLIP_CHECKLIST` in `dedup/seqcode.py`.

**4. Cross-check of the counts the repository already carries.**
`seqcode.py`'s docstring reports the full list as 48 407 names and the
`status=SeqCode` crawl as 112 pages / 3 350 rows, both measured on 2026-09-25 —
the same values this probe observed live, which is what settles that the code
keys on `status=SeqCode` and not on `status=valid` (39 339, twelve times the
SeqCode count).

## Re-probe — 2026-11-13

The same script, run again seven weeks later against the same endpoints. The
service's shape is unchanged: still `page` and `status` only, still a fixed
30-item page, still no lookup by name, still HTML at `names/1.json`, still no
published rate limit. What changed is the size of the lists and one behaviour
this record relied on.

| Probe | 2026-09-25 | 2026-11-13 |
| --- | --- | --- |
| `status=public` | 48 407 / 1 614 pages | 49 304 / 1 644 pages |
| `status=valid` | 39 339 | 40 663 |
| `status=ICNP` | 34 951 | 36 208 |
| `status=SeqCode` | 3 350 / 112 pages | 3 417 / 114 pages |
| `status=automated` | 9 068 | 8 641 |
| `status=ICNafp` | 1 038 | 1 038 |
| `status=<unrecognised>` | `count: 0` | `count: 52 395` |

**The empty answer became an everything answer.** An unrecognised `status` value
used to return `count: 0`; it now returns 52 395 rows, more than
`status=public` — the filter is ignored outright instead of matching nothing.
The rule drawn from the old behaviour stands, and matters more now: the service
validates the status word not at all, so no count may be read as a ruling, and
`seqcode.py` must keep ruling from the snapshot. Of the two failure modes the one
that silently matches everything is the more dangerous, because it looks like a
successful query.

**The completeness finding reproduces.** Two consecutive crawls of
`status=SeqCode` walked 114 pages each and yielded 2 517 and 2 537 unique names,
sharing only 2 453: 64 names appeared in the first pass and not the second, 84 in
the second and not the first — while the API reported 3 417 rows for both passes.
Absence from the snapshot is therefore still not evidence of absence in the
registry, and the M0 gate stays exactly where the 2026-09-25 record left it.

`scripts/probe_seqcode_api.py`'s `RECORDED` table now carries the 2026-11-13
numbers, which is what the scheduled live-smoke compares against. The shipped
snapshot was deliberately **not** rebuilt here: at 49 days it is well inside the
90-day window `scripts/build_seqcode_snapshot.py` sets, and a rebuild replaces a
shipped data asset together with every ruling keyed on it — that is its own
change, with its own date.

## Licence terms (resolves the question DATA_LICENSE records under "SeqCode Registry data — resolved 2026-09-25")

Quoted verbatim from the footer of `https://registry.seqco.de/page/api`,
retrieved 2026-09-25:

> © 2022-2026 The SeqCode Initiative
> All information contributed to the SeqCode Registry is released under the
> terms of the Creative Commons Attribution (CC BY) 4.0 license

So the "SeqCode data: CC-BY 4.0" attribution string that `storage/store.py`
emits — and whose licence `corpus_seed.json`'s `_meta.provenance` records — is
**correct as to licence**: it is now quoted from
upstream rather than carried over from an earlier draft. CC BY 4.0 is
attribution-only (no ShareAlike), which is *less* copyleft than the CC BY-SA 4.0
that governs the LPSN-derived assets; see `DATA_LICENSE` for what that means for
redistribution.

**Rate limits: none published.** The API documentation page contains no rate,
quota or throttle statement (searched for rate / limit / throttle / quota), and
no rate-limit response headers were observed. `data/rate_limit_budget.json`
therefore cannot cite an upstream number for SeqCode; until one exists, any
crawl must self-throttle and say so.

## How to re-derive this record

```bash
.venv/bin/python scripts/probe_seqcode_api.py          # re-run every probe above
.venv/bin/python scripts/probe_seqcode_api.py --json   # machine-readable output
```

The script prints one line per probe with the observed counts, so drift (an
endpoint appearing, a parameter starting to work, the detail endpoint starting
to serve JSON) is visible in seconds and this file can be re-dated.
