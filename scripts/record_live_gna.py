#!/usr/bin/env python3
"""Record a genuine live GNA GNverifier response as a test fixture.

Closes the M2 live-testing gate stated in ``src/prokname/dedup/gna.py``:

    "before relying on results, confirm one live response still matches this
     schema and record it as a fixture ... a live capture is still outstanding"

The recording pins the field names and the ``matchType`` vocabulary the
service ACTUALLY emits today, so ``gna.check()`` breaks in a test (rather than
silently turning a published name into "not found") the next time upstream
renames something. GNA needs no credentials, so this can be re-run by anyone:

    .venv/bin/python scripts/record_live_gna.py            # writes the fixture
    .venv/bin/python scripts/record_live_gna.py --check     # diff only, no write

``--check`` is the safe mode: it queries the live service, compares the field
set of the response against the recorded fixture and exits non-zero on drift.

Design notes
------------
* The request body is byte-for-byte the one ``dedup/gna.py`` posts
  (``withAllMatches: false``). That matters: with ``withAllMatches: true`` the
  service returns a DIFFERENT shape (``names[].results[]`` instead of
  ``names[].bestResult``), so a fixture recorded in the wrong mode would pin a
  contract production never sees.
* Three queries, one per branch of the adapter's decision table: an Exact hit
  (must rule as a reference, not "not found"), a Fuzzy hit (must rule as a near
  match, never as occupancy — that finding), and a genuine NoMatch (must rule as
  "not found").
* Responses are stored untrimmed. Trimming to "the fields we read" is exactly
  what would hide an upstream rename.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "gna_verifications_live.json"

#: Exactly the endpoint and body prokname.dedup.gna posts.
VERIFICATIONS_URL = "https://verifier.globalnames.org/api/v1/verifications"

#: (query, what the adapter must conclude from it, why that branch matters)
CAPTURES = (
    (
        "Escherichia coli",
        "found_reference",
        "a published, currently-used name must never read as 'not found'",
    ),
    (
        "Escerichia coli",
        "found_near_match",
        "one letter short: a Fuzzy hit is a similarity warning, never occupancy "
        "",
    ),
    (
        "Proknameabsentgenus nowherei",
        "not_found",
        "a string no datasource carries must read as 'not found', the only "
        "honest negative answer the reference tier can give",
    ),
)


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post(name: str) -> dict:
    """POST one verification exactly as the production adapter does."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - environment problem
        raise SystemExit(
            "httpx is required to record a live capture: "
            "pip install -e '.[online]'  (or use .venv/bin/python)"
        ) from exc
    resp = httpx.post(
        VERIFICATIONS_URL,
        json={
            "nameStrings": [name],
            "withAllMatches": False,
            "withVernaculars": False,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _entry(payload: dict) -> dict:
    names = payload.get("names") or []
    return names[0] if names and isinstance(names[0], dict) else {}


def _summary(entry: dict) -> dict:
    """The field-level facts a schema-drift check compares on."""
    best = entry.get("bestResult")
    return {
        "entry_keys": sorted(entry),
        "entry_matchType": entry.get("matchType"),
        "has_bestResult": isinstance(best, dict),
        "best_keys": sorted(best) if isinstance(best, dict) else None,
        "best_matchType": best.get("matchType") if isinstance(best, dict) else None,
        "best_editDistance_type": (
            type(best.get("editDistance")).__name__ if isinstance(best, dict) else None
        ),
        "best_isSynonym_type": (
            type(best.get("isSynonym")).__name__ if isinstance(best, dict) else None
        ),
    }


def record() -> int:
    captures = []
    for query, expected, why in CAPTURES:
        payload = _post(query)
        entry = _entry(payload)
        captures.append(
            {
                "query": query,
                "expected_status": expected,
                "why_this_case": why,
                "observed": _summary(entry),
                "response": payload,
            }
        )
        print(f"recorded {query!r}: entry matchType={entry.get('matchType')!r} "
              f"bestResult={'yes' if isinstance(entry.get('bestResult'), dict) else 'no'}")

    doc = {
        "_provenance": {
            "kind": "live_capture",
            "live_capture": True,
            "recorded_at": _now(),
            "recorded_by": "scripts/record_live_gna.py",
            "endpoint": VERIFICATIONS_URL,
            "request_shape": {
                "nameStrings": ["<one name per query>"],
                "withAllMatches": False,
                "withVernaculars": False,
            },
            "note": (
                "Recorded from the live GNverifier service — the only fixture in "
                "this repository that is. Responses are stored untrimmed on "
                "purpose: the point is to pin the field names and matchType "
                "vocabulary the service emits today, so an upstream rename "
                "breaks tests/test_api_fixtures.py instead of silently turning "
                "a published name into 'not found' (dedup/gna.py guards exactly this). "
                "Re-record with "
                "`python scripts/record_live_gna.py`; check for drift without "
                "writing with `--check`."
            ),
        },
        "captures": captures,
    }
    FIXTURE.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size} bytes)")
    return 0


def check() -> int:
    """Compare the live service against the recorded fixture; no writes."""
    if not FIXTURE.exists():
        print(f"{FIXTURE.relative_to(REPO)} does not exist yet", file=sys.stderr)
        return 2
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    drift = []
    for stored in recorded["captures"]:
        before = stored["observed"]
        after = _summary(_entry(_post(stored["query"])))
        for key, now in after.items():
            was = before.get(key)
            if key.endswith("_keys"):
                added = sorted(set(now or ()) - set(was or ()))
                gone = sorted(set(was or ()) - set(now or ()))
                if gone:
                    drift.append(f"{stored['query']}: {key} dropped {gone}")
                if added:
                    drift.append(f"{stored['query']}: {key} gained {added}")
            elif was != now:
                drift.append(f"{stored['query']}: {key} {was!r} -> {now!r}")
    if drift:
        print("GNA schema drift detected against the recorded live capture:")
        for line in drift:
            print("  -", line)
        return 1
    print("live GNA response still matches the recorded schema")
    return 0


def main() -> int:
    # Console code pages are not a safe assumption for redirected output;
    # see prokname.diagnostics.ensure_reportable_output.
    from prokname.diagnostics import ensure_reportable_output
    ensure_reportable_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="diff the live service against the fixture, write nothing")
    args = parser.parse_args()
    return check() if args.check else record()


if __name__ == "__main__":
    raise SystemExit(main())
