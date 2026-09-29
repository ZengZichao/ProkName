# SeqCode Registry — endpoint and licence record (2026-09-25)

[English](seqcode-registry-2026-09-25.md) | [中文](seqcode-registry-2026-09-25.zh.md)

Resolves two things that `src/prokname/dedup/seqcode.py` and `DATA_LICENSE`
recorded as open questions:

1. the M0 flip checklist item 1 ("record the endpoint path/method/parameters
   from https://registry.seqco.de/page/api") and item 3 ("confirm the field
   names that answer 'registered / valid under SeqCode'");
2. `DATA_LICENSE` "Open question / unresolved — SeqCode-derived redistribution
   terms", which stated that the repository had never read or quoted the
   Registry's own published terms.

Everything below was read from the live service on **2026-09-25 (UTC)** during
this repository's own optimization pass. No endpoint was invented; the
`prokname` policy that forbids guessing URLs still holds, and this file exists
precisely so the policy's evidence lives inside the repository instead of in a
`/tmp` directory or a maintainer's memory.

## Sources consulted

| What | URL | Notes |
| --- | --- | --- |
| REST API documentation page | `https://registry.seqco.de/page/api` | HTTP 200, `<title>API Documentation | SeqCode Registry</title>`; the page is server-rendered Rails HTML, no machine-readable schema |
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
| `names.json?status=…` vocabulary | `public`, `automated`, `SeqCode`, `ICNP`, `ICNafp`, `valid` | ✅ all six return data; **an unknown value returns `count: 0` rather than an error** (silently empty — a typo reads as "nothing registered") |
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
therefore must NOT be flipped to `_M0_ENDPOINT_VERIFIED = True` on the strength
of this record: items 2 and 5 of the checklist (a committed live JSON cassette
per endpoint *used*, and a green scheduled live-smoke) still stand, and item 1's
answer is "the endpoint that would answer this question does not exist".

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
corpus. A versioned `seqcode_registered.json` snapshot would let `check` give
the three currently-unreachable verdicts (`NO_CLEAR_CONFLICT`,
`VERIFY_WARNING`, `PARAHOMONYM_WARNING`) from local data, which is also what
M0's "expert-signable, auditable" posture wants: a dated, attributed snapshot
beats a live call whose answer changes between runs.

That is a design change (new shipped data asset, new refresh cadence, one
licence obligation now settled — see below), so it is recorded here as the
recommended path rather than implemented unilaterally.

**4. Cross-check of the counts the repository already carries.**
`seqcode.py`'s docstring states "48,386 names / 3,338 validly published under
SeqCode as of 2026-08-16". Live on 2026-09-25: `status=public` 48 407 and
`status=SeqCode` 3 350. Both grew in the expected direction by ~0.05 % and ~0.4 %
over six weeks, which independently corroborates that `status=SeqCode` is the
count the docstring meant — and that `status=valid` (39 339) is not.

## Licence terms (resolves DATA_LICENSE's open question item 1)

Quoted verbatim from the footer of `https://registry.seqco.de/page/api`,
retrieved 2026-09-25:

> © 2022-2026 The SeqCode Initiative
> All information contributed to the SeqCode Registry is released under the
> terms of the Creative Commons Attribution (CC BY) 4.0 license

So the "SeqCode data: CC-BY 4.0" attribution string that `storage/store.py` and
`corpus_seed.json` emit is **correct as to licence** — it is now quoted from
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
