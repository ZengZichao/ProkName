"""Cache layer for dedup query results.

Successful query results are cached per (name, source, online-state) with a
7-day TTL. This avoids redundant API calls and supports the rate-limit budget
. Cache is stored as JSON files under
~/.cache/prokname/ (or PROKNAME_CACHE_DIR).

Freshness contract (review that finding — this section used to promise a
`query_date` component that `_cache_path()` never had)
---------------------------------------------------------------------
The key is `(name, source, online-state)`; the *date is deliberately NOT part
of the key*. Freshness is governed by one mechanism only — the TTL
(`max_age_days`, default 7):

- an entry younger than the TTL is served, whatever calendar day it came from;
- an entry older than the TTL is deleted on read and the source is re-queried;
- therefore a name published *today* can be masked by a cached `not_found` for
  up to `max_age_days`. That is the price of the rate-limit budget: callers who
  cannot pay it pass `max_age_days=0` (never serve) or call `invalidate()`
  after an authority release.

Folding the calendar day into the key was rejected deliberately: it multiplies
the on-disk footprint once per day per name and makes the first run of a new
day look like a cold cache while the previous day's answer is still well
within its intended TTL.

Online-state binding
----------------------------------------------------
`put(..., online=True)` records that the answer came out of a LIVE query and
`get(..., online=False)` refuses such records. An offline run therefore cannot
replay an online `not_found` to justify NO_CLEAR_CONFLICT, so the contract
"when the authority cannot be reached, no conclusion is issued" holds again.
The `(cached)` annotation in the report detail is kept. Credential state is
part of the same discipline: an online run without credentials answers
`unavailable`, and `unavailable` is never cached.

Other design points:
- The cache directory is resolved on every call (not at import time) so tests
  and long-lived processes can retarget it via PROKNAME_CACHE_DIR.
- Cache filenames carry a content hash of the raw name: the on-disk
  sanitisation (spaces → underscores) is lossy, and 'Bacillus subtilis' vs
  'Bacillus_subtilis' must not collide.
- Writes are atomic (temp file + os.replace) so a crash never leaves a
  half-written JSON behind.
- Checkpoint/resume for long batch runs lives in scripts/rebuild_corpus.py
  under `CACHE_DIR/_checkpoints/…`; `clear()` protects it (see
  `PROTECTED_NAMES`, `_looks_like_cache_dir`).
- `clear()` never removes a directory tree: it unlinks only files that parse
  as prokname cache records, refuses to touch a directory that does not look
  like a prokname cache, and supports `dry_run`. Batch artifacts written by
  scripts/rebuild_corpus.py (per-name JSON slices) are not cache records and
  are left alone by design.
- Cache is purely local; no external service dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .._atomic import atomic_write_json as _atomic_write_json

DEFAULT_TTL_DAYS = 7

#: Files that are never cache entries and must survive `clear()`.
PROTECTED_NAMES = frozenset({"_checkpoint.json", "index.json"})

#: Marker file written next to the entries so `clear()` can tell a prokname
#: cache directory from an unrelated directory the user mis-pointed
#: PROKNAME_CACHE_DIR at.
MARKER_NAME = ".prokname-cache.json"
_MARKER_PAYLOAD = {"tool": "prokname", "kind": "dedup-query-cache"}


def cache_dir() -> Path:
    """Resolve the cache root on every call (env var wins, for tests too)."""
    return Path(os.environ.get(
        "PROKNAME_CACHE_DIR",
        os.path.join(os.path.expanduser("~"), ".cache", "prokname"),
    ))


def _state_tag(online: bool) -> str:
    return "online" if online else "offline"


def _cache_path(source: str, name: str, *, online: bool = False) -> Path:
    """Return the collision-free cache file path for a (source, name, state)."""
    safe_name = name.replace(" ", "_").replace("/", "_")
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return (cache_dir() / source /
            f"{safe_name}.{_state_tag(online)}.{digest}.json")


def _is_cache_record(payload: Any) -> bool:
    return (isinstance(payload, dict) and "_cached_at" in payload
            and "_query" in payload)


def ensure_cache_marker() -> Path:
    """Write (once) the marker that identifies this directory as ours."""
    root = cache_dir()
    marker = root / MARKER_NAME
    if not marker.exists():
        root.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(marker, {
            **_MARKER_PAYLOAD,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        })
    return marker


def _is_cache_shape(path: Path) -> bool:
    """True when a file parses as a prokname cache record."""
    try:
        return _is_cache_record(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        return False


def _looks_like_cache_dir(base: Path) -> bool:
    """Guard against `PROKNAME_CACHE_DIR` pointing at somebody else's data.

    Accepted: the marker file, a directory that already holds at least one
    record-shaped JSON, or an UNOVERRIDDEN default root named `prokname`.
    """
    if (base / MARKER_NAME).exists():
        return True
    default_root = Path(os.path.join(os.path.expanduser("~"), ".cache",
                                     "prokname"))
    if "PROKNAME_CACHE_DIR" not in os.environ and base == default_root:
        return True
    for path in base.rglob("*.json"):
        if path.name in PROTECTED_NAMES:
            continue
        if _is_cache_shape(path):
            return True
    return False


def get(source: str, name: str, *, online: bool = False,
        max_age_days: int = DEFAULT_TTL_DAYS) -> dict | None:
    """Retrieve a cached result if it exists, is fresh, and matches the state.

    Args:
        source: Data source identifier (e.g. "lpsn", "seqcode").
        name: The queried name (e.g. "Escherichia coli").
        online: The network state of the calling run. Records obtained against
            a live authority are NEVER handed to an offline run (review defect
            offline records are likewise not handed to an online run.
        max_age_days: Maximum age in days before the entry is considered stale.

    Returns:
        The cached dict, or None if it is missing / stale / belongs to a
        different name / was produced in the other network state.
    """
    path = _cache_path(source, name, online=online)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    # Guard against sanitisation collisions: the on-disk entry must belong
    # to exactly the requested name.
    if data.get("_query") != name:
        return None

    # Belt and braces with the path tag: a record written by a newer/older
    # layout must not cross the online/offline boundary either.
    if bool(data.get("_online", False)) != bool(online):
        return None

    cached_at = data.get("_cached_at")
    if not cached_at:
        return None

    # Check TTL
    try:
        cached_time = datetime.fromisoformat(cached_at)
        age = (datetime.now(UTC) - cached_time).total_seconds()
        if age > max_age_days * 86400:
            # Stale: prune and return None
            path.unlink(missing_ok=True)
            return None
    except (ValueError, TypeError):
        return None

    return data


def put(source: str, name: str, result: dict, *, online: bool = False) -> None:
    """Store a query result in the cache, tagged with its network state.

    Args:
        source: Data source identifier.
        name: The queried name.
        result: The result dict to cache (augmented with metadata).
        online: True when the answer came from a live query. Stored so that
                `get()` can refuse to replay it offline.
    """
    # Don't cache 'unavailable' results (they may be transient)
    if result.get("status", "") == "unavailable":
        return

    ensure_cache_marker()
    cached = {
        **result,
        "_cached_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "_source": source,
        "_query": name,
        "_online": bool(online),
    }
    _atomic_write_json(_cache_path(source, name, online=online), cached)


def invalidate(source: str, name: str, *, online: bool | None = None) -> bool:
    """Remove a specific cache entry. Returns True if it existed.

    `online=None` (default) removes the entry for both network states.
    """
    states = [False, True] if online is None else [bool(online)]
    removed = False
    for state in states:
        path = _cache_path(source, name, online=state)
        if path.exists():
            path.unlink()
            removed = True
    return removed


def clear_plan(source: str | None = None) -> list[Path]:
    """The files `clear()` would remove (nothing is deleted here)."""
    base = cache_dir() / source if source else cache_dir()
    if not base.exists():
        return []
    plan: list[Path] = []
    for path in sorted(base.rglob("*.json")):
        if path.name in PROTECTED_NAMES or path.name == MARKER_NAME:
            continue
        if _is_cache_shape(path):
            plan.append(path)
    return plan


def clear(source: str | None = None, *, dry_run: bool = False,
          confirm: bool = False) -> int:
    """Clear cache entries, optionally filtered by source.

    Safety rules added for review that finding:

    - batch checkpoints (`_checkpoint.json` and the `_checkpoints/` tree
      written by scripts/rebuild_corpus.py) survive;
    - only files that parse as prokname cache records are removed — no
      directory tree is ever rmtree'd, so a mis-pointed PROKNAME_CACHE_DIR
      cannot be wiped wholesale;
    - a directory that does not look like a prokname cache raises;
    - `dry_run=True` reports the count without touching the disk;
    - `confirm=True` is required to clear the WHOLE cache (`source=None`).

    Returns:
        Number of entries removed (or, with dry_run, that would be removed).
    """
    base = cache_dir() / source if source else cache_dir()
    if not base.exists():
        return 0
    if not _looks_like_cache_dir(base):
        raise RuntimeError(
            f"refusing to clear {str(base)!r}: it does not look like a prokname "
            f"cache directory (no {MARKER_NAME!r} marker and no cache-shaped "
            "JSON files). Check PROKNAME_CACHE_DIR — clear() never deletes "
            "foreign trees."
        )
    if source is None and not confirm and not dry_run:
        raise RuntimeError(
            "refusing to clear the ENTIRE dedup cache without confirmation: "
            "pass confirm=True (after asking the user) or clear(source=...) "
            "for one source"
        )

    removed = 0
    for path in clear_plan(source):
        if dry_run:
            removed += 1
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:  # pragma: no cover - raced with another process
            continue
    if not dry_run:
        _prune_empty_dirs(base)
    return removed


def _prune_empty_dirs(base: Path) -> None:
    """Remove directories our deletions left empty (never files)."""
    if not base.exists():
        return
    for path in sorted((p for p in base.rglob("*") if p.is_dir()),
                       key=lambda p: len(p.parts), reverse=True):
        if path.name.startswith("_"):
            continue  # _checkpoints/ etc. belong to other tools
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
        except OSError:  # pragma: no cover
            continue


def stats() -> dict[str, Any]:
    """Return cache statistics."""
    root = cache_dir()
    if not root.exists():
        return {"total_entries": 0, "by_source": {}, "cache_dir": str(root)}

    by_source: dict[str, int] = {}
    total = 0
    for path in root.rglob("*.json"):
        if path.name in PROTECTED_NAMES or path.name == MARKER_NAME:
            continue
        if any(part.startswith("_")
               for part in path.relative_to(root).parts[:-1]):
            continue  # checkpoint / sidecar trees are not cache entries
        source = path.parent.name
        by_source[source] = by_source.get(source, 0) + 1
        total += 1

    return {
        "total_entries": total,
        "by_source": by_source,
        "cache_dir": str(root),
        "looks_like_cache": (root / MARKER_NAME).exists() or total > 0,
    }
