"""Authority adapter: SeqCode Registry.

Same contract as the LPSN adapter: never fabricate; an answer this source
cannot give must block adjudication rather than read as "the name is free".

WHAT CHANGED (2026-09-25). This adapter used to return 'unavailable'
unconditionally, because the M0 gate said the REST contract had never been
recorded. It has now been recorded, by probing the live service:
docs/provenance/seqcode-registry-2026-09-25.md, re-derivable with
scripts/probe_seqcode_api.py. The record produced a surprise that changes the
design, so it is summarised here rather than left in a document nobody reads:

1. There is no lookup-by-name. `names.json` accepts only `page` and `status`;
   unknown parameters are silently ignored and return the full 48 407-name
   list, and the page size is fixed at 30. Asking one question online would
   cost 112 to 1 614 requests. So the adapter does NOT go online at all: it
   rules from a dated local snapshot, the same way the NCBI adapter rules from
   a local taxdump. `allow_network` is accepted and deliberately unused.

2. The snapshot can prove occupancy and nothing else. Measured on 2026-09-25:
   two consecutive identical crawls of `status=SeqCode` each returned 112 pages
   / 3 350 rows but shared only 2 433 of 2 545 / 2 529 unique names, and eight
   names sampled at random whose own registry records answer
   `status_name: "Valid (SeqCode)"` appeared in neither crawl. Absence from the
   snapshot is therefore NOT evidence of absence in the registry, and this
   adapter must never return `not_found`. It returns FOUND_UNKNOWN instead,
   which sits in NON_RULING_STATUSES and keeps `adjudicate()` BLOCKED — which
   is the correct outcome, and now a *measured* one rather than an untested
   placeholder.

   What the snapshot CAN say is the positive: every name found under
   `status=SeqCode` answered `status_name: "Valid (SeqCode)"` on inspection, so
   a hit is a real occupancy ruling. That is new capability — a genome-based
   name that SeqCode published and LPSN has never heard of now produces
   CONFLICT instead of BLOCKED.

3. The clean verdicts (`NO_CLEAR_CONFLICT`, `VERIFY_WARNING`,
   `PARAHOMONYM_WARNING`) therefore stay unreachable through this adapter, and
   depend on LPSN being able to answer negatively. That is the remaining M0
   work; `m0_flip_checklist()` lists what is still outstanding, and
   `prokname check --help` plus USAGE say so in the same words users see.

Data provenance and licence are carried inside the snapshot itself
(`data/seqcode_registered.json`, `_meta`), built by
scripts/build_seqcode_snapshot.py. Upstream terms, quoted verbatim from
https://registry.seqco.de/page/api on 2026-09-25: "All information contributed
to the SeqCode Registry is released under the terms of the Creative Commons
Attribution (CC BY) 4.0 license".
"""

from __future__ import annotations

import datetime as dt
from functools import cache

from ..engine import data as _data
from .model import (
    FOUND_UNKNOWN,
    FOUND_VALID,
    UNAVAILABLE,
    SourceResult,
)

SEQCODE_URL = "https://registry.seqco.de/"
API_DOC_URL = "https://registry.seqco.de/page/api"
SNAPSHOT_ASSET = "seqcode_registered.json"
_TIER = "authority"

#: What is still outstanding before this adapter may rule NEGATIVELY, i.e.
#: before the clean verdicts become reachable. Items 1-4 of the original M0
#: list are DONE (recorded in docs/provenance/seqcode-registry-2026-09-25.md);
#: what remains could not be closed by measurement, because the registry does
#: not offer the capability. Kept as data so tests, `prokname data` and the
#: user-facing detail text all quote the same list.
M0_FLIP_CHECKLIST = (
    "the registry must offer a complete, stable name list (or a lookup by "
    "name): status=SeqCode silently omits names whose own status_name says "
    "'Valid (SeqCode)', and its offset pagination drops records between "
    "consecutive crawls, so absence cannot be ruled",
    "a committed completeness proof for whatever list is used (count "
    "cross-check alone is not one: 3,350 reported rows yielded 2,545 unique "
    "names on the first pass and 2,529 on the second)",
    "SeqCode to state, in its own words, what 'status=SeqCode' selects for "
    "ranks above genus — the per-name status_name field is the only "
    "documentation that exists today",
    "an upstream rate-limit statement, so data/rate_limit_budget.json's "
    "seqcode entry can cite a real number instead of a self-imposed one",
)

#: Positive rulings are backed by direct observation of the upstream
#: `status_name` field; see the module docstring. Flip to False only if the
#: snapshot's own status_selector_basis evidence stops holding.
_POSITIVE_RULINGS_VERIFIED = True


def m0_flip_checklist() -> tuple[str, ...]:
    """What must still land before this adapter may rule 'not found'."""
    return M0_FLIP_CHECKLIST


@cache
def _snapshot() -> dict | None:
    """The occupancy snapshot, or None when it is absent or unreadable.

    Cached on purpose (asset files are immutable inside an installed wheel);
    reload_snapshot() clears this, the derived index and the engine's asset
    cache together, so a rebuilt snapshot cannot be half-applied.
    """
    try:
        return _data.load_json(SNAPSHOT_ASSET)
    except (FileNotFoundError, ModuleNotFoundError):
        return None
    except Exception:  # noqa: BLE001 - a broken asset must not crash `check`
        return None


def reload_snapshot() -> None:
    """Drop the cached snapshot, its index and the underlying asset cache."""
    _index.cache_clear()
    _snapshot.cache_clear()
    _data.load_json.cache_clear()



def snapshot_info() -> dict:
    """What the snapshot is, for `prokname data`, diagnostics and tests."""
    doc = _snapshot()
    if doc is None:
        return {"present": False, "asset": SNAPSHOT_ASSET,
                "hint": "build it with: python scripts/build_seqcode_snapshot.py"}
    meta = doc.get("_meta") or {}
    return {
        "present": True,
        "asset": SNAPSHOT_ASSET,
        "retrieved_at": meta.get("retrieved_at"),
        "names_recorded": meta.get("names_recorded"),
        "source_endpoint": meta.get("source_endpoint"),
        "max_age_days": meta.get("max_age_days"),
        "stale": snapshot_is_stale(),
        "licence": meta.get("licence"),
        "can_rule_negative": False,
        "can_rule_positive": True,
    }


def _retrieved_at() -> dt.datetime | None:
    meta = (_snapshot() or {}).get("_meta") or {}
    raw = meta.get("retrieved_at")
    if not isinstance(raw, str):
        return None
    try:
        return dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.UTC)
    except ValueError:
        return None


def snapshot_age_days() -> float | None:
    """Age of the snapshot in days, or None when it cannot be dated."""
    stamp = _retrieved_at()
    if stamp is None:
        return None
    return (dt.datetime.now(dt.UTC) - stamp).total_seconds() / 86400.0


def snapshot_is_stale() -> bool:
    """True when the snapshot is older than its own declared budget.

    Stale does not disable the adapter: a positive hit is still a hit, and an
    absence was never evidence anyway. It only makes the detail text say so,
    because a six-month-old occupancy list is a different claim from a
    six-hour-old one.
    """
    meta = (_snapshot() or {}).get("_meta") or {}
    limit = meta.get("max_age_days")
    age = snapshot_age_days()
    if age is None or not isinstance(limit, (int, float)):
        return True
    return age > float(limit)


@cache
def _index() -> dict[str, dict]:
    """casefolded name -> snapshot row. One build, reused for every check."""
    rows = (_snapshot() or {}).get("names") or []
    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("name") or "").strip().casefold()
        if key:
            out.setdefault(key, row)
    return out


# Registered here rather than next to reload_snapshot(): these two caches are
# derived from one asset, so a global data reload must drop them together (a
# rebuilt seqcode_registered.json otherwise keeps answering from the previous
# crawl for the life of the process). reload_snapshot() stays the targeted
# entry point. Registration must follow the definitions it references.
def clear_derived_caches() -> None:
    """Drop the snapshot and its derived index together."""
    _snapshot.cache_clear()
    _index.cache_clear()


_data.on_invalidate(clear_derived_caches)


def _uri_for(name: str) -> str:
    row = _index().get(str(name or "").strip().casefold()) or {}
    return str(row.get("uri") or SEQCODE_URL)


def _state_phrase() -> str:
    """How old the snapshot is, phrased for a user-facing detail line."""
    age = snapshot_age_days()
    stamp = (_snapshot() or {}).get("_meta", {}).get("retrieved_at") or "unknown date"
    if age is None:
        return f"snapshot dated {stamp} (age unknown)"
    if age < 1:
        hours = max(0, int(age * 24))
        span = f"{hours} hour{'s' if hours == 1 else ''}" if hours >= 1 else "minutes"
        stale = " — STALE, rebuild with scripts/build_seqcode_snapshot.py" \
            if snapshot_is_stale() else ""
        return f"snapshot dated {stamp} ({span} old){stale}"
    days = int(age)
    suffix = " — STALE, rebuild with scripts/build_seqcode_snapshot.py" \
        if snapshot_is_stale() else ""
    return f"snapshot dated {stamp} ({days} day{'s' if days == 1 else ''} old){suffix}"


def check(name: str, *, allow_network: bool = False) -> SourceResult:
    """Rule on `name` against the SeqCode Registry occupancy snapshot.

    `allow_network` is accepted for adapter symmetry and deliberately ignored:
    the public API cannot answer a name lookup (see the module docstring), so
    going online would mean either 112+ requests per question or a fake answer.
    A hit is a ruling; a miss is explicitly NOT a ruling.
    """
    if _snapshot() is None:
        return SourceResult(
            name="SeqCode", status=UNAVAILABLE, tier=_TIER,
            detail=(
                f"occupancy snapshot {SNAPSHOT_ASSET} is not installed; build "
                "it with `python scripts/build_seqcode_snapshot.py`. "
                "Consequence: adjudicate() returns BLOCKED for every run, "
                "because an authority that was never consulted must not "
                "produce a clean verdict."
            ),
            url=SEQCODE_URL,
            online_derived=False,
        )

    query = str(name or "").strip()
    if not query:
        return SourceResult(
            name="SeqCode", status=UNAVAILABLE, tier=_TIER,
            detail="empty query name; nothing was checked", url=SEQCODE_URL,
        )

    hit = query.casefold() in _index()
    if hit:
        return SourceResult(
            name="SeqCode", status=FOUND_VALID, tier=_TIER,
            detail=(
                f"registered and validly published under SeqCode "
                f"({_state_phrase()}; source status=SeqCode, whose per-name "
                f"status_name reads 'Valid (SeqCode)'): {_uri_for(query)}"
            ),
            url=_uri_for(query),
            verified=_POSITIVE_RULINGS_VERIFIED,
        )
    outstanding = "; ".join(m0_flip_checklist())
    return SourceResult(
        name="SeqCode", status=FOUND_UNKNOWN, tier=_TIER,
        detail=(
            f"not present in the SeqCode snapshot ({_state_phrase()}) — but "
            "that is NOT a ruling that the name is free: the registry's "
            "status=SeqCode list silently omits names whose own status_name "
            "says 'Valid (SeqCode)', so absence is uninformative and "
            "adjudicate() must treat it as unable-to-rule (BLOCKED). "
            f"Still outstanding before this source may answer negatively: "
            f"{outstanding}"
        ),
        url=SEQCODE_URL,
        verified=False,
    )
