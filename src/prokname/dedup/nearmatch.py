"""Local near-match (parahomonym) scan.

Tier two of dedup: exact matching is delegated to the authority APIs; this
component scans a LOCAL corpus for names within a small edit distance, to
catch 'one connecting vowel apart' confusables that exact matching misses.
The corpus composition and its cut-off date are always reported alongside the
hits — the coverage boundary is stated honestly, never implied.

Comparison domains
-------------------------------------
Under the codes, homonymy is judged per rank: a specific epithet competes
only inside its genus. Stemming the GENERIC name into the epithet domain
(the old `stem_name` stemmed every token, so 'Bacillus' → 'bacill' and
'coli' → 'col') mixes two comparison domains and can both hide and invent
hits. Now:

- the leading (generic) token is kept verbatim in every stem view;
- only the following (epithet and beyond) tokens are stemmed;
- this mirrors GNmatcher, which "matches names using stemmed canonical forms
  where suffixes of SPECIFIC EPITHETS are removed"
  — gnames/gnverifier, fuzzy-matching.md:11-39, re-read 2026-09-25:
  docs/provenance/upstream-references.md#l2--gnmatcher-stemmed-canonical-calibre.

Long entries are not excluded any more
----------------------------------------------------------------------
Length bucketing used to make 'Bacillus subtilis subsp. spizizenii' and
'Candidatus Pelagibacter ubique' unreachable from a binomial query: their
normalized lengths sit far outside the ±max_distance band. Each corpus entry
now additionally exposes a *core* view — the leading binomial, with rank
markers ('subsp.', 'var.', 'pv.', …) and the 'Candidatus' prefix stripped —
and the query is compared against both views. A corpus entry whose core
equals the query's core is reported with distance 0 and caliber 'core…',
which is the honest statement "the binomial part of this corpus name already
is your name", instead of silence.

Cache discipline
-----------------------------------
The index cache used to be keyed by `id(corpus_names)` (a memory address): a
freed list's address can be recycled by a DIFFERENT corpus, and the cache
never evicted. It is now keyed by a process-local content digest of the
corpus (entry count + commutative mix of every (name, source) hash), bounded
by an LRU of `INDEX_CACHE_MAX` entries, with `clear_index_cache()` and
`index_cache_info()` for explicit invalidation.
"""

from __future__ import annotations

import hashlib
import itertools
from collections import OrderedDict
from datetime import date, datetime

from ..engine import data
from ..engine.orthography import latinize
from .model import NearMatch

# ---------------------------------------------------------------------------
# Edit distance
# ---------------------------------------------------------------------------


def levenshtein(a: str, b: str) -> int:
    """Classic edit distance (small strings; O(len_a * len_b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,        # deletion
                    current[j - 1] + 1,     # insertion
                    previous[j - 1] + (ca != cb),  # substitution
                )
            )
        previous = current
    return previous[-1]


def bounded_levenshtein(a: str, b: str, max_distance: int) -> int:
    """Edit distance capped at `max_distance`, with early termination.

    Returns the true distance when it is <= max_distance, otherwise
    max_distance + 1. The banded DP only fills the diagonal band where the
    distance can still be within the cap, so large-corpus scans avoid the
    full O(len_a * len_b) table for obviously distant pairs.
    """
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        band_lo = max(1, i - max_distance)
        band_hi = min(len(b), i + max_distance)
        current = [i] + [max_distance + 1] * len(b)
        for j in range(band_lo, band_hi + 1):
            current[j] = min(
                previous[j] + 1,                        # deletion
                current[j - 1] + 1,                     # insertion
                previous[j - 1] + (ca != b[j - 1]),     # substitution
            )
        if current[band_lo:band_hi + 1] and min(current[band_lo:band_hi + 1]) > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1] if previous[-1] <= max_distance else max_distance + 1


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """Normalize a scientific name for comparison (latinize, fold spaces)."""
    parts = [latinize(p) for p in name.split()]
    return " ".join(p for p in parts if p)


#: Rank / status markers that carry no name content. A corpus entry such as
#: 'Bacillus subtilis subsp. spizizenii' is reachable from the binomial
#: 'Bacillus subtilis' once these are dropped from the core view
#:. 'Candidatus' is the provisional candidate-status
#: prefix (LPSN/NCBI usage; the seed corpus carries
#: 'Candidatus Pelagibacter ubique') and is likewise not part of the name.
RANK_MARKERS = frozenset({
    "subsp.", "subsp", "subspecies", "var.", "var", "variety", "formae", "f.",
    "forma", "biovar.", "biovar", "pathovar.", "pv.", "serovar.", "str.",
    "strain", "sect.", "section", "subsect.", "ser.", "series", "prosp.",
    "aff.", "cf.", "candidatus", "ca.", "gen.", "genus", "sp.", "spp.",
})


def core_name(normalized: str) -> str:
    """Leading binomial of a normalized name, markers/prefixes dropped.

    'bacillus subtilis subsp. spizizenii' → 'bacillus subtilis'
    'candidatus pelagibacter ubique'      → 'pelagibacter ubique'
    Two-token (and shorter) names are returned unchanged, so the core view
    never loses information the full view did not have.
    """
    tokens = [t for t in normalized.split() if t.lower() not in RANK_MARKERS]
    if len(tokens) <= 2:
        return " ".join(tokens) if tokens else normalized
    return " ".join(tokens[:2])


# ---------------------------------------------------------------------------
# Latin stemmer (Taxamatch / GNmatcher-inspired, epithets only)
# ---------------------------------------------------------------------------

# Suffix inventory derived from the shipped rule assets (rules.json
# adjective paradigms + person_genitive endings): the stemmer strips ONE
# inflectional ending, longest match first. Comparisons stay consistent
# because both sides of a pair are stemmed with the same table.
_STEM_SUFFIXES: tuple[str, ...] = tuple(sorted(
    (
        # place adjectives: -ensis/-ense
        "ensis", "ense",
        # second declension: -us/-a/-um
        "us", "a", "um",
        # third declension: -is/-e
        "is", "e",
        # loving / nourishing paradigms end in -us/-a/-um after the base
        # (troph-icus/-ica/-icum), already covered above
        # person genitives: -ii/-iae (and documented -i variant)
        "iae", "ii", "i",
    ),
    key=len,
    reverse=True,
))

_MIN_STEM_LEN = 3  # never stem down to a degenerate 1–2 character stem


def stem_latin(word: str) -> str:
    """Strip one Latin inflectional ending from a single name word.

    Returns the word unchanged when no known ending matches or when the
    remainder would be shorter than _MIN_STEM_LEN. Deliberately crude and
    deterministic — an auditable approximation of Taxamatch-style stemming,
    not a full Latin morphological analyser.
    """
    lowered = word.lower()
    for suffix in _STEM_SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) - len(suffix) >= _MIN_STEM_LEN:
            return lowered[: len(lowered) - len(suffix)]
    return lowered


def stem_name(name: str) -> str:
    """Stem the EPITHET tokens of a normalized name; keep the generic one.

    Review that finding: the generic name and the specific epithet are not the
    same comparison domain (homonymy is judged within a genus), so stemming
    'Escherichia' → 'escherichi' alongside 'coli' → 'col' either hides or
    invents hits. GNmatcher does the same thing: "suffixes of specific
    epithets are removed" (gnverifier/fuzzy-matching.md:13-15). The genus
    token is lower-cased only.
    """
    tokens = name.split()
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0].casefold()
    return " ".join([tokens[0].casefold()] + [stem_latin(t) for t in tokens[1:]])


def stem_core(name: str) -> str:
    """Stem view of the *core* (leading binomial) of a normalized name."""
    return stem_name(core_name(name))


# ---------------------------------------------------------------------------
# Corpus indexing
# ---------------------------------------------------------------------------


class _IndexedCorpus:
    """Precomputed normalized / stemmed / core views of a corpus.

    Building the index once per corpus (instead of normalizing every entry
    on every query) and restricting the DP to the length buckets within the
    threshold is what keeps large-corpus scans tractable.

    Record layout (positional for speed):
      0 display, 1 source, 2 norm, 3 stem, 4 core_norm, 5 core_stem,
      6 len(norm), 7 len(stem), 8 len(core_norm), 9 len(core_stem)
    """

    __slots__ = ("records", "by_norm_len", "by_stem_len", "by_core_len",
                 "by_core_stem_len")

    def __init__(self, corpus_names: list[dict]):
        records: list[tuple] = []
        for entry in corpus_names:
            display = entry["name"]
            norm = normalize_name(display)
            core = core_name(norm)
            stem = stem_name(norm)
            cstem = stem_name(core)
            records.append((
                display, entry.get("source", "unknown"), norm, stem, core,
                cstem, len(norm), len(stem), len(core), len(cstem),
            ))
        self.records = records
        self.by_norm_len: dict[int, list[tuple]] = {}
        self.by_stem_len: dict[int, list[tuple]] = {}
        self.by_core_len: dict[int, list[tuple]] = {}
        self.by_core_stem_len: dict[int, list[tuple]] = {}
        for rec in records:
            self.by_norm_len.setdefault(rec[6], []).append(rec)
            self.by_stem_len.setdefault(rec[7], []).append(rec)
            self.by_core_len.setdefault(rec[8], []).append(rec)
            self.by_core_stem_len.setdefault(rec[9], []).append(rec)

    @staticmethod
    def _band(buckets: dict[int, list[tuple]], length: int, max_distance: int):
        for length_ in range(max(0, length - max_distance), length + max_distance + 1):
            yield from buckets.get(length_, ())

    def norm_band(self, length: int, max_distance: int):
        return self._band(self.by_norm_len, length, max_distance)

    def stem_band(self, length: int, max_distance: int):
        return self._band(self.by_stem_len, length, max_distance)

    def core_band(self, length: int, max_distance: int):
        return self._band(self.by_core_len, length, max_distance)

    def core_stem_band(self, length: int, max_distance: int):
        return self._band(self.by_core_stem_len, length, max_distance)


# --- content-addressed, bounded index cache -------------

#: How many corpus indexes are kept at once. Each index over the full taxdump
#: (~2.7M names) is large, so the bound is deliberately small; the previous
#: `id()`-keyed dict grew without limit.
INDEX_CACHE_MAX = 4

#: LRU: least-recently-used index is evicted when the bound is exceeded.
_INDEX_CACHE: OrderedDict[str, _IndexedCorpus] = OrderedDict()

_MASK = 0xFFFF_FFFF_FFFF_FFFF


def corpus_index_key(corpus_names: list[dict]) -> str:
    """Content-addressed key for a corpus (stable within this process).

    Replaces `id(corpus_names)`: a memory address can be recycled by a
    different corpus after garbage collection, which let one corpus be
    answered with another corpus's index. The digest mixes the entry count,
    the total name length, and a commutative (XOR + wrapping sum) mix of the
    per-entry hashes, so two corpora of the SAME length but different
    content get different keys while the same content in a freshly built
    list reuses the index. Permutation of entries does not change the key.
    """
    xor = 0
    total = 0
    nchar = 0
    for entry in corpus_names:
        name = str(entry.get("name", ""))
        source = str(entry.get("source", "unknown"))
        digest = hash((name, source)) & _MASK
        xor ^= digest
        total = (total + digest) & _MASK
        nchar += len(name)
    return f"{len(corpus_names)}|{nchar}|{xor:016x}|{total:016x}"


def index_cache_info() -> dict:
    """Introspection for tests and for `prokname` diagnostics."""
    return {
        "entries": len(_INDEX_CACHE),
        "max_entries": INDEX_CACHE_MAX,
        "keys": list(_INDEX_CACHE.keys()),
    }


def clear_index_cache() -> int:
    """Drop every cached corpus index; returns how many were dropped."""
    count = len(_INDEX_CACHE)
    _INDEX_CACHE.clear()
    return count


# The corpus index is derived from corpus_seed.json / genus_gender.json, so a
# data reload must drop it.
data.on_invalidate(clear_index_cache)


def _index_for(corpus_names: list[dict]) -> _IndexedCorpus:
    key = corpus_index_key(corpus_names)
    index = _INDEX_CACHE.get(key)
    if index is not None:
        _INDEX_CACHE.move_to_end(key)
        return index
    index = _IndexedCorpus(corpus_names)
    _INDEX_CACHE[key] = index
    while len(_INDEX_CACHE) > INDEX_CACHE_MAX:
        _INDEX_CACHE.popitem(last=False)
    return index


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------


def _scan_views(
    index: _IndexedCorpus,
    q_full: str,
    q_core: str,
    q_norm: str,
    max_distance: int,
    full_pos: int,
    core_pos: int,
    full_band: str,
    core_band: str,
    caliber: str,
) -> list[NearMatch]:
    """Shared candidate generation for the whole/stem calibers.

    Every candidate is compared in two views (the full name and the core
    binomial); the smaller distance wins.

    Distance 0 means "identical under this caliber". That is a hit in the
    stem caliber (suffix-only variants such as -ensis/-ense collapse to one
    stem — the documented purpose of `scan_stemmed`) and a hit in the core
    view of either caliber when a longer corpus entry merely extends the
    query's binomial ('Bacillus subtilis' vs 'Bacillus subtilis subsp.
    spizizenii'). The query's own exact spelling is always dropped.
    """
    hits: dict[str, NearMatch] = {}
    seen: set[int] = set()
    for rec in itertools.chain(
        getattr(index, full_band)(len(q_full), max_distance),
        getattr(index, core_band)(len(q_core), max_distance),
    ):
        marker = id(rec)
        if marker in seen:
            continue
        seen.add(marker)
        display, source, norm = rec[0], rec[1], rec[2]
        if norm == q_norm:
            continue  # the exact name itself is not a near match
        d_full = bounded_levenshtein(q_full, rec[full_pos], max_distance)
        d_core = bounded_levenshtein(q_core, rec[core_pos], max_distance)
        best: tuple[int, str] | None = None
        if d_full <= max_distance:
            best = (d_full, caliber)
        if d_core <= max_distance and (best is None or d_core < best[0]):
            best = (d_core, f"{caliber}-core")
        if best is None:
            continue
        distance, kind = best
        previous = hits.get(display)
        if previous is None or distance < previous.distance:
            hits[display] = NearMatch(
                corpus_name=display, distance=distance, source=source,
                caliber=kind,
            )
    return list(hits.values())


def scan(
    corpus_names: list[dict],
    query: str,
    max_distance: int = 2,
) -> list[NearMatch]:
    """Return corpus entries within `max_distance` edits of the query.

    Default 2 because the classic confusable pairs (-ensis/-ense,
    -trophicus/-tropha) sit at edit distance 2, not 1.

    Each corpus entry is compared in two views (full normalized name, and
    core binomial), so multi-word / 'Candidatus' entries stay reachable
.
    """
    index = _index_for(corpus_names)
    q = normalize_name(query)
    hits = _scan_views(index, q, core_name(q), q, max_distance, 2, 4,
                       "norm_band", "core_band", caliber="whole")
    hits.sort(key=lambda m: (m.distance, m.corpus_name))
    return hits


def scan_stemmed(
    corpus_names: list[dict],
    query: str,
    max_distance: int = 1,
) -> list[NearMatch]:
    """Return corpus entries whose STEMS sit within `max_distance` edits.

    Stem-equal pairs (distance 0, e.g. -ensis/-ense suffix-only variants)
    count as hits when the full normalized names differ — exact copies of
    the query itself are still excluded. Default threshold 1 follows the
    GNverifier/GNmatcher experience that stem-level distance > 1 mostly
    yields false positives (gnverifier/fuzzy-matching.md:25-30).

    Only epithet tokens are stemmed; the generic name is
    compared verbatim, because homonymy is judged within a genus.
    """
    index = _index_for(corpus_names)
    q_norm = normalize_name(query)
    q = stem_name(q_norm)
    q_core = stem_core(q_norm)
    hits = _scan_views(index, q, q_core, q_norm, max_distance, 3, 5,
                       "stem_band", "core_stem_band", caliber="stem")
    hits.sort(key=lambda m: (m.distance, m.corpus_name))
    return hits


# ---------------------------------------------------------------------------
# Corpus metadata helpers
# ---------------------------------------------------------------------------


def load_seed_corpus() -> tuple[list[dict], dict]:
    """Load the shipped demo seed corpus and its provenance metadata."""
    payload = data.corpus_seed()
    return payload["names"], payload["_meta"]


def corpus_is_stale(corpus_date: str | None, max_age_days: int = 180,
                    reference: date | None = None) -> bool:
    """Flag a corpus older than `max_age_days` relative to `reference`.

    Review that finding: a corpus whose `_meta.corpus_date` is present but null
    used to raise TypeError here (the caller's `except ValueError` could not
    catch it, so `check_name()` crashed). An absent, empty or unparseable
    date is treated as STALE — unknown freshness never reads as fresh.
    """
    if not isinstance(corpus_date, str) or not corpus_date.strip():
        return True
    try:
        parsed = datetime.strptime(corpus_date.strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return True
    ref = reference or date.today()
    return (ref - parsed).days > max_age_days


def index_digest(corpus_names: list[dict]) -> str:
    """Short, human-pinnable digest of a corpus (for reports/manifests)."""
    return hashlib.sha256(
        corpus_index_key(corpus_names).encode("utf-8")
    ).hexdigest()[:12]
