"""Verify the upstream facts that prokname's code comments cite.

Writes nothing; prints what each cited source actually says at the cited line,
so docs/provenance/upstream-references.md can be built from an observation
rather than from a transcription of an earlier transcription.

    .venv/bin/python scripts/check_upstream_citations.py
"""
from __future__ import annotations

import re

import httpx

H = {"User-Agent": "prokname-provenance-check/0.1"}

CITED = {
    # Pinned by COMMIT, never by branch: `LeibnizDSMZ/lpsn-api@main` returns
    # 404 today while `@c15229e7` (the pin the code carries) serves the exact
    # lines cited — branch names move or get renamed, and a citation that
    # silently 404s is how "we verified this upstream" turns into folklore.
    "LeibnizDSMZ/lpsn-api README.md @ c15229e7": (
        "https://raw.githubusercontent.com/LeibnizDSMZ/lpsn-api/c15229e7/README.md",
        r"retrieve|taxonomic_status|nomenclatural_status|full_name",
    ),
    "gnames/gnverifier fuzzy-matching.md @ main": (
        "https://raw.githubusercontent.com/gnames/gnverifier/main/fuzzy-matching.md",
        r"stem|epithet|canonical",
    ),
    "seq-code/documentation guide/curation.md @ 10d08dec": (
        "https://raw.githubusercontent.com/seq-code/documentation/10d08dec/"
        "guide/curation.md",
        r"validly published|SeqCode|ICNP|preempt",
    ),
}


def _get(url: str) -> httpx.Response | None:
    """Fetch with retries. A TLS handshake timeout is not a stale citation, and
    reporting one as one would train people to ignore the real signal."""
    last: Exception | None = None
    for attempt, delay in enumerate((0, 3, 8), start=1):
        if delay:
            import time
            time.sleep(delay)
        try:
            return httpx.get(url, headers=H, timeout=45, follow_redirects=True)
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"   attempt {attempt}/3 failed: {type(exc).__name__}",
                  flush=True)
    print(f"   giving up: {last!r}")
    return None


def main() -> int:
    # Console code pages are not a safe assumption for redirected output;
    # see prokname.diagnostics.ensure_reportable_output.
    from prokname.diagnostics import ensure_reportable_output
    ensure_reportable_output()
    failures = 0
    for label, (url, pattern) in CITED.items():
        resp = _get(url)
        if resp is None:
            print(f"UNREACHABLE {label}: {url}")
            failures += 1
            continue
        print(f"=== {label} [{resp.status_code}]")
        if resp.status_code != 200:
            failures += 1
            continue
        hits = [(i, ln) for i, ln in enumerate(resp.text.splitlines(), 1)
                if re.search(pattern, ln, re.I)]
        for i, ln in hits[:8]:
            print(f"   {i:4d}| {ln.strip()[:112]}")
        if not hits:
            print("   (no matching lines — the citation may be stale)")
            failures += 1
    print(f"\n{failures} citation problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
