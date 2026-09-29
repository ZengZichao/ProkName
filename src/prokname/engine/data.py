"""Loading of the versioned, expert-reviewable data assets.

The JSON files under prokname/data are the single source of truth for rules
and lexicons (design principle "auditable"). Two
properties used to be promises in a docstring and nothing more; both are now
enforced, because a documented contract that "callers must treat them as read-only"
is not a contract while "callers cannot mutate them" is.

1. READ-ONLY. Every asset is returned wrapped in a mapping/sequence that
   refuses mutation (``_ReadOnlyDict`` / ``_ReadOnlyList``). They are real
   ``dict``/``list`` subclasses, so ``json.dumps``, ``.values()``, slicing and
   ``isinstance`` keep working — the engine cannot tell the difference, and a
   caller that tries to write gets ``TypeError`` at the offending line instead
   of silently corrupting the shared cache every other module reads.

2. RELOADABLE. ``invalidate()`` drops the asset cache and every derived cache
   that registered with it, so replacing a rules file and calling
   ``prokname.reload_data()`` takes effect in-process. Before this, the only
   cache with a reload entry point was LPSN's status table, and Studio froze a
   rank snapshot at import time — meaning "M0 expert sign-off, then swap the
   JSON" silently required a restart.

Derived caches register through ``on_invalidate()`` rather than being imported
here: this module is the innermost layer and must not reach out to ``dedup`` or
``studio``. If a module has not been imported yet, it has no caches to drop, so
an empty hook list is correct rather than a missing case.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import cache
from importlib import resources

DATA_PACKAGE = "prokname.data"

#: Registered by modules holding caches derived from these assets. Hooks are
#: called by invalidate() and are never allowed to leave the system half-updated
#: (see _hooks, which is cleared before any hook runs).
_hooks: list[Callable[[], None]] = []


class _ReadOnlyDict(dict):
    """A dict that refuses to be mutated, without ceasing to be a dict."""

    __slots__ = ()

    def _nope(self, *args, **kwargs):  # noqa: ANN002,ANN003,ARG002,D102
        raise TypeError(
            "prokname data assets are read-only; build a copy instead "
            "(dict(asset)) — mutating a shared asset would change every "
            "other module's view of the rules"
        )

    __setitem__ = __delitem__ = _nope
    update = setdefault = pop = popitem = clear = _nope

    def __ior__(self, other):  # pragma: no cover - `d |= other`
        return self._nope()


class _ReadOnlyList(list):
    """A list that refuses to be mutated, without ceasing to be a list."""

    __slots__ = ()

    def _nope(self, *args, **kwargs):  # noqa: ANN002,ANN003,ARG002,D102
        raise TypeError(
            "prokname data assets are read-only; build a copy instead "
            "(list(asset))"
        )

    __setitem__ = __delitem__ = _nope
    append = extend = insert = remove = pop = sort = reverse = clear = _nope

    def __iadd__(self, other):  # pragma: no cover - `l += other`
        return self._nope()


def _freeze(value):  # noqa: ANN001, ANN201 - recursive by nature
    """Wrap ``value`` so mutation fails loudly, recursively.

    Applied once, at load time, then cached: the cost is paid per asset per
    process, not per lookup.
    """
    if isinstance(value, dict):
        return _ReadOnlyDict({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return _ReadOnlyList(_freeze(v) for v in value)
    return value


def on_invalidate(hook: Callable[[], None]) -> Callable[[], None]:
    """Register a derived-cache clear to run whenever the assets change.

    Usable as a decorator on an existing ``clear_*`` function; returns it
    unchanged so the module keeps its own public name.
    """
    _hooks.append(hook)
    return hook


@cache
def load_json(filename: str) -> dict:
    ref = resources.files(DATA_PACKAGE).joinpath(filename)
    return _freeze(json.loads(ref.read_text(encoding="utf-8")))


def invalidate() -> int:
    """Drop the asset cache and every derived cache; returns hooks run.

    Order matters and is deliberate: the asset cache is cleared first, then
    each registered hook re-derives from scratch on next use, so no hook can
    observe a torn state (new asset against old derived index, or the reverse).
    A hook that raises is reported but does not stop the others — a half-cleared
    cache is recoverable, an unloadable CLI is not.
    """
    load_json.cache_clear()
    ran = 0
    errors: list[str] = []
    for hook in list(_hooks):
        try:
            hook()
            ran += 1
        except Exception as exc:  # noqa: BLE001 - one bad hook must not strand the rest
            errors.append(f"{getattr(hook, '__qualname__', hook)!r}: {exc!r}")
    if errors:  # pragma: no cover - only reachable with a broken hook
        raise RuntimeError(
            "data assets were cleared, but these derived caches failed to: "
            + "; ".join(errors)
        )
    return ran


def invalidate_hooks() -> tuple[str, ...]:
    """Names of the registered derived caches, for `prokname data`.

    Qualified with the owning module: three of them are legitimately called
    `clear_derived_caches`, and a listing that shows the same name three times
    is not evidence that three things are wired up.
    """
    names = []
    for hook in _hooks:
        module = getattr(hook, "__module__", "") or ""
        qual = getattr(hook, "__qualname__", None) or repr(hook)
        tail = ".".join(module.split(".")[-2:]) if module else ""
        names.append(f"{tail}.{qual}" if tail else qual)
    return tuple(names)


def rules() -> dict:
    return load_json("rules.json")


def gender_lexicon() -> dict:
    return load_json("genus_gender.json")


def gender_heuristics() -> dict:
    return load_json("gender_endings.json")


def person_genitive() -> dict:
    # Latinization-paradigm -> genitive-ending model. Kept in its own
    # asset because every cell has to carry an LPSN instance and a verified flag.
    return load_json("person_genitive.json")


def type_genus_stems() -> dict:
    # Declension-stem lexicon for higher-rank name derivation:
    # suprageneric names are built on the GENITIVE stem of the type genus, never
    # on the nominative. Also carries the documented morphological fallback rules
    # and the attestation list that compliance may be asserted from.
    return load_json("type_genus_stems.json")


def lpsn_status() -> dict:
    # Authority status vocabulary the LPSN adapter classifies with. Shipped as
    # an asset so an expert can review the exact table a ruling came from; rows
    # carry their own `verified` flag, and `prokname data` reports how many are
    # still unverified.
    return load_json("lpsn_status.json")


def seqcode_registered() -> dict:
    # SeqCode occupancy snapshot (dedup/seqcode.py rules from this). Rebuilt
    # from the live registry by scripts/build_seqcode_snapshot.py; carries its
    # own retrieval date, licence quote and negative-scope warning.
    return load_json("seqcode_registered.json")


def stems() -> dict:
    # NOT CONSUMED by the engine: documentation/transparency asset only,
    # surfaced so `prokname data` can report its version and review status.
    # Engine stem handling reads rules.json instead. Marked as unconnected in
    # asset_status() so nobody mistakes shipping for effect.
    return load_json("stems.json")


def rate_limit_budget() -> dict:
    # Planned per-source request budgets. Consumed for pacing by
    # scripts/build_seqcode_snapshot.py (the only runtime reader as of
    # 2026-09-25); scripts/rebuild_corpus.py still carries its own RateLimiter
    # for LPSN instead of reading this file, which asset_status() reports as
    # partially connected.
    return load_json("rate_limit_budget.json")


def corpus_seed() -> dict:
    return load_json("corpus_seed.json")


#: Which assets the engine actually reads, and what happens if you edit them.
#: `consumers` is a description, not a call graph — it is here so `prokname data`
#: cannot drift into claiming a file is live when no code loads it.
_ASSET_CONSUMERS: dict[str, str] = {
    "rules.json": "engine (generation + validation), routing, benchmark",
    "genus_gender.json": "engine.gender lookup, dedup.nearmatch",
    "gender_endings.json": "engine.gender heuristics",
    "person_genitive.json": "engine.genitive, engine.validate",
    "type_genus_stems.json": "engine.generate (higher ranks)",
    "lpsn_status.json": "dedup.lpsn classify_status (authority rulings)",
    "seqcode_registered.json": "dedup.seqcode (authority, positive only)",
    "corpus_seed.json": "dedup.nearmatch (demo corpus, 16 names)",
    "stems.json": "NOT CONNECTED — shipped for reporting only",
    "rate_limit_budget.json": "PARTIAL — scripts/build_seqcode_snapshot.py pacing "
                              "only; adapters keep their own retry caps",
}


def asset_status() -> dict:
    """Versions, gating status and consumer state of every data asset.

    This is the command-surface answer to "would editing this file change
    anything?", which is the question an M0 sign-off has to be able to ask.
    """
    loaders = {
        "rules.json": rules,
        "genus_gender.json": gender_lexicon,
        "gender_endings.json": gender_heuristics,
        "person_genitive.json": person_genitive,
        "type_genus_stems.json": type_genus_stems,
        "lpsn_status.json": lpsn_status,
        "seqcode_registered.json": seqcode_registered,
        "stems.json": stems,
        "corpus_seed.json": corpus_seed,
        "rate_limit_budget.json": rate_limit_budget,
    }
    assets: dict[str, dict] = {}
    for name, loader in loaders.items():
        try:
            payload = loader()
        except Exception as exc:  # noqa: BLE001 - report, never crash the listing
            assets[name] = {"present": False, "error": repr(exc)}
            continue
        meta = payload.get("_meta", payload)
        entry = {
            "present": True,
            "version": meta.get("version"),
            "status": str(meta.get("status", meta.get("note", "")))[:160],
            "consumed_by": _ASSET_CONSUMERS.get(name, "unknown"),
        }
        # Verified-flag inventory: the M0 gate is "every ruling traces to a
        # verified assertion", so the count of unverified rows is the honest
        # distance still to go. Only reported where such rows exist.
        unverified = _count_unverified(payload)
        if unverified is not None:
            entry["unverified_rows"] = unverified
        if name == "seqcode_registered.json":
            entry["retrieved_at"] = meta.get("retrieved_at")
            entry["names_recorded"] = meta.get("names_recorded")
        assets[name] = entry
    return assets


def _count_unverified(node: object) -> int | None:
    """Count dict rows explicitly marked verified: false, recursively.

    Returns None when nothing in this asset carries the flag at all, so the
    listing does not imply "all verified" where the question was never asked.
    """
    seen = False

    def walk(value: object) -> int:
        nonlocal seen
        total = 0
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "verified":
                    seen = True
                    if child is False:
                        total += 1
                else:
                    total += walk(child)
        elif isinstance(value, list):
            for child in value:
                total += walk(child)
        return total

    count = walk(node)
    return count if seen else None
