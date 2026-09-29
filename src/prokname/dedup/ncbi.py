"""Reference-source adapter: NCBI Taxonomy.

NCBI is explicitly NOT a naming authority — it only answers "is this name in
use in the public sequence database taxonomy?" for cross-reference purposes.

Two paths:
- Online: via GNA GNverifier (which aggregates NCBI among hundreds of sources)
- Offline: via the built-in taxdump parser (this module), which parses the
  NCBI taxdump.tar.gz names.dmp / nodes.dmp files locally, zero external
  dependencies. The taxdump is downloaded separately by the user.

The taxdump parser is also used to build the near-match (parahomonym) corpus.

`name_class` semantics:
- ``authority`` is the nomenclatural AUTHOR CITATION of a taxon (e.g.
  "Woese 1987"), NOT a name that is used as a synonym. The old code mapped it
  to `found_synonym`, which misread the source data.
- ``misspelling`` IS the parahomonym signal this tool cares about (a
  misspelt name is precisely the "one letter apart" case), so it gets its own
  `found_parahomonym` status instead of falling through to `found_reference`.
- A name commonly appears on SEVERAL rows with different `name_class` values.
  The old code returned on the FIRST line, so the answer depended on file
  order. Every matching row is now collected and merged by the priority order
  below (highest first).
- Provenance, honestly stated: the NCBI taxdump field specification is NOT
  part of the vendored reference snapshots (the taxonkit mirror parses
  names.dmp but documents no name_class vocabulary; grepping the mirror for
  "genbank common name" / "misspelling" returns no taxdump documentation).
  The value list below therefore comes from the the LPSN status label vocabulary
  NCBI taxdump README (https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump_readme.txt),
  and every row carries ``verified: false`` until an M0 record captures the
  README alongside a real names.dmp sample. Unverified rows can only ever
  produce reference-tier warnings — never a conflict — so an unverified label
  cannot flip a verdict; unknown labels degrade to `found_other`.

Indexing (performance half):
- names.dmp has ~2.7M rows. Building a `{lowercased name: [(name_class,
  taxid), ...]}` index ONCE and keeping it in process, with a pickled sidecar
  in the prokname cache directory keyed by the file's size+mtime signature,
  replaces the previous "rescan the whole file for every query".
- Chosen format: pickle sidecar (no third-party dependency; the project
  ships no pyarrow/pandas in the runtime requirements). First call parses
  and writes it; later calls in the same process hit the memo, later
  processes hit the sidecar. Set PROKNAME_TAXDUMP_INDEX_DIR to relocate it
  and PROKNAME_TAXDUMP_INDEX=0 to disable persistence.
"""

from __future__ import annotations

import gzip
import os
import pickle
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .cache import cache_dir
from .model import (
    FOUND_OTHER,
    FOUND_PARAHOMONYM,
    FOUND_REFERENCE,
    FOUND_SYNONYM,
    NOT_FOUND,
    UNAVAILABLE,
    SourceResult,
)

NCBI_URL = "https://www.ncbi.nlm.nih.gov/taxonomy"
_TIER = "reference"

#: name_class -> (priority, category, status, note). Lower priority wins.
#: Order is the mandated one:
#:   scientific name > authority > synonym > misspelling >
#:   genbank common name > blast name > includes/anamorph/teleomorph
@dataclass(frozen=True)
class NameClassSemantics:
    name_class: str
    priority: int
    status: str
    occupies_name: bool
    verified: bool
    note: str
    provenance: str


_PROVENANCE = (
    "NCBI taxdump_readme.txt (not vendored, not re-read here) — pending M0 "
    "record; see "
    "docs/provenance/upstream-references.md"
    "#l5--ncbi-taxdump-name_class-vocabulary; value list cross-checked "
    "against the LPSN status label vocabulary"
)

NAME_CLASS_SEMANTICS: dict[str, NameClassSemantics] = {
    spec.name_class: spec
    for spec in [
        NameClassSemantics(
            "scientific name", 1, FOUND_REFERENCE, True, False,
            "the name NCBI uses for the taxon — a genuine usage record",
            _PROVENANCE,
        ),
        NameClassSemantics(
            "authority", 2, FOUND_OTHER, False, False,
            "author citation (e.g. 'Woese 1987'), NOT a synonym and not a "
            "name in use — an earlier reader misread this as found_synonym",
            _PROVENANCE,
        ),
        NameClassSemantics(
            "synonym", 3, FOUND_SYNONYM, True, False,
            "the string is used as a synonym of another taxon",
            _PROVENANCE,
        ),
        NameClassSemantics(
            "misspelling", 4, FOUND_PARAHOMONYM, False, False,
            "orthographic variant flagged by NCBI — the parahomonym signal "
            "this check exists to surface",
            _PROVENANCE,
        ),
        NameClassSemantics(
            "genbank common name", 5, FOUND_REFERENCE, True, False,
            "vernacular usage in GenBank, not a scientific name",
            _PROVENANCE,
        ),
        NameClassSemantics(
            "blast name", 6, FOUND_REFERENCE, True, False,
            "BLAST-facing vernacular, not a scientific name",
            _PROVENANCE,
        ),
        NameClassSemantics(
            "includes", 7, FOUND_OTHER, False, False,
            "grouping label, not a name record for this taxon",
            _PROVENANCE,
        ),
        NameClassSemantics(
            "anamorph", 8, FOUND_OTHER, False, False,
            "asexual-stage name recorded for the taxon — informational",
            _PROVENANCE,
        ),
        NameClassSemantics(
            "teleomorph", 9, FOUND_OTHER, False, False,
            "sexual-stage name recorded for the taxon — informational",
            _PROVENANCE,
        ),
    ]
}

#: Unknown name_class values: never guess their semantics.
_UNVERIFIED_SPEC = NameClassSemantics(
    "<unknown>", 99, FOUND_OTHER, False, False,
    "unrecognised name_class — reported as a bare record, no interpretation",
    "no source (value absent from the table above)",
)


def _norm_class(raw: str) -> str:
    return " ".join(str(raw or "").split()).casefold().rstrip(".").strip()


def semantics_for(name_class: str) -> NameClassSemantics:
    """Look up the semantics of one name_class value (exact match only)."""
    key = _norm_class(name_class)
    for spec_class, spec in NAME_CLASS_SEMANTICS.items():
        if _norm_class(spec_class) == key:
            return spec
    return _UNVERIFIED_SPEC


def merge_matches(rows: Iterable[tuple[str, str]]) -> tuple[SourceResult | None, list[str]]:
    """Merge every (name_class, taxid) row found for one exact name.

    Returns (result_or_None_when_no_rows, human-readable class summary).
    The winner is the highest-priority (lowest number) name_class; ties keep
    the first-seen taxid so the answer is reproducible.
    """
    rows = list(rows)
    if not rows:
        return None, []
    specs = [(semantics_for(cls), cls, taxid) for cls, taxid in rows]
    specs.sort(key=lambda item: (item[0].priority,))
    best_spec, best_class, best_taxid = specs[0]
    seen: list[str] = []
    for _spec, cls, _taxid in specs:
        if cls not in seen:
            seen.append(cls)
    return (
        SourceResult(
            name="NCBI",
            status=best_spec.status,
            tier=_TIER,
            detail=(
                f"taxid={best_taxid}, name_class={best_class!r} "
                f"(of {len(rows)} row(s): {', '.join(seen)}; "
                f"merged by name_class priority, not file order) — "
                f"{best_spec.note}"
            ),
            url=NCBI_URL,
            verified=best_spec.verified,
        ),
        seen,
    )


def check(name: str, *, allow_network: bool = False,
          taxdump_dir: str | None = None) -> SourceResult:
    """Query NCBI taxonomy for a name.

    Offline (default): uses the built-in taxdump parser if a taxdump directory
    is available (resolved in order: explicit argument > PROKNAME_TAXDUMP_DIR
    env var > persisted `prokname config --taxdump-dir` setting).

    Online: delegates to the GNA adapter (which aggregates NCBI among other
    sources) — we do NOT build a direct NCBI E-utilities adapter.
    """
    # Try offline taxdump first: explicit argument > env var > persisted config
    dump_dir = taxdump_dir or os.environ.get("PROKNAME_TAXDUMP_DIR")
    if not dump_dir:
        from ..config import resolved_setting

        dump_dir = resolved_setting("taxdump_dir", "PROKNAME_TAXDUMP_DIR")
    if dump_dir:
        result = _check_taxdump(name, dump_dir)
        if result is not None:
            return result
        # Configured but no names.dmp found — say so, don't claim "not configured"
        detail = (
            f"taxdump directory configured but names.dmp not found in "
            f"{dump_dir!r}; expected names.dmp or names.dmp.gz"
        )
    else:
        detail = (
            "offline mode; no taxdump configured "
            "(set PROKNAME_TAXDUMP_DIR or use --taxdump-dir)"
        )

    if not allow_network:
        return SourceResult(
            name="NCBI", status=UNAVAILABLE, tier=_TIER,
            detail=detail + "; reference source — non-blocking",
            url=NCBI_URL,
        )

    # Online: delegate to GNA (which includes NCBI)
    try:
        from .gna import check as gna_check
        gna_result = gna_check(name, allow_network=True)
        return SourceResult(
            name="NCBI", status=gna_result.status, tier=_TIER,
            detail=f"via GNA GNverifier: {gna_result.detail}",
            url=NCBI_URL,
            online_derived=True,
            verified=gna_result.verified,
        )
    except Exception as exc:
        return SourceResult(
            name="NCBI", status=UNAVAILABLE, tier=_TIER,
            detail=f"NCBI online query failed: {exc!r} (reference source — non-blocking)",
            url=NCBI_URL,
            online_derived=True,
        )


def _open_names_dmp(dump_dir: str) -> tuple[Path, Callable[..., IO]] | None:
    """Locate names.dmp (plain or gzipped) and its opener; None if absent."""
    base = Path(dump_dir)
    plain = base / "names.dmp"
    if plain.exists():
        return plain, open
    zipped = base / "names.dmp.gz"
    if zipped.exists():
        return zipped, lambda p: gzip.open(p, "rt", encoding="utf-8")
    return None


def _iter_names_dmp(path: Path, opener: Callable[..., IO]):
    """Yield (taxid, name_text, name_class) triples from a names.dmp file.

    names.dmp fields are separated by '\\t|\\t' and each line ends with '\\t|'.
    """
    with opener(path) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t|\t")
            if len(parts) >= 4:
                yield (
                    parts[0].strip(),
                    parts[1].strip(),
                    parts[3].rstrip("\t|").strip(),
                )


# ---------------------------------------------------------------------------
# Reusable name index (performance half)
# ---------------------------------------------------------------------------

#: in-process memo: signature -> index. Bounded to a handful of taxdump dirs.
_INDEX_MEMO: dict[tuple, dict[str, list[tuple[str, str]]]] = {}
_INDEX_MEMO_MAX = 2


def _index_signature(path: Path) -> tuple:
    stat = path.stat()
    return (str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns))


def _index_sidecar_path(signature: tuple) -> Path | None:
    """Where the pickled index lives (may be disabled; None ⇒ in-RAM only)."""
    if os.environ.get("PROKNAME_TAXDUMP_INDEX", "1") == "0":
        return None

    root = Path(os.environ.get("PROKNAME_TAXDUMP_INDEX_DIR") or
                (cache_dir() / "ncbi_index"))
    digest = re.sub(r"[^0-9A-Za-z_.-]", "_", signature[0]).rsplit("/", 1)[-1]
    import hashlib

    tag = hashlib.sha256(f"{signature}".encode()).hexdigest()[:12]
    return root / f"names_index.{digest}.{tag}.pkl"


def _build_index(path: Path, opener: Callable[..., IO]) -> dict[str, list[tuple[str, str]]]:
    """Single pass over names.dmp → {lowercased name: [(class, taxid), ...]}."""
    index: dict[str, list[tuple[str, str]]] = {}
    for taxid, name_txt, name_class in _iter_names_dmp(path, opener):
        index.setdefault(name_txt.casefold(), []).append((name_class, taxid))
    return index


def _load_index(path: Path, opener: Callable[..., IO]) -> dict[str, list[tuple[str, str]]]:
    signature = _index_signature(path)
    memo = _INDEX_MEMO.get(signature)
    if memo is not None:
        return memo

    sidecar = _index_sidecar_path(signature)
    if sidecar is not None and sidecar.exists():
        try:
            with sidecar.open("rb") as fh:
                index = pickle.load(fh)
            if not isinstance(index, dict):
                raise TypeError("index sidecar is not a dict")
        except Exception:
            index = _build_index(path, opener)  # corrupt/outdated sidecar
            try:
                _write_sidecar(sidecar, index)
            except OSError:
                pass
    elif sidecar is not None:
        index = _build_index(path, opener)
        try:
            _write_sidecar(sidecar, index)
        except OSError:
            pass  # read-only cache dir: the in-process memo still helps
    else:
        index = _build_index(path, opener)

    while len(_INDEX_MEMO) >= _INDEX_MEMO_MAX:
        _INDEX_MEMO.pop(next(iter(_INDEX_MEMO)))
    _INDEX_MEMO[signature] = index
    return index


def _write_sidecar(sidecar: Path, index: dict) -> None:
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
    with tmp.open("wb") as fh:
        pickle.dump(index, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, sidecar)


def clear_index_memo() -> int:
    """Drop the in-process taxdump index memo; returns how many were dropped."""
    count = len(_INDEX_MEMO)
    _INDEX_MEMO.clear()
    return count


def reload_index_memo() -> int:
    """Alias for clear_index_memo(), named for the reload path.

    Deliberately NOT registered with engine.data.on_invalidate(): this memo
    indexes the user's own taxdump files, which are not a shipped data asset, so
    a rules.json reload has nothing to do with it. Call it after replacing the
    taxdump on disk (prokname config --taxdump-dir).
    """
    return clear_index_memo()


def _check_taxdump(name: str, dump_dir: str) -> SourceResult | None:
    """Check a name against local NCBI taxdump files.

    Returns None when there is no names.dmp to consult; otherwise an exact
    answer built from EVERY row for the name (never the first line seen).
    """
    located = _open_names_dmp(dump_dir)
    if located is None:
        return None
    path, opener = located
    name_key = " ".join(name.split()).casefold()
    try:
        rows = _load_index(path, opener).get(name_key, [])
    except (OSError, EOFError, UnicodeDecodeError, pickle.UnpicklingError) as exc:
        return SourceResult(
            name="NCBI", status=UNAVAILABLE, tier=_TIER,
            detail=f"taxdump parse error: {exc!r}",
            url=NCBI_URL,
        )
    merged, _seen = merge_matches(rows)
    if merged is None:
        return SourceResult(
            name="NCBI", status=NOT_FOUND, tier=_TIER,
            detail="no NCBI taxdump match", url=NCBI_URL,
        )
    return merged


def load_taxdump_names(dump_dir: str) -> list[dict]:
    """Load all scientific / synonym names from NCBI taxdump for corpus building.

    Used by the near-match (parahomonym) scan to build the NCBI portion of
    the three-source corpus. Corpus building is a full pass
    by nature (it has to see every name exactly once); per-query lookups use
    the reusable index in `_check_taxdump` instead.

    Parse/read errors propagate to the caller: a silently truncated corpus
    would understate the coverage boundary, which the plan forbids.
    """
    located = _open_names_dmp(dump_dir)
    if located is None:
        raise FileNotFoundError(
            f"no names.dmp or names.dmp.gz found in {dump_dir!r}"
        )
    path, opener = located
    result = []
    for _taxid, name_txt, name_class in _iter_names_dmp(path, opener):
        if _norm_class(name_class) in ("scientific name", "synonym"):
            result.append({
                "name": name_txt,
                "source": "NCBI taxdump",
                "class": name_class,
            })
    return result
