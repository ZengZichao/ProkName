#!/usr/bin/env python3
"""Build the SeqCode occupancy snapshot that dedup/seqcode.py rules from.

Why a snapshot instead of a live query
--------------------------------------
The SeqCode Registry's public REST API (docs/provenance/
seqcode-registry-2026-09-25.md) documents 17 GET routes and serves them, but it
offers **no lookup by name**: `names.json` accepts only `page` and `status`,
unknown parameters are silently ignored, and the page size is fixed at 30.
Answering "is this exact name registered under SeqCode" against the live API
would therefore mean walking 112 pages for a single name — per query, per run.
That is what this script replaces: one bounded crawl now, an exact local lookup
forever, with the crawl date carried into every ruling.

The plan already expected this. `data/rate_limit_budget.json` has a `seqcode`
entry whose `estimated_calls.full_export` is described as "Full SeqCode Registry
export for local corpus" — this script is that export, and it is also the first
thing in the runtime that actually reads the budget file for pacing; before
this script the budget drove no limiter at all.

Usage
-----
    .venv/bin/python scripts/build_seqcode_snapshot.py            # status=SeqCode
    .venv/bin/python scripts/build_seqcode_snapshot.py --dry-run  # 2 pages, no write
    .venv/bin/python scripts/build_seqcode_snapshot.py --status public   # whole registry
    .venv/bin/python scripts/build_seqcode_snapshot.py --max-pages 5     # partial (debug)

Pacing comes from rate_limit_budget.json (seqcode.rate_limit.
max_requests_per_second). With a 429 or a 5xx the crawl backs off exponentially
and retries, then stops rather than hammering: an incomplete snapshot is
refused, never written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
DATA = SRC / "prokname" / "data"
BUDGET_FILE = DATA / "rate_limit_budget.json"
OUT_DEFAULT = DATA / "seqcode_registered.json"

API_BASE = "https://api.seqco.de/v1"
AGENT = "prokname-snapshot-builder/0.1 (https://github.com/ZengZichao/ProkName)"

#: Verbatim from the footer of https://registry.seqco.de/page/api, 2026-09-25.
LICENCE_QUOTE = (
    "© 2022-2026 The SeqCode Initiative — All information contributed to the "
    "SeqCode Registry is released under the terms of the Creative Commons "
    "Attribution (CC BY) 4.0 license"
)

#: How old the snapshot may get before seqcode.py marks its own answers
#: provisional. SeqCode names are added slowly (~0.4 % over six weeks, see the
#: provenance record), so a quarter is honest for a nomenclatural occupancy
#: check while still forcing a refresh cadence.
MAX_AGE_DAYS = 90


def _pace_from_budget() -> float:
    """Seconds between requests, taken from the project's own budget file."""
    try:
        budget = json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
        rps = float(budget["seqcode"]["rate_limit"]["max_requests_per_second"])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"warning: cannot read seqcode pacing from {BUDGET_FILE.name} "
              f"({exc!r}); falling back to 1 request/second", file=sys.stderr)
        return 1.0
    if rps <= 0:
        return 1.0
    return 1.0 / rps


def _get(url: str, *, attempts: int = 4) -> dict:
    """GET one JSON page with exponential backoff; raise on refusal."""
    import httpx

    delay = 2.0
    last: str = "no attempt"
    for attempt in range(1, attempts + 1):
        try:
            resp = httpx.get(url, headers={"User-Agent": AGENT,
                                           "Accept": "application/json"},
                             timeout=30.0, follow_redirects=True)
            if resp.status_code == 200:
                ctype = resp.headers.get("content-type", "")
                if "json" not in ctype:
                    raise RuntimeError(
                        f"expected JSON, got {ctype!r} — the API shape changed; "
                        "re-run scripts/probe_seqcode_api.py and re-date the "
                        "provenance record before trusting this crawl")
                return resp.json()
            last = f"HTTP {resp.status_code}"
            if resp.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"{url}: {last} (not retryable)")
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - network is the failure mode
            last = f"{type(exc).__name__}: {exc}"
        if attempt < attempts:
            print(f"  {last}; retry {attempt + 1}/{attempts} in {delay:.0f}s",
                  file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{url}: giving up after {attempts} attempts ({last})")


def crawl(status: str, *, max_pages: int | None, dry_run: bool) -> tuple[list[dict], dict]:
    """Walk names.json?status=<status> to exhaustion. Returns (names, stats)."""
    pace = _pace_from_budget()
    url = f"{API_BASE}/names.json?status={status}"
    seen: dict[str, dict] = {}
    pages = 0
    rows = 0
    reported: dict = {}
    while url:
        payload = _get(url)
        response = payload.get("response") or {}
        values = payload.get("values") or []
        pages += 1
        rows += len(values)
        if not reported:
            reported = {k: response.get(k) for k in
                        ("count", "current_page", "total_pages")}
        for item in values:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            seen[name] = {"name": name, "id": item.get("id"),
                          "uri": item.get("uri")}
        print(f"  page {response.get('current_page', pages):>5}/"
              f"{response.get('total_pages', '?'):<6} +{len(values):<3} "
              f"unique={len(seen)}")
        nxt = response.get("next")
        if max_pages and pages >= max_pages:
            print(f"  stopped at --max-pages {max_pages}", file=sys.stderr)
            nxt = None
        elif dry_run and pages >= 2:
            nxt = None
        url = nxt or ""
        if url:
            time.sleep(pace)
    return sorted(seen.values(), key=lambda r: r["name"].casefold()), {
        "pages_fetched": pages,
        "rows_seen": rows,
        "count_reported_by_api": reported.get("count"),
        "total_pages_reported": reported.get("total_pages"),
    }


def union_crawl(status: str, *, passes: int, max_pages: int | None,
                dry_run: bool) -> tuple[list[dict], dict]:
    """Crawl `passes` times and keep the union, because one pass is not stable.

    Measured on 2026-09-25: two consecutive identical crawls of
    status=SeqCode both returned 112 pages / 3,350 rows, but only 2,433 of
    2,545 / 2,529 names were shared — 96 names appeared only in the second
    pass and 112 only in the first. Offset pagination over a list that is
    being written while it is read does that. Unioning passes is the only
    remedy this API allows (it has no keyset or sort parameter), and it buys
    recall for the positive rulings this snapshot is permitted to make. It does
    NOT make the list complete, so absence must never rule. See
    docs/provenance/seqcode-registry-2026-09-25.md.
    """
    merged: dict[str, dict] = {}
    per_pass: list[int] = []
    for index in range(1, (passes if not dry_run else min(passes, 2)) + 1):
        print(f"pass {index}/{passes if not dry_run else 2}")
        names, stats = crawl(status, max_pages=max_pages, dry_run=dry_run)
        per_pass.append(len(names))
        for row in names:
            merged.setdefault(row["name"], row)
        if dry_run:
            break
    return sorted(merged.values(), key=lambda r: r["name"].casefold()), {
        **stats,
        "passes": passes if not dry_run else 1,
        "unique_per_pass": per_pass,
        "union_after_passes": len(merged),
        "pagination_churn": sum(p - min(per_pass) for p in per_pass),
    }


def build(status: str, out: Path, *, dry_run: bool,
          max_pages: int | None, passes: int) -> int:
    print(f"crawling {API_BASE}/names.json?status={status}")
    names, stats = union_crawl(status, passes=passes, max_pages=max_pages,
                               dry_run=dry_run)

    completeness_issues: list[str] = []
    if not dry_run and stats["total_pages_reported"]:
        if stats["pages_fetched"] < stats["total_pages_reported"]:
            completeness_issues.append(
                f"only {stats['pages_fetched']} of "
                f"{stats['total_pages_reported']} pages fetched")
    if stats["count_reported_by_api"] and len(names) > stats["count_reported_by_api"]:
        completeness_issues.append(
            f"collected {len(names)} names but the API reported "
            f"{stats['count_reported_by_api']}")

    blob = "\n".join(r["name"] for r in names).encode("utf-8")
    doc = {
        "_meta": {
            "kind": ("SeqCode Registry names, filtered by the API's own "
                     f"status={status} selector"),
            "version": "0.1.0-m0",
            "retrieved_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_endpoint": f"{API_BASE}/names.json?status={status}",
            "built_by": "scripts/build_seqcode_snapshot.py",
            "licence": LICENCE_QUOTE,
            "licence_source": "https://registry.seqco.de/page/api (footer), "
                              "retrieved 2026-09-25",
            "attribution": "Data © The SeqCode Initiative, CC BY 4.0. "
                           "Cite https://seqco.de/ and the individual "
                           "https://seqco.de/i:<id> URIs when republishing.",
            "status_selector_basis": (
                "status=SeqCode is READ as 'registered and validly published "
                "under SeqCode', and that reading was checked against the "
                "registry's own per-name `status_name` field: every name found "
                "in this filter answered status_name 'Valid (SeqCode)', while "
                "names outside it answered 'Automated discovery' or "
                "'Valid (ICNP)'. The observed status_name vocabulary is "
                "Automated discovery / Valid (SeqCode) / Valid (ICNP), and the "
                "selector counts add up exactly (SeqCode 3,350 + ICNP 34,951 + "
                "ICNafp 1,038 = valid 39,339), so the filter means what it says "
                "— but see negative_scope for what it cannot say."
            ),
            "negative_scope": (
                "ABSENCE FROM THIS LIST IS NOT EVIDENCE OF ABSENCE, and "
                "seqcode.py must never rule 'not found' from it. Measured on "
                "2026-09-25: (a) two consecutive identical crawls of this "
                "selector each returned 112 pages / 3,350 rows but shared only "
                "2,433 of 2,545 / 2,529 unique names, i.e. offset pagination "
                "over a mutating list is unstable and drops records; (b) eight "
                "names sampled at random whose own records answer status_name "
                "'Valid (SeqCode)' appeared in NEITHER crawl. The API offers no "
                "keyset pagination, no sort and no lookup-by-name, so "
                "completeness is not achievable here. This snapshot therefore "
                "supports positive occupancy rulings only; the three clean "
                "verdicts in adjudicate() stay gated on LPSN answering "
                "negatively, which is an M0 deliverable, not a bug to optimise "
                "away. See docs/provenance/seqcode-registry-2026-09-25.md."
            ),
            "max_age_days": MAX_AGE_DAYS,
            "names_sha256": hashlib.sha256(blob).hexdigest(),
            **stats,
            "names_recorded": len(names),
            "completeness_issues": completeness_issues,
            "dry_run": dry_run,
        },
        "names": names,
    }

    if dry_run:
        print(json.dumps(doc["_meta"], indent=2, ensure_ascii=False))
        print(f"(dry run: first {min(5, len(names))} names) "
              f"{json.dumps([r['name'] for r in names[:5]])}")
        print("nothing written")
        return 0
    if completeness_issues:
        print("refusing to write an incomplete snapshot:", file=sys.stderr)
        for issue in completeness_issues:
            print("  -", issue, file=sys.stderr)
        return 1

    sys.path.insert(0, str(SRC))
    from prokname._atomic import atomic_write_text  # reuse the shipped writer

    atomic_write_text(out, json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out.relative_to(REPO)}: {len(names)} names, "
          f"{out.stat().st_size} bytes")
    return 0


def main() -> int:
    # Console code pages are not a safe assumption for redirected output;
    # see prokname.diagnostics.ensure_reportable_output.
    from prokname.diagnostics import ensure_reportable_output
    ensure_reportable_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", default="SeqCode",
                        help="API status selector to snapshot (default SeqCode; "
                             "'public' is the whole registry, ~1614 pages)")
    parser.add_argument("--output", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch two pages, print the metadata, write nothing")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="stop early (debug); implies an incomplete snapshot")
    parser.add_argument("--passes", type=int, default=3,
                        help="how many full crawls to union (default 3; the "
                             "endpoint's pagination is unstable, so one pass "
                             "drops records — see the module docstring)")
    args = parser.parse_args()
    return build(args.status, args.output, dry_run=args.dry_run,
                 max_pages=args.max_pages, passes=max(1, args.passes))


if __name__ == "__main__":
    raise SystemExit(main())
