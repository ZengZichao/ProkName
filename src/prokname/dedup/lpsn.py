"""Authority adapter: LPSN (the ICNP authority).

Design contract:
- NEVER fabricate a ruling. If the adapter cannot actually query LPSN it
  returns status 'unavailable', and the orchestrator BLOCKS adjudication
  ('not found' is only meaningful when the authority was reachable).
- Thin wrapper over the official client when possible: PyPI package `lpsn`
  (LeibnizDSMZ/lpsn-api, MIT; contract pinned to source commit c15229e7,
  package version 1.0.0), Keycloak credentials via OS keyring or
  PROKNAME_LPSN_* environment variables. No plaintext passwords in files
  or logs.
- Network is opt-in: offline by default; only `--online` touches the API.

Official client usage sequence (lpsn-api client.py):
1. `LpsnClient(user, password)` authenticates via DSMZ Keycloak. The client
   REPORTS failures by printing, never by raising — a fresh instance exposes
   `access_token` only after successful authentication, so its absence is
   the auth-failure signal. Its prints are captured here so `--json` output
   and logs stay clean.
2. `client.search(taxon_name=..., match_mode="exact")` (underscores map to
   API hyphens) runs the advanced search and returns a hit COUNT. It returns
   0 BOTH for a genuine zero-result query AND for a rejected query (error
   payload only printed); the two are distinguished afterwards by inspecting
   `client.result`: a dict with `count == 0` means genuinely not found,
   anything else means the query did not succeed. Because the client's
   underscore→hyphen mapping can silently disagree with the documented
   `{match_mode}` parameter name (and the service default is a substring
   `contains` search), the explicit mode is only the first line of defence —
   `retrieve()` results are additionally filtered to records whose
   `full_name` equals the query (case/whitespace-insensitive) before any
   classification happens (see `check`).
3. `client.retrieve()` yields the full entries of the last search as dicts
   (optionally filtered by a list of keys). Classification reads the
   `lpsn_taxonomic_status` dict key of an identity-matched record only — and
   that key carries LPSN's *joined* status string, i.e. nomenclatural and
   taxonomic labels comma-joined (see "Status classification" below).

M0 gate: the recorded endpoint behaviour above derives from the official
client source (LeibnizDSMZ/lpsn-api); batch verification
runs should still cross-check one live response per run before relying on
the results.

Status classification:
- LPSN does NOT carry one status field. It carries a *nomenclatural* status
  ("validly published under the ICNP", "conserved name", "not validly
  published", "validly published under ICN", "illegitimate name", ...) and a
  *taxonomic* status ("correct name", "preferred name", "synonym",
  "misspelling", "later homonym", ...), and the page/API emit them
  COMMA-JOINED into a single string, sometimes with parenthetical qualifiers
  ("correct name (and explicitly recommended for medical use)",
  "synonym (and no standing)"). Evidence:
  docs/provenance/upstream-references.md#l1--lpsn-client-response-shape —
  the field names and the one-label-per-record shape are pinned to
  LeibnizDSMZ/lpsn-api@c15229e7 README.md:81-96 and re-checkable with
  scripts/check_upstream_citations.py. The joined multi-fact rendering is
  observed on live LPSN pages (URLs recorded with each row of
  data/lpsn_status.json) but has never been captured as a live API cassette, so
  the rows that depend on it keep verified=false. A working note that collected
  these observations used to sit in /tmp/prokname_fix/ and is gone; the URLs
  survived it, which is the point of citing URLs rather than scratch files.
- Therefore classification is a THREE-step operation, never a whole-string
  lookup and never a substring test (the original
  defect):
    1. `split_status()` tokenises on top-level commas/semicolons and separates
       each parenthetical qualifier from its bare label (qualifier text is
       preserved verbatim for the detail string, never silently dropped);
    2. every token is looked up, exact-match only, in TWO SEPARATE label
       tables (`data/lpsn_status.json` -> `nomenclatural_statuses` /
       `taxonomic_statuses`); a token that exists in both tables is flagged
       ambiguous and is never classified;
    3. `_combine()` derives the verdict: VALIDITY comes from the nomenclatural
       tokens only, NAME OCCUPANCY from the taxonomic tokens only, so the two
       vocabularies cannot be conflated even in principle. A nomenclatural
       token alone can never produce an occupancy claim; a taxonomic token
       alone can never produce an ICNP-validity claim.
- Safety properties that must not regress:
  * `Non-validly published name` / `Not validly published name` (and the live
    `not validly published`) are never read as valid;
  * `Variant` / `In preparation` (and `misspelling`) are non-occupying
    warnings, never hard conflicts;
  * an identity-matched record with no readable status stays `verified=False`
    and non-ruling;
  * any unrecognised token -> category `unknown`, which the orchestrator
    treats like an unreachable authority (BLOCKED) instead of guessing;
  * only labels that OCCUPY the name string may produce a conflict.
- Field-shape surprises (a dict- or list-valued status) are handled inside
  the same try block as the network call, so a changed payload degrades to
  `found_unknown`/`unavailable` instead of raising AttributeError out of
  `check()`.
"""

from __future__ import annotations

import contextlib
import io
import os
from dataclasses import dataclass
from functools import cache

from .. import diagnostics
from ..engine import data as _data
from .model import (
    FOUND_OCCUPIED,
    FOUND_OTHER,
    FOUND_UNKNOWN,
    FOUND_VALID,
    SourceResult,
)

LPSN_URL = "https://lpsn.dsmz.de/"
_TIER = "authority"

#: Expert-reviewable label -> claim tables. They are a data asset rather
#: than inline logic so that a nomenclaturist can audit a ruling cell by cell.
LPSN_STATUS_ASSET = "lpsn_status.json"

#: The two LPSN status fields. A token is attributed to exactly one of them
#: and contributes ONLY that dimension to the verdict.
NOMENCLATURAL = "nomenclatural"
TAXONOMIC = "taxonomic"
_VOCABULARIES = (NOMENCLATURAL, TAXONOMIC)
_AMBIGUOUS = "ambiguous"          # a label present in BOTH vocabularies

# --- nomenclatural claims (may only assert validity) ------------------------
VALID_ICNP = "valid_under_icnp"            # 'validly published under the ICNP'
VALID_OTHER_CODE = "valid_under_other_code"  # e.g. 'validly published under ICN'
NOT_VALID = "not_validly_published"        # negated / illegitimate / announced
VALIDITY_UNASSERTED = "unasserted"         # matched, but asserts nothing

# --- taxonomic claims (may only assert name occupancy) ----------------------
OCCUPYING_CORRECT = "occupies_correct_name"
OCCUPYING_IN_USE = "occupies_name_in_use"    # 'preferred name'
OCCUPYING_NOT_CORRECT = "occupied_not_correct"  # synonym / homonym / previous
NON_OCCUPYING = "does_not_occupy"            # variant, misspelling, ...

# --- derived combination classes -------------------------------------------
CONTRADICTORY = "contradictory"
_ABSENT = ""

#: category (data asset) -> SourceResult status. The mapping lives here so a
#: reader of the table sees exactly what a category does to a verdict.
_CATEGORY_STATUS = {
    "occupied_valid": FOUND_VALID,       # conflict — correct, validly published
    "occupied_other": FOUND_OCCUPIED,    # conflict — string is occupied
    "unoccupied_record": FOUND_OTHER,    # warning — record exists, name free
    "unknown": FOUND_UNKNOWN,            # no ruling
}
_CATEGORY_OCCUPIES = {
    "occupied_valid": True,
    "occupied_other": True,
    "unoccupied_record": False,
    "unknown": None,
}
_CATEGORY_VALUES = tuple(_CATEGORY_STATUS)

#: Occupying claims that do NOT assert "this is the correct name".
_OCCUPYING_OTHER = (OCCUPYING_IN_USE, OCCUPYING_NOT_CORRECT)

# Last-resort tables used ONLY when the data asset cannot be read (e.g. a
# frozen bundle that predates it). They contain just the two labels that are
# sourced from the vendored official client; every other label then degrades
# to `found_unknown`, i.e. the adapter stops being able to rule — which is
# the honest failure mode.
#   provenance: lpsn-api/README.md:88-96 (snapshot c15229e7)
# The nomenclatural table is EMPTY on purpose: no nomenclatural label is
# attested by the vendored client, so without the asset prokname can read the
# taxonomic half of a status string only.
_FALLBACK_NOMENCLATURAL: list[dict] = []
_FALLBACK_TAXONOMIC = [
    {"label": "correct name", "aliases": [], "claim": OCCUPYING_CORRECT,
     "category": "occupied_valid", "verified": True,
     "provenance": "lpsn-api/README.md:89 (vendored c15229e7)"},
    {"label": "synonym", "aliases": [], "claim": OCCUPYING_NOT_CORRECT,
     "category": "occupied_other", "verified": True,
     "provenance": "lpsn-api/README.md:90-95 (vendored c15229e7)"},
]


@dataclass(frozen=True)
class StatusToken:
    """One comma/semicolon-separated status label of a status string."""

    text: str                # verbatim fragment, as it appeared in the record
    label: str               # bare label, parenthetical qualifiers removed
    qualifier: str           # qualifier text kept verbatim ('' when none)
    vocabulary: str          # 'nomenclatural' | 'taxonomic' | '' (unmatched)
    claim: str               # the matched row's validity/occupancy claim
    verified: bool
    provenance: str = ""
    note: str = ""


@dataclass(frozen=True)
class StatusClassification:
    """Result of classifying one `lpsn_taxonomic_status` value.

    `label` is the full status string as flattened from the record; `tokens`
    is the per-token breakdown with the vocabulary each token was read from,
    so a reviewer can see *why* the combination produced `category`.
    """

    label: str
    category: str
    status: str
    occupies: bool | None
    verified: bool
    provenance: str = ""
    note: str = ""
    tokens: tuple[StatusToken, ...] = ()
    #: derived from the NOMENCLATURAL tokens only
    validity: str = _ABSENT
    #: derived from the TAXONOMIC tokens only
    occupancy: str = _ABSENT
    #: parenthetical qualifier text carried through from the source string
    qualifiers: tuple[str, ...] = ()


def _normalise_label(raw: object) -> str:
    """Casefold / whitespace-collapse a status label (never substring-match)."""
    return " ".join(str(raw).split()).casefold()


def split_status(text: str) -> list[str]:
    """Split a status string into top-level `,` / `;` separated fragments.

    Parentheses/brackets are tracked so a qualifier that itself contains a
    comma ("correct name (see also Bacillota, cf. Smith 2020)") stays ONE
    fragment instead of being torn into two unrecognisable halves.
    """
    fragments: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([{":
            depth += 1
            current.append(char)
        elif char in ")]}":
            depth = max(0, depth - 1)
            current.append(char)
        elif char in ",;" and depth == 0:
            fragments.append("".join(current))
            current = []
        else:
            current.append(char)
    fragments.append("".join(current))
    return [f.strip() for f in fragments if f.strip()]


def strip_qualifier(fragment: str) -> tuple[str, str]:
    """Separate a fragment's bare label from its parenthetical qualifier(s).

    Returns (bare_label, qualifiers): the label keeps its own wording so the
    exact table lookup sees "correct name", while the qualifier text is
    returned verbatim for the detail string: a qualifier such as
    "(and no standing)" must be *reported*, not silently normalised away.
    """
    bare: list[str] = []
    qualifiers: list[str] = []
    depth = 0
    buffer: list[str] = []
    for char in fragment:
        if char in "([{":
            depth += 1
            if depth == 1:
                buffer = []
                continue
        if char in ")]}":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    inner = " ".join("".join(buffer).split())
                    if inner:
                        qualifiers.append(inner)
                    continue
        if depth > 0:
            buffer.append(char)
        else:
            bare.append(char)
    if depth > 0:  # unbalanced: keep the text we have, drop nothing
        trailing = " ".join("".join(buffer).split())
        if trailing:
            qualifiers.append(trailing)
    # NB: `bare` is a character list — join it with "" first. Trailing '.' is
    # NOT stripped: labels such as 'var.' and 'cass./orth. var.' are
    # legitimate abbreviations and must survive to the lookup key.
    return (" ".join("".join(bare).split()).strip(" ,;:"),
            "; ".join(qualifiers))


def _flatten_status(raw: object) -> tuple[str | None, bool]:
    """Best-effort extraction of a printable label from a status field.

    Returns (label, shape_ok). The official client yields full entries as
    dicts whose `lpsn_taxonomic_status` is a string (lpsn-api/README.md:89);
    `retrieve(filter=[...])` instead yields lists of single-key dicts
    (lpsn-api/README.md:81-96), so a dict/list-shaped value is flattened
    rather than crashed on.
    """
    if raw is None:
        return "", True
    if isinstance(raw, str):
        return raw.strip(), True
    if isinstance(raw, (int, float, bool)):
        return str(raw), True
    if isinstance(raw, dict):
        for key in ("lpsn_taxonomic_status", "taxonomic_status", "name",
                    "label", "status", "title"):
            if key in raw and not isinstance(raw[key], (dict, list)):
                return str(raw[key]).strip(), True
        scalars = [str(v).strip() for v in raw.values()
                   if not isinstance(v, (dict, list))]
        return ("; ".join(s for s in scalars if s), False)
    if isinstance(raw, (list, tuple)):
        flat = [str(v).strip() for v in raw if not isinstance(v, (dict, list))]
        if not flat:
            for item in raw:
                if isinstance(item, dict):
                    inner, ok = _flatten_status(item)
                    if inner:
                        return inner, ok
        return ("; ".join(s for s in flat if s), False)
    return (str(raw), False)


def _register(lookup: dict[str, dict], key: str, row: dict) -> None:
    """Index one label/alias under its vocabulary — refusing ambiguity.

    A key that shows up twice with a DIFFERENT vocabulary or a different
    claim is stored as an `ambiguous` marker: such a label can then never be
    read as either dimension, which is what makes the nomenclatural/taxonomic
    conflation impossible by construction rather than by convention.
    """
    previous = lookup.get(key)
    if previous is None:
        lookup[key] = row
        return
    if (previous.get("vocabulary") != row.get("vocabulary")
            or previous.get("claim") != row.get("claim")):
        lookup[key] = {"label": row.get("label", key), "vocabulary": _AMBIGUOUS,
                       "claim": "", "verified": False,
                       "provenance": "label present in more than one vocabulary "
                                     "or with conflicting claims",
                       "note": "ambiguous asset row — prokname refuses to pick "
                               "a dimension for it"}


def build_status_lookup(payload: object) -> tuple[dict[str, dict], str]:
    """label/alias -> {vocabulary, claim, verified, …} from the data asset.

    Reads the TWO vocabulary lists of `lpsn_status.json`. The flat
    `statuses` list the asset also carries is the *derived* standalone view
    (see tests/test_lpsn_status_vocabulary.py) and is not consulted here, so
    a reviewer cannot make the matcher rule by listing a label once only.
    """
    try:
        rows = payload.get("nomenclatural_statuses")
        tax = payload.get("taxonomic_statuses")
        if not isinstance(rows, list) or not isinstance(tax, list):
            raise ValueError("no nomenclatural_statuses/taxonomic_statuses list")
        if not rows and not tax:
            raise ValueError("empty status vocabularies")
        provenance_note = str(payload.get("_meta", {}).get("version", ""))
    except Exception:  # unreadable / unshipped asset → conservative fallback
        rows, tax = _FALLBACK_NOMENCLATURAL, _FALLBACK_TAXONOMIC
        provenance_note = "FALLBACK (asset unreadable)"
    lookup: dict[str, dict] = {}
    for vocabulary, table in ((NOMENCLATURAL, rows), (TAXONOMIC, tax)):
        for row in table:
            if not isinstance(row, dict):
                continue
            label = row.get("label")
            claim = row.get("claim")
            if not isinstance(label, str) or not label.strip():
                continue
            if not isinstance(claim, str) or not claim.strip():
                continue
            entry = {
                "label": label.strip(),
                "vocabulary": vocabulary,
                "claim": claim.strip(),
                "verified": bool(row.get("verified", False)),
                "provenance": str(row.get("provenance", "")),
                "note": str(row.get("note", "")),
            }
            _register(lookup, _normalise_label(label), entry)
            aliases = row.get("aliases", [])
            if isinstance(aliases, (list, tuple)):
                for alias in aliases:
                    if isinstance(alias, str) and alias.strip():
                        _register(lookup, _normalise_label(alias), entry)
    return lookup, provenance_note


@cache
def _status_lookup() -> tuple[dict[str, dict], str]:
    """The memoised lookup. The returned rows are read-only: never mutate."""
    try:
        payload = _data.load_json(LPSN_STATUS_ASSET)
    except Exception:  # missing / corrupt asset → the vendored two-label set
        payload = {}
    return build_status_lookup(payload)


def _validity_class(claims: list[str]) -> str:
    """Collapse the NOMENCLATURAL claims of a status string into one class."""
    kinds = set(claims)
    if not kinds:
        return _ABSENT
    if VALID_ICNP in kinds and NOT_VALID in kinds:
        return CONTRADICTORY          # 'validly published' + 'not validly …'
    if NOT_VALID in kinds:
        return NOT_VALID
    if VALID_ICNP in kinds:
        return VALID_ICNP
    if VALID_OTHER_CODE in kinds:     # valid under ICN/ICNafp ⇒ not ICNP-valid
        return VALID_OTHER_CODE
    return VALIDITY_UNASSERTED


def _occupancy_class(claims: list[str]) -> str:
    """Collapse the TAXONOMIC claims of a status string into one class.

    A single claim is reported as-is (so the reviewer sees exactly which
    taxonomic label LPSN used); several are collapsed only where they agree
    that the string is occupied without being the correct name.
    """
    kinds = set(claims)
    if not kinds:
        return _ABSENT
    if len(kinds) == 1:
        return next(iter(kinds))
    if OCCUPYING_CORRECT in kinds and NON_OCCUPYING in kinds:
        return CONTRADICTORY
    if kinds <= set(_OCCUPYING_OTHER):
        return OCCUPYING_NOT_CORRECT
    # 'correct name' beside a synonym / previous name / variant: LPSN cannot
    # mean both, and prokname will not pick one.
    return CONTRADICTORY


def combine_status_claims(validity: str, occupancy: str) -> str:
    """The category a (validity, occupancy) pair licenses. Pure function.

    Occupancy is readable ONLY from the taxonomic dimension, so a status
    string that carries no taxonomic token can never be told 'this name is
    the correct one' — nor, except for an explicitly negative nomenclatural
    finding, may it be told anything at all (`unknown` → BLOCKED). Validity
    is readable ONLY from the nomenclatural dimension, so `correct name` on
    its own can never be upgraded by the taxonomic side into an ICNP-validity
    claim beyond what the vendored client's documented value licenses.
    """
    if CONTRADICTORY in (validity, occupancy):
        return "unknown"
    if occupancy == _ABSENT:
        return "unoccupied_record" if validity == NOT_VALID else "unknown"
    if occupancy == NON_OCCUPYING:
        return "unoccupied_record"
    if occupancy in _OCCUPYING_OTHER:
        return "occupied_other"
    # occupancy == OCCUPYING_CORRECT
    if validity == _ABSENT or validity == VALID_ICNP:
        return "occupied_valid"
    if validity in (NOT_VALID, VALID_OTHER_CODE):
        return "occupied_other"      # occupied, but not ours to publish under
    return "unknown"                 # validity matched a row that asserts nothing


def _unresolved_classification(text: str, unresolved: list[str],
                               shape_ok: bool) -> StatusClassification:
    """A non-empty status containing a label we cannot source → no verdict."""
    return StatusClassification(
        label=text,
        category="unknown",
        status=_CATEGORY_STATUS["unknown"],
        occupies=_CATEGORY_OCCUPIES["unknown"],
        verified=False,
        provenance=(
            f"unrecognised status token(s) {', '.join(repr(u) for u in unresolved)}"
            f" — no exact match in {LPSN_STATUS_ASSET}"
            + ("" if shape_ok else "; unrecognised field shape")
        ),
        note=(
            "unknown LPSN status token — prokname refuses to infer validity or "
            "occupancy from substrings or from the other vocabulary; add the "
            "label to the right one of the two tables (nomenclatural vs "
            "taxonomic) with an expert-verified claim"
        ),
    )


def classify_status(raw: object) -> StatusClassification:
    """Classify an `lpsn_taxonomic_status` value — per-token exact lookup.

    Never raises: an unexpected shape or an unsourceable token yields
    category 'unknown' with `verified=False`, which the orchestrator refuses
    to rule on. Substring matching is impossible here; so is reading a
    nomenclatural label as a taxonomic one (or the reverse).

    Two distinct abstentions:
    - an EMPTY / unreadable status on an identity-matched record: LPSN does
      list the string, we just cannot read why — report it as a non-occupying
      record (`found_other`, warning tier) with `verified=False`;
    - a NON-EMPTY status with a token that is not in either table
      (`found_unknown`): the vocabulary itself drifted, so no verdict at all
      is admissible.
    """
    text, shape_ok = _flatten_status(raw)
    label = _normalise_label(text)
    if not label:
        return StatusClassification(
            label="",
            category="unoccupied_record",
            status=FOUND_OTHER,
            occupies=False,
            verified=False,
            provenance=(
                "no lpsn_taxonomic_status value on the identity-matched record"
            ),
            note=(
                "record exists but carries no readable taxonomic status — "
                "reported as a non-occupying record, not as a conflict"
            ),
        )
    lookup, _ = _status_lookup()
    tokens: list[StatusToken] = []
    qualifiers: list[str] = []
    unresolved: list[str] = []
    for fragment in split_status(text):
        bare, qualifier = strip_qualifier(fragment)
        if qualifier:
            qualifiers.append(qualifier)
        if not bare:
            continue               # a qualifier-only fragment: reported, not classified
        row = lookup.get(_normalise_label(bare))
        vocabulary = str(row.get("vocabulary", "")) if row else ""
        if row is None or vocabulary not in _VOCABULARIES:
            unresolved.append(bare)
            continue
        tokens.append(StatusToken(
            text=fragment,
            label=bare,
            qualifier=qualifier,
            vocabulary=vocabulary,
            claim=str(row["claim"]),
            verified=bool(row.get("verified", False)),
            provenance=str(row.get("provenance", "")),
            note=str(row.get("note", "")),
        ))
    if unresolved:
        return _unresolved_classification(text, unresolved, shape_ok)
    if not tokens:
        # Nothing classifiable survived (punctuation, a lone parenthetical,
        # an unparsable scalar) — that is vocabulary drift, not a clean record.
        return _unresolved_classification(text, [text], shape_ok)

    validity = _validity_class(
        [t.claim for t in tokens if t.vocabulary == NOMENCLATURAL])
    occupancy = _occupancy_class(
        [t.claim for t in tokens if t.vocabulary == TAXONOMIC])
    category = combine_status_claims(validity, occupancy)
    provenance = " + ".join(
        f"{t.vocabulary}:{t.label} [{t.provenance or 'provenance missing'}]"
        for t in tokens
    )
    if qualifiers:
        provenance += "; qualifier(s) kept verbatim: " + "; ".join(
            repr(q) for q in qualifiers)
    if not shape_ok:
        provenance += "; unrecognised field shape"
    notes = " | ".join(f"{t.vocabulary}:{t.note}" for t in tokens if t.note)
    note = (
        f"nomenclatural claim={validity or '-'} (validity read from "
        f"nomenclatural tokens only), taxonomic claim={occupancy or '-'} "
        f"(name occupancy read from taxonomic tokens only)"
    )
    if qualifiers:
        note += (" — parenthetical qualifier(s) preserved, not interpreted: "
                 + "; ".join(repr(q) for q in qualifiers))
    if notes:
        note += f"; {notes}"
    return StatusClassification(
        label=text,
        category=category,
        status=_CATEGORY_STATUS.get(category, FOUND_UNKNOWN),
        occupies=_CATEGORY_OCCUPIES.get(category),
        # `verified` answers a different question from `category`: it says the
        # tokens were read from sourced rows. A verified row may still license
        # no ruling (category 'unknown'), which is what `status` reports.
        verified=all(t.verified for t in tokens) and shape_ok,
        provenance=provenance,
        note=note,
        tokens=tuple(tokens),
        validity=validity,
        occupancy=occupancy,
        qualifiers=tuple(qualifiers),
    )


def status_table() -> dict:
    """The raw expert-reviewable status asset (read-only)."""
    try:
        return _data.load_json(LPSN_STATUS_ASSET)
    except Exception:  # pragma: no cover - asset ships with the package
        return {"nomenclatural_statuses": _FALLBACK_NOMENCLATURAL,
                "taxonomic_statuses": _FALLBACK_TAXONOMIC,
                "statuses": _FALLBACK_TAXONOMIC,
                "_meta": {"version": "fallback",
                          "note": "asset unreadable; only the two "
                                  "vendored-verified taxonomic labels are "
                                  "classified"}}


def reload_status_table() -> None:
    """Drop the memoised status table (after an expert edits the asset)."""
    _status_lookup.cache_clear()
    _data.load_json.cache_clear()


def clear_derived_caches() -> None:
    """Drop the derived status lookup; re-reads the asset on next use.

    Registered with engine.data.on_invalidate() so a global reload covers the
    derived index too, not just the raw table. reload_status_table() stays the
    targeted entry point for anyone who only touched this asset.
    """
    _status_lookup.cache_clear()


_data.on_invalidate(clear_derived_caches)


# Retry posture for interactive CLI use: the official client defaults
# (10 retries x 50 s) would block a pipeline for up to ~8 minutes on a
# dead endpoint; batch scripts can raise these via their own client.
_CLIENT_MAX_RETRIES = 2
_CLIENT_RETRY_DELAY = 2  # seconds
_CLIENT_TIMEOUT = 60  # seconds


def _credentials() -> tuple[str | None, str | None]:
    user = os.environ.get("PROKNAME_LPSN_USER")
    password = os.environ.get("PROKNAME_LPSN_PASSWORD")
    if not (user and password):
        try:  # optional OS keychain storage
            import keyring

            user = user or "prokname-lpsn"
            password = password or keyring.get_password("prokname", user)
        except Exception:
            password = password or None
    return user, password


def check(name: str, *, allow_network: bool = False) -> SourceResult:
    """Query LPSN for a name. Offline/uncredentialed ⇒ 'unavailable'.

    Every answer produced with `allow_network=True` is stamped
    `online_derived=True`, which is what lets the cache refuse to replay it
    in an offline run.
    """
    result = _check(name, allow_network=allow_network)
    if allow_network:
        result.online_derived = True
    return result


def _check(name: str, *, allow_network: bool = False) -> SourceResult:
    if not allow_network:
        return SourceResult(
            name="LPSN", status="unavailable", tier=_TIER,
            detail="offline mode (default); re-run with --online to query LPSN",
            url=LPSN_URL,
        )
    try:
        import lpsn  # official DSMZ client (PyPI: lpsn)
    except ImportError:
        return SourceResult(
            name="LPSN", status="unavailable", tier=_TIER,
            detail=(
                "official client not installed: pip install lpsn, then register "
                "for free API access at https://api.lpsn.dsmz.de/ and provide "
                "credentials via keyring or PROKNAME_LPSN_USER/PASSWORD"
            ),
            url=LPSN_URL,
        )
    user, password = _credentials()
    if not (user and password):
        return SourceResult(
            name="LPSN", status="unavailable", tier=_TIER,
            detail=(
                "no credentials found (keyring or PROKNAME_LPSN_USER/"
                "PROKNAME_LPSN_PASSWORD); free registration at "
                "https://api.lpsn.dsmz.de/"
            ),
            url=LPSN_URL,
        )
    # The official client prints progress/errors to stdout; capture it so
    # machine-readable output is never polluted. Everything it wants to say
    # on failure is summarised in the SourceResult detail instead.
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            client = lpsn.LpsnClient(
                user,
                password,
                max_retries=_CLIENT_MAX_RETRIES,
                retry_delay=_CLIENT_RETRY_DELAY,
                request_timeout=_CLIENT_TIMEOUT,
            )
            if getattr(client, "access_token", None) is None:
                raise RuntimeError(
                    "LPSN authentication failed after "
                    f"{_CLIENT_MAX_RETRIES} retries (check credentials)"
                )
            count = client.search(taxon_name=name, match_mode="exact")
            if count == 0:
                # 0 is ambiguous: genuine zero hits vs rejected query. The
                # successful-response state carries `count: 0`; anything
                # else means the API refused the query (details printed).
                state = getattr(client, "result", None)
                if isinstance(state, dict) and state.get("count") == 0:
                    return SourceResult(
                        name="LPSN", status="not_found", tier=_TIER,
                        detail="no LPSN record for this name", url=LPSN_URL,
                    )
                raise RuntimeError(
                    "LPSN API rejected the query "
                    f"(client said: {buffer.getvalue().strip()!r})"
                )
            matches = list(client.retrieve())
    except Exception as exc:  # auth, token refresh, rate limit, network, drift
        # The client's own words went down the redirect above. Echo them only
        # under `--debug`: an offline user's output contract is the table and
        # the exit code, and log text risks being read as a finding.
        diagnostics.note("lpsn", f"query {name!r} failed: {exc!r}")
        diagnostics.note("lpsn-client-output", buffer.getvalue())
        return SourceResult(
            name="LPSN", status="unavailable", tier=_TIER,
            detail=f"query failed: {exc!r} (authority unavailable ⇒ adjudication blocked)",
            url=LPSN_URL,
        )
    if not matches:
        return SourceResult(
            name="LPSN", status="not_found", tier=_TIER,
            detail="no LPSN record for this name", url=LPSN_URL,
        )
    # Identity guard (the reliable half of the exact-match defence): the
    # official client maps `match_mode` to `match-mode` while the API docs
    # name it `{match_mode}` — when the spellings disagree the parameter is
    # silently ignored and the service falls back to its default `contains`
    # (substring) search. A substring hit is NOT occupancy of the query
    # name, so only records whose full_name equals the query (modulo case
    # and whitespace) may rule on it.
    target = " ".join(name.split()).casefold()
    exact = [
        m for m in matches
        if isinstance(m, dict)
        and " ".join(str(m.get("full_name", "")).split()).casefold() == target
    ]
    if not exact:
        candidates = "; ".join(
            sorted({str(m.get("full_name", "")) for m in matches if isinstance(m, dict)})[:5]
        )
        return SourceResult(
            name="LPSN", status="not_found", tier=_TIER, url=LPSN_URL,
            detail=(
                f"LPSN returned {len(matches)} record(s) for this query but none "
                f"whose full_name equals it (substring matches only — treated "
                f"as not found); closest: {candidates or '-'}"
            ),
        )
    first = exact[0]
    # Classification goes through the expert-reviewable label tables
    # (data/lpsn_status.json): the status string is split into tokens and each
    # token is matched in the table of ITS OWN vocabulary. NO substring test,
    # NO whole-string test — see the two-vocabulary note above.
    classification = classify_status(first.get("lpsn_taxonomic_status"))
    full_name = first.get("full_name", "")
    detail = (
        f"lpsn_taxonomic_status={classification.label!r} -> "
        f"category={classification.category} "
        f"[nomenclatural={classification.validity or '-'} "
        f"(validity), taxonomic={classification.occupancy or '-'} "
        f"(occupancy); {len(classification.tokens)} status token(s); "
        f"{len(matches)} record(s)]"
    )
    if classification.note:
        detail += f"; {classification.note}"
    if not classification.verified:
        detail += (
            "; NOT verified against a live LPSN response "
            f"({classification.provenance or 'provenance missing'})"
        )
    if full_name:
        detail = f"full_name={full_name!r}; {detail}"
    return SourceResult(
        name="LPSN",
        status=classification.status,
        tier=_TIER,
        detail=detail,
        url=LPSN_URL,
        verified=classification.verified,
    )
