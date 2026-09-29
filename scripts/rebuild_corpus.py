"""Snapshot rebuild scripts for benchmark corpus construction.

These scripts rebuild the benchmark test sets from official data sources:
- LPSN: via the official API/downloads (CC BY-SA 4.0; scraping forbidden)
- SeqCode Registry: via the official REST API (CC-BY 4.0)
- NCBI taxdump: via the official FTP bulk download

Usage:
    python scripts/rebuild_corpus.py --source lpsn --snapshot-date 2026-09-01
    python scripts/rebuild_corpus.py --source seqcode
    python scripts/rebuild_corpus.py --source taxdump
    python scripts/rebuild_corpus.py --source all --output-dir src/prokname/data/

Requirements:
    - LPSN: credentials via keyring or PROKNAME_LPSN_USER/PASSWORD env vars
    - SeqCode: no auth required (public API)
    - NCBI taxdump: no auth required (FTP download)

M0 gate: the exact LPSN API endpoint/field mapping and SeqCode REST paths must
be recorded from the official documentation before these scripts produce
paper-grade data. Until then, they provide the scaffold with rate-limiting,
caching, and checkpoint/resume logic.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

# Make src importable without installation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---- Rate limiter -----------------------------------------------------------

class RateLimiter:
    """Token-bucket rate limiter for API calls."""

    def __init__(self, max_rps: float = 1.0):
        self.min_interval = 1.0 / max_rps if max_rps > 0 else 0
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


# ---- Cache + checkpoint -----------------------------------------------------

CACHE_DIR = Path(os.environ.get(
    "PROKNAME_CACHE_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "prokname"),
))

#: Sub-tree that holds batch resume state. The leading underscore keeps it out
#: of the per-source cache-entry namespace, so `prokname.dedup.cache.clear()`
#: cannot destroy a long-running rebuild.
CHECKPOINT_ROOT = "_checkpoints"


def cache_key(source: str, name: str, date: str) -> Path:
    # NB: the old dict-comprehension version keyed the dict by the argument
    # VALUES and then indexed it with the literal names "source"/"date" — a
    # guaranteed KeyError that was masked by the broken retrieve() call
    # upstream. Sanitize each part explicitly instead.
    def _sanitize(part: str) -> str:
        return part.replace(" ", "_").replace("/", "_").replace("..", "_")

    return CACHE_DIR / _sanitize(source) / _sanitize(date) / f"{_sanitize(name)}.json"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically: temp file in the same directory, then replace.

    Long batch runs are interrupted routinely; a half-written cache entry or
    checkpoint must never survive as if it were complete.
    """
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload) -> None:
    _atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2)
    )


def load_cache(source: str, name: str, date: str) -> dict | None:
    path = cache_key(source, name, date)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_cache(source: str, name: str, date: str, data: dict) -> None:
    _atomic_write_json(cache_key(source, name, date), data)


def load_checkpoint(source: str, date: str) -> list[str]:
    """Load the list of already-processed names (for resume after interruption).

    Review that finding: checkpoints used to live inside the cache partition
    `CACHE_DIR/<source>/<date>/_checkpoint.json`, where `cache.clear()` (which
    rmtree'd `CACHE_DIR/<source>`) deleted the resume state of a long batch
    run. They now live in a dedicated `CACHE_DIR/_checkpoints/…` tree that
    cache.clear() recognises as foreign to the entry namespace and preserves;
    the legacy location is still read so an in-flight run is not orphaned.
    """
    path = checkpoint_path(source, date)
    legacy = CACHE_DIR / source / date / "_checkpoint.json"
    for candidate in (path, legacy):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
    return []


def checkpoint_path(source: str, date: str) -> Path:
    """Protected location for batch resume state (never a cache entry)."""
    return CACHE_DIR / CHECKPOINT_ROOT / f"{source}.{date}.json"


def save_checkpoint(source: str, date: str, names: list[str]) -> None:
    path = checkpoint_path(source, date)
    _atomic_write_json(path, names)


# ---- LPSN rebuild -----------------------------------------------------------

def rebuild_lpsn(snapshot_date: str, output_dir: Path, limit: int | None = None) -> None:
    """Rebuild LPSN-derived corpus slice via the official API.

    M0 gate: endpoint/field mapping must be recorded from official docs.
    """
    print(f"[LPSN] Rebuilding corpus slice (snapshot_date={snapshot_date})")

    try:
        import lpsn  # official DSMZ client (PyPI: lpsn)
    except ImportError:
        print("[LPSN] ERROR: official client not installed: pip install lpsn")
        print("[LPSN] Register for free: https://api.lpsn.dsmz.de/")
        return

    user, password = _lpsn_credentials()
    if not (user and password):
        print("[LPSN] ERROR: no credentials (keyring or PROKNAME_LPSN_USER/PASSWORD)")
        return

    limiter = RateLimiter(max_rps=1.0)
    checkpoint = load_checkpoint("lpsn", snapshot_date)

    try:
        client = lpsn.LpsnClient(user, password)
    except Exception as exc:
        print(f"[LPSN] ERROR: client init failed: {exc!r}")
        return

    # Query for all genera (the M0 endpoint record determines exact parameters)
    names_to_query: list[str] = []  # populated from a seeds file or user input
    if not names_to_query:
        print("[LPSN] No query list provided; using benchmark A/B set genera as seeds")
        from prokname.benchmark.sets import load_a_set, load_b1_set, load_b2_set
        genera = set()
        for c in load_a_set():
            genera.add(c.genus)
        for c in load_b1_set():
            genera.add(c.genus)
        for c in load_b2_set():
            genera.add(c.genus)
        names_to_query = sorted(genera)

    if limit:
        names_to_query = names_to_query[:limit]

    results = []
    failures: list[str] = []
    for i, name in enumerate(names_to_query):
        if name in checkpoint:
            continue

        limiter.wait()
        cached = load_cache("lpsn", name, snapshot_date)
        if cached:
            results.append(cached)
            continue

        try:
            # Official-client sequence (see src/prokname/dedup/lpsn.py): a
            # search() is MANDATORY before retrieve() — retrieve() reads the
            # search state stored on the client. `match_mode="exact"` asks
            # the server for exact matching; retrieve() yields dicts.
            count = client.search(taxon_name=name, match_mode="exact")
            state = getattr(client, "result", None)
            if count == 0:
                if not (isinstance(state, dict) and state.get("count") == 0):
                    raise RuntimeError(
                        f"LPSN rejected the query for {name!r} "
                        f"(client said: {state!r})"
                    )
                matches: list = []
            else:
                matches = list(client.retrieve())
            record = {
                "query": name,
                "snapshot_date": snapshot_date,
                "matches": [
                    {
                        "full_name": m.get("full_name", ""),
                        "lpsn_taxonomic_status": m.get("lpsn_taxonomic_status", ""),
                        "lpsn_id": m.get("id", ""),
                    }
                    for m in matches if isinstance(m, dict)
                ],
            }
            save_cache("lpsn", name, snapshot_date, record)
            results.append(record)
            checkpoint.append(name)
            if (i + 1) % 10 == 0:
                save_checkpoint("lpsn", snapshot_date, checkpoint)
                print(f"[LPSN] Progress: {i+1}/{len(names_to_query)}")
        except Exception as exc:
            failures.append(name)
            print(f"[LPSN] ERROR: query failed for {name!r}: {exc!r}")
            time.sleep(2)

    save_checkpoint("lpsn", snapshot_date, checkpoint)

    # Honest-failure gate: a silent empty corpus would make every later
    # "no near match" conclusion meaningless (false negative). Never write
    # one — and never pretend partial failure was success.
    if failures or not results:
        print(
            f"[LPSN] FATAL: {len(failures)} query/queries failed "
            f"({len(results)} usable record(s)); refusing to write an "
            "empty/partial corpus"
        )
        raise SystemExit(1)

    output_path = output_dir / f"lpsn_export_{snapshot_date}.json"
    _atomic_write_json(
        output_path,
        {"snapshot_date": snapshot_date, "source": "LPSN", "license": "CC BY-SA 4.0",
         "count": len(results), "records": results},
    )
    print(f"[LPSN] Written {len(results)} records to {output_path}")


def _lpsn_credentials() -> tuple[str | None, str | None]:
    user = os.environ.get("PROKNAME_LPSN_USER")
    password = os.environ.get("PROKNAME_LPSN_PASSWORD")
    if not (user and password):
        try:
            import keyring
            user = user or "prokname-lpsn"
            password = keyring.get_password("prokname", user)
        except Exception:
            pass
    return user, password


# ---- SeqCode rebuild --------------------------------------------------------

# Same discipline as dedup/seqcode.py: an endpoint that M0 has not verified
# from the official API docs (https://registry.seqco.de/page/api) must never
# be called — inventing URLs and hoping they work is explicitly forbidden.
_SEQCODE_ENDPOINT_VERIFIED = False
_SEQCODE_EXPORT_URL = "https://registry.seqco.de/api/v1/names"  # M0: verify

# Request-boundary allowlist: only this exact https host may be contacted,
# and redirects are only followed while they stay inside it. A bulk-download
# script must never be talked into fetching internal/metadata endpoints.
_ALLOWED_EXPORT_HOSTS = {"registry.seqco.de"}


def _url_within_allowlist(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in _ALLOWED_EXPORT_HOSTS


def rebuild_seqcode(output_dir: Path) -> None:
    """Rebuild SeqCode Registry corpus slice via the official REST API.

    M0 gate: REST endpoint paths must be recorded from https://registry.seqco.de/page/api
    """
    print("[SeqCode] Rebuilding corpus slice")

    if not _SEQCODE_ENDPOINT_VERIFIED:
        print(
            "[SeqCode] SKIPPED: REST endpoint not yet verified — M0 must record "
            f"paths/schema from the official API docs ({_SEQCODE_EXPORT_URL} is "
            "an unverified candidate constant, never called as-is)"
        )
        return

    limiter = RateLimiter(max_rps=2.0)

    try:
        import httpx
    except ImportError:
        print("[SeqCode] ERROR: httpx not installed: pip install httpx")
        return

    if not _url_within_allowlist(_SEQCODE_EXPORT_URL):
        print(
            "[SeqCode] ERROR: endpoint URL outside the https allowlist "
            f"{sorted(_ALLOWED_EXPORT_HOSTS)} — refusing to request it"
        )
        return

    try:
        limiter.wait()
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            url = _SEQCODE_EXPORT_URL
            for _hop in range(3):  # bounded manual redirect loop, allowlisted
                resp = client.get(url)
                if resp.is_redirect:
                    location = resp.headers.get("location", "")
                    if not _url_within_allowlist(location):
                        print(
                            f"[SeqCode] WARNING: redirect to non-allowlisted "
                            f"host refused: {location or '<none>'!r}"
                        )
                        return
                    url = location
                    continue
                break
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[SeqCode] WARNING: bulk export failed: {exc!r}")
        print("[SeqCode] M0 gate: verify the REST endpoint path from official docs")
        return

    records = data if isinstance(data, list) else data.get("names", data.get("data", []))
    output_path = output_dir / f"seqcode_export_{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
    _atomic_write_json(
        output_path,
        {"snapshot_date": datetime.now(UTC).strftime("%Y-%m-%d"),
         "source": "SeqCode Registry", "license": "CC-BY 4.0",
         "count": len(records), "records": records},
    )
    print(f"[SeqCode] Written {len(records)} records to {output_path}")


# ---- NCBI taxdump rebuild ---------------------------------------------------

# names.dmp fields are separated by '\t|\t' and each line ends with '\t|'
# (same format as dedup/ncbi.py). Splitting on plain '\t' silently puts the
# unique-name column into the name_class slot and drops every record.
TAXDUMP_URL = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz"


def parse_names_dmp(text: str) -> list[dict]:
    """Parse names.dmp content into {'taxid', 'name', 'class'} records."""
    records = []
    for line in text.splitlines():
        parts = line.rstrip("\t|").split("\t|\t")
        if len(parts) >= 4:
            records.append({
                "taxid": parts[0].strip(),
                "name": parts[1].strip(),
                "class": parts[3].strip(),
            })
    return records


def rebuild_taxdump(output_dir: Path) -> None:
    """Download and parse NCBI taxdump for local corpus + near-match scan."""
    print("[NCBI] Rebuilding taxdump slice")
    import socket
    import tarfile
    import urllib.request

    local_path = output_dir / "taxdump.tar.gz"
    extract_dir = output_dir / "taxdump"

    print(f"[NCBI] Downloading {TAXDUMP_URL} ...")
    # urlretrieve has no timeout parameter; bound it globally so a hung
    # connection cannot stall the rebuild forever.
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(120)
    try:
        urllib.request.urlretrieve(TAXDUMP_URL, str(local_path))
    except Exception as exc:
        print(f"[NCBI] ERROR: download failed: {exc!r}")
        return
    finally:
        socket.setdefaulttimeout(old_timeout)

    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(local_path, "r:gz") as tar:
        try:
            # 'data' filter (3.12+, backported to 3.11.4): blocks path
            # traversal and absolute paths from the archive
            tar.extractall(path=extract_dir, filter="data")
        except TypeError:  # pragma: no cover - pre-3.11.4 runtimes
            # The unfiltered fallback must NOT silently drop the traversal
            # protection: reject absolute paths and '..' members before
            # extracting anything.
            for member in tar.getmembers():
                member_path = (extract_dir / member.name).resolve()
                if member.islnk() or member.issym() or not str(member_path).startswith(
                    str(extract_dir.resolve()) + os.sep
                ):
                    raise RuntimeError(
                        f"refusing to extract unsafe archive member: {member.name!r}"
                    ) from None
            tar.extractall(path=extract_dir)

    # Parse names.dmp to extract all prokaryotic names
    names_path = extract_dir / "names.dmp"
    if not names_path.exists():
        print("[NCBI] ERROR: names.dmp not found after extraction")
        return

    names = [
        rec for rec in parse_names_dmp(names_path.read_text(encoding="utf-8"))
        if rec["class"] == "scientific name"
    ]
    if not names:
        print("[NCBI] ERROR: names.dmp parsed to zero scientific names — "
              "refusing to write an empty corpus (parser/data drift?)")
        return

    snapshot_date = datetime.now(UTC).strftime("%Y-%m-%d")
    kept = names[:10000]
    output_path = output_dir / f"ncbi_taxdump_{snapshot_date}.json"
    _atomic_write_json(
        output_path,
        {"snapshot_date": snapshot_date,
         "source": "NCBI taxdump", "count": len(kept),
         "total_scientific_names": len(names),
         "truncated": len(kept) < len(names),
         "records": kept},
    )
    # Clean up raw archive
    local_path.unlink(missing_ok=True)
    print(f"[NCBI] Written {len(kept)} of {len(names)} scientific names "
          f"(first 10k saved) to {output_path}")


# ---- Main -------------------------------------------------------------------

def main() -> None:
    # Console code pages are not a safe assumption for redirected output;
    # see prokname.diagnostics.ensure_reportable_output.
    from prokname.diagnostics import ensure_reportable_output
    ensure_reportable_output()
    parser = argparse.ArgumentParser(
        description="Rebuild benchmark corpus from official data sources "
                    ""
    )
    parser.add_argument("--source", choices=["lpsn", "seqcode", "taxdump", "all"], default="all")
    parser.add_argument("--snapshot-date", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument("--output-dir", default="src/prokname/data/")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of queries (testing)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.source in ("lpsn", "all"):
        rebuild_lpsn(args.snapshot_date, output_dir, limit=args.limit)
    if args.source in ("seqcode", "all"):
        rebuild_seqcode(output_dir)
    if args.source in ("taxdump", "all"):
        rebuild_taxdump(output_dir)

    print("\nDone. Review the M0 endpoint records before using for paper-grade benchmarks.")


if __name__ == "__main__":
    main()
