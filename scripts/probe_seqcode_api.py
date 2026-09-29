#!/usr/bin/env python3
"""Re-derive the SeqCode Registry endpoint record in docs/provenance/.

docs/provenance/seqcode-registry-2026-09-25.md states what the public API does
today, which is what decides whether dedup/seqcode.py may ever go online. That
record is a claim about a service this repository does not control, so it must
be re-checkable in one command:

    .venv/bin/python scripts/probe_seqcode_api.py
    .venv/bin/python scripts/probe_seqcode_api.py --json

Exit code is 0 when every observation still matches the recorded one, 1 when
something drifted (and prints what), 2 when the service could not be reached.
Drift is not an error to fix here — it is the news that the M0 gate may be
re-openable, or that a previously working route has gone.

Nothing in this script is imported by prokname; it is a provenance tool, like
scripts/record_live_gna.py. Both deliberately talk to the outside world only
when a human runs them.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import NamedTuple

BASE = "https://api.seqco.de/v1"
AGENT = "prokname-provenance-probe/0.1 (https://github.com/ZengZichao/ProkName)"

#: The observations docs/provenance/seqcode-registry-2026-09-25.md records.
#: `count` is the API's own `response.count`, `pages` its `total_pages`.
RECORDED: dict[str, dict] = {
    "page/status.json": {"status": 200, "json": True, "body": {"status": "ok"}},
    "names.json": {"status": 200, "json": True, "page_size": 30},
    "names.json?status=public": {"count": 48407, "pages": 1614},
    "names.json?status=valid": {"count": 39339},
    "names.json?status=ICNP": {"count": 34951},
    "names.json?status=SeqCode": {"count": 3350, "pages": 112},
    "names.json?status=automated": {"count": 9068},
    "names.json?status=ICNafp": {"count": 1038},
    # An unrecognised status must stay visibly wrong: it answers count 0, i.e.
    # "nothing registered", which is why seqcode.py may never map a zero count
    # onto NOT_FOUND without first validating the status word.
    "names.json?status=nonsense": {"count": 0},
    # Unknown parameters are silently ignored — this is the proof that no
    # by-name lookup exists, so a single check would need 112..1614 requests.
    "names.json?name=Escherichia+coli": {"count": 48407},
    "names.json?search=Escherichia": {"count": 48407},
    "names.json?per_page=500": {"page_size": 30},
    # Documented as JSON with a worked example URL; in fact serves HTML.
    "names/1.json": {"status": 200, "json": False},
}


class Observation(NamedTuple):
    key: str
    status: int | None
    is_json: bool
    count: int | None
    pages: int | None
    page_size: int | None
    body_head: str
    error: str


def _probe(url: str) -> Observation:
    import httpx  # local import: provenance tooling must not become a dependency

    try:
        resp = httpx.get(url, headers={"User-Agent": AGENT}, timeout=30.0,
                         follow_redirects=True)
    except Exception as exc:  # noqa: BLE001 - the answer IS the observation
        return Observation(url.split(BASE + "/")[-1], None, False, None, None,
                           None, "", f"{type(exc).__name__}: {exc}")
    key = url.removeprefix(BASE + "/")
    ctype = resp.headers.get("content-type", "")
    is_json = "json" in ctype
    if not is_json:
        return Observation(key, resp.status_code, False, None, None, None,
                           resp.text[:60].replace("\n", " "), "")
    data = resp.json()
    response = data.get("response") if isinstance(data, dict) else None
    values = data.get("values") if isinstance(data, dict) else None
    count = response.get("count") if isinstance(response, dict) else None
    pages = response.get("total_pages") if isinstance(response, dict) else None
    size = len(values) if isinstance(values, list) else None
    body_head = json.dumps(data, ensure_ascii=False)[:60] if count is None else ""
    return Observation(key, resp.status_code, True, count, pages, size,
                       body_head, "")


def main() -> int:
    # Console code pages are not a safe assumption for redirected output;
    # see prokname.diagnostics.ensure_reportable_output.
    from prokname.diagnostics import ensure_reportable_output
    ensure_reportable_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    observations, drift = [], []
    for key in RECORDED:
        obs = _probe(f"{BASE}/{key}")
        observations.append(obs)
        want = RECORDED[key]
        for field, expected in want.items():
            got = {"status": obs.status, "json": obs.is_json, "count": obs.count,
                   "pages": obs.pages, "page_size": obs.page_size,
                   "body": None}[field]
            if field == "body":
                if obs.error or (obs.body_head and '"status":"ok"' not in
                                 obs.body_head.replace(" ", "")):
                    drift.append(f"{key}: body no longer the documented ok")
            elif got != expected:
                drift.append(f"{key}: {field} {expected!r} -> {got!r}")

    if args.as_json:
        print(json.dumps([o._asdict() for o in observations], indent=2))
    else:
        for o in observations:
            if o.error:
                print(f"ERR   {o.key:38s} {o.error}")
            else:
                print(f"{o.status} {o.key:38s} json={str(o.is_json):5s} "
                      f"count={o.count} pages={o.pages} page_size={o.page_size}")

    unreachable = [o for o in observations if o.error]
    if unreachable and len(unreachable) == len(observations):
        print("\nregistry unreachable: this run says nothing about the contract",
              file=sys.stderr)
        return 2
    if drift:
        print("\ndrift against docs/provenance/seqcode-registry-2026-09-25.md:")
        for line in drift:
            print("  -", line)
        print("\nRe-date the provenance record; if a by-name lookup appeared, "
              "the M0 gate in dedup/seqcode.py is worth reopening.")
        return 1
    print("\nlive service still matches the recorded endpoint contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
