"""C-set GAN comparison adapter.

Fairness protocol:
1. Aligned inputs: the same etymology stems fed to both systems.
2. Fixed seed, ≥3 repetitions, and the two variance sources reported
   separately (stem-to-stem spread vs run-to-run variation).
3. Compliance judged by a referee that actually applies the rules — not by a
   capitalisation check (see :func:`compliance_referee`).
4. Species-level capability boundary experiment.

Status of this suite — read before citing any C-set number:

* GAN (telatin/gan, GPL-3.0) is **not vendored** here and its CLI surface is
  **not documented anywhere in this repository**.  The previous version of
  this file guessed ``--stem/--type`` flags; a comparison built on a guessed
  interface is not a comparison, so the adapter now refuses to run without an
  explicit, user-supplied command spec (``gan_command`` /
  ``prokname bench --gan-command``).  Without one the suite reports
  ``protocol_only_never_executed`` and emits **no GAN-side numbers at all**.
* Consequences of the GPL-3.0 isolation: CI runs with ``skip_gan=True``, so
  the GAN side never executes there; what CI *does* execute is the protocol
  itself, against a fake executable
  (``tests/test_metrics_c_set_protocol.py``), so the plumbing — spec
  substitution, seed propagation, output parsing, referee, variance
  bookkeeping, honest failure reporting — is regression-tested.
* No GPL-3.0 source code enters this repository or its distribution; the only
  interaction is a subprocess call with the user's own GAN installation.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import fmean, pstdev, stdev

from ..engine.generate import generate
from .sets import load_c_set

# ---------------------------------------------------------------------------
# Command spec
# ---------------------------------------------------------------------------

#: Placeholders a user-supplied GAN command spec may contain.  ``{stem}`` is
#: mandatory: a spec that never passes the stem cannot be a name generator.
SPEC_PLACEHOLDERS = ("stem", "etymology_type", "source_language", "seed")
#: Environment variable prokname sets so the tool can be seeded without the
#: user having to know its flag spelling (in addition to ``{seed}``).
GAN_SEED_ENV = "GAN_RANDOM_SEED"
#: Per-invocation wall-clock limit.
GAN_TIMEOUT_S = 30.0


def _validate_command_spec(gan_command: Sequence[str] | str) -> list[str]:
    """Normalise and sanity-check a user-supplied GAN command spec.

    Accepts an argv list or a shell-quoted string.  Raises ``ValueError`` with
    an actionable message instead of guessing flags.
    """
    if isinstance(gan_command, str):
        spec = shlex.split(gan_command)
    else:
        spec = [str(part) for part in gan_command]
    if not spec:
        raise ValueError(
            "empty GAN command spec. Provide the tool's real interface, e.g. "
            "['/path/to/gan', 'gen', '{stem}', '--seed', '{seed}'] — see "
            "`gan --help` on the machine where GAN is installed (M0 record)."
        )
    joined = " ".join(spec)
    if "{stem}" not in spec and "{stem}" not in joined:
        raise ValueError(
            f"GAN command spec {spec!r} does not use the stem at all "
            "('{stem}' placeholder missing). Running it would measure "
            "something unrelated to the input, so it is refused."
        )
    for part in spec:
        if part.startswith("{") and part.endswith("}"):
            key = part[1:-1]
            if key not in SPEC_PLACEHOLDERS:
                raise ValueError(
                    f"unknown placeholder {part!r} in GAN command spec; "
                    f"allowed: {', '.join('{' + k + '}' for k in SPEC_PLACEHOLDERS)}"
                )
    return spec


#: Why a stem carries no GAN number when the subprocess was never reached.
#: Recorded per stem so "skipped" can never be read as "GAN produced nothing".
NO_SPEC_REASONS = {
    "skipped_by_flag": (
        "GAN invocation skipped by flag (skip_gan=True — the CI default under "
        "GPL-3.0 isolation): no GAN number exists for this stem. This is a "
        "statement about the run, not a measurement of the competing tool."
    ),
}


def _expand_spec(spec: Sequence[str], values: dict[str, object]) -> list[str]:
    out = []
    for part in spec:
        for key, val in values.items():
            part = part.replace("{" + key + "}", str(val))
        out.append(part)
    return out


def _resolve_executable(argv: Sequence[str]) -> str | None:
    """Locate the executable of an expanded argv, or ``None`` if absent."""
    binary = argv[0]
    if os.path.sep in binary:
        return binary if os.path.isfile(binary) and os.access(binary, os.X_OK) else None
    return shutil.which(binary)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GANRunResult:
    """Results of a single GAN CLI invocation for one stem."""
    stem: str
    etymology_type: str
    candidates: list[str] = field(default_factory=list)
    error: str | None = None
    cli_command: str = ""
    seed: int | None = None
    elapsed_ms: float = 0.0


@dataclass
class ProknameRunResult:
    """Results of prokname generation for one stem.

    ``error`` mirrors :attr:`GANRunResult.error`: a generator crash yields no
    count, not a count of zero.
    """
    stem: str
    etymology_type: str
    candidates: list[dict] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: float = 0.0


@dataclass
class StemComparison:
    """Comparison results for one stem across repetitions."""
    stem: str
    etymology_type: str
    source_language: str
    gan_runs: list[GANRunResult] = field(default_factory=list)
    prokname_runs: list[ProknameRunResult] = field(default_factory=list)

    @property
    def gan_candidate_counts(self) -> list[int | None]:
        """``None`` where the GAN run did not produce a usable result."""
        return [
            None if r.error else len(r.candidates) for r in self.gan_runs
        ]

    @property
    def prokname_candidate_counts(self) -> list[int | None]:
        """``None`` where prokname's generator raised (never a count of 0)."""
        return [
            None if r.error else len(r.candidates) for r in self.prokname_runs
        ]

    @property
    def gan_compliant_counts(self) -> list[int | None]:
        """Count of GAN candidates the referee explicitly accepts."""
        return [
            None if r.error else _count_compliant(r.candidates, r.etymology_type)
            for r in self.gan_runs
        ]

    @property
    def prokname_compliant_counts(self) -> list[int | None]:
        """Count of prokname candidates accepted by the SAME referee.

        Both sides are judged by one referee so the comparison is not decided
        by prokname's own self-report; but see
        :func:`compliance_referee`'s limitation note — for the agreement part
        the referee *is* prokname's validator, which is a bias any comparison
        built on it must disclose (it cannot be both neutral and rule-aware
        without a second implementation or human annotators).
        """
        return [
            None if r.error else _count_compliant(r.candidates, r.etymology_type)
            for r in self.prokname_runs
        ]


# ---------------------------------------------------------------------------
# Compliance referee (fairness protocol #3)
# ---------------------------------------------------------------------------

def _orthographic_guard(name: str) -> bool:
    """Necessary-but-not-sufficient shape check for a scientific name.

    Strict ASCII letters and one separating space only — ``str.isalpha()``
    would accept 'ü'/'ø', so diacritics fail here (a published name must be
    Latin letters only).
    """
    if not name or not name.strip():
        return False
    if not all("a" <= ch <= "z" or "A" <= ch <= "Z" or ch == " " for ch in name):
        return False
    parts = name.strip().split()
    if not parts:
        return False
    genus = parts[0]
    if not genus[0].isupper() or not genus[1:].islower():
        return False
    return all(p.islower() for p in parts[1:])


#: Grammatical agreement for a *person* genitive depends on the sex of the
#: honoured person (Boyd → *boydii* for a male, *boydiae* for a female), and
#: ``validate_agreement`` refuses to guess: without ``person_gender`` it
#: returns ``None``.  The C-set cases do not carry that fact, so both sides of
#: the comparison are judged under ONE declared assumption rather than the
#: referee abstaining on every person name — the assumption is recorded in
#: each verdict and in :func:`_referee_protocol`, because a number produced
#: under it is only comparable if it is the same number for both systems.
ASSUMED_PERSON_GENDER = "male"


def compliance_referee(
    name: str,
    etymology_type: str,
    *,
    person_gender: str | None = None,
) -> dict:
    """Referee used for BOTH systems' candidates (fairness protocol #3).

    Three stages, where the pre-fix implementation had one, which is why
    "feasibility" was effectively "starts with a capital letter":

    1. :func:`_orthographic_guard` — shape/charset, system-independent.
    2. rank identification — a single-token (genus-rank) candidate cannot be
       judged for agreement at all, and is reported ``not_assessed``.
    3. ``prokname.engine.validate.validate_agreement`` — the *real* rule
       engine, applied to binomial candidates and honouring ``etymology_type``
       (the previous referee deleted that argument, so the verdict could not
       depend on the etymology being compared).  ``*R. mongolensis*`` claims a
       place adjective and ``*K. michiganensis*`` is judged as one; the same
       string read as ``feature`` gets a different verdict.

    Returns a dict so a None referee verdict is distinguishable from a False
    one::

        {"compliant": True/False/None, "stage": ..., "detail": ...}

    LIMITATION (must be disclosed wherever these numbers appear): stage 3 is
    prokname's own validator, so the referee is *not* independent of one of
    the competitors — it reuses prokname's reading of the Code, and it must
    assume a person gender where the case data is silent.  The bias runs in
    the direction of whichever candidate form prokname's rules favour.  For
    paper-grade claims the replacement is two human annotators with Cohen's κ
, or a second, independent rule implementation.
    """
    if not _orthographic_guard(name):
        return {
            "compliant": False,
            "stage": "orthographic_guard",
            "detail": "shape/charset reject (ASCII letters, capitalised genus, "
                      "lowercase epithet)",
        }
    parts = name.strip().split()
    if len(parts) < 2:
        # Genus-rank candidate: grammatical agreement is a species-level
        # notion, so there is no rule to check here beyond shape.  Reported as
        # `not_assessed`, never as a pass.
        return {
            "compliant": None,
            "stage": "not_assessed",
            "detail": "single-token (genus-rank) candidate: agreement rules "
                      "do not apply; only the orthographic guard was checked",
        }
    from prokname.engine.validate import validate_agreement

    kwargs: dict[str, object] = {}
    assumed: str | None = None
    if etymology_type == "person":
        assumed = person_gender or ASSUMED_PERSON_GENDER
        kwargs["person_gender"] = assumed

    try:
        vr = validate_agreement(parts[0], parts[1], etymology_type, **kwargs)
    except (ValueError, KeyError) as exc:  # referee must not take the run down
        return {
            "compliant": None,
            "stage": "validator_error",
            "detail": f"validate_agreement raised {exc!r}",
        }
    return {
        "compliant": vr.compliant,
        "stage": "prokname_validate_agreement",
        "etymology_type": etymology_type,
        "grammatical_category": vr.grammatical_category,
        "person_gender_assumed": assumed,
        "verdict_issued": vr.compliant is not None,
        "detail": "; ".join(getattr(vr, "notes", []) or [])
        or "; ".join(getattr(vr, "warnings", []) or []),
    }


def _count_compliant(entries: Sequence[dict] | Sequence[str], etymology_type: str) -> int:
    """Candidates whose referee verdict is an explicit ``True``.

    ``None`` (not assessed / validator error) is NOT counted as compliant —
    counting refusals as passes is the failure mode being removed elsewhere in
    this round of fixes.
    """
    count = 0
    for entry in entries:
        name = entry if isinstance(entry, str) else entry["name"]
        if compliance_referee(name, etymology_type)["compliant"] is True:
            count += 1
    return count


# ---------------------------------------------------------------------------
# GAN invocation (GPL-3.0 isolation)
# ---------------------------------------------------------------------------

def _invoke_gan(
    stem: str,
    etymology_type: str,
    seed: int,
    *,
    spec: Sequence[str] | None,
    source_language: str = "",
    no_spec_reason: str | None = None,
) -> GANRunResult:
    """Invoke GAN for one stem via a user-supplied command spec.

    We NEVER import or copy GAN source code — we call its CLI as a subprocess
    and record the exact command.  Without a spec we do not guess one; with a
    spec whose executable is missing we return an honest error.  GAN output is
    never fabricated in either case, and the reason *why* nothing ran is
    attached to every stem, so a skipped suite cannot be read as "GAN produced
    no candidates".
    """
    result = GANRunResult(stem=stem, etymology_type=etymology_type, seed=seed)
    if spec is None:
        result.error = no_spec_reason or (
            "no GAN command spec supplied. The adapter does not guess the "
            "competing tool's flags: pass "
            "`--gan-command` (or gan_command= in evaluate_c_set) with the "
            "documented interface, e.g. ['gan', 'gen', '{stem}', '--seed', "
            "'{seed}']."
        )
        return result

    argv = _expand_spec(
        spec,
        {
            "stem": stem,
            "etymology_type": etymology_type,
            "source_language": source_language,
            "seed": seed,
        },
    )
    executable = _resolve_executable(argv)
    if executable is None:
        result.error = (
            f"GAN executable not found: {argv[0]!r}. Install GAN "
            "(telatin/gan, GPL-3.0) or point the command spec at it by path."
        )
        result.cli_command = " ".join(shlex.quote(a) for a in argv)
        return result
    argv[0] = executable
    result.cli_command = " ".join(shlex.quote(a) for a in argv)

    env = {**os.environ, GAN_SEED_ENV: str(seed)}
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=GAN_TIMEOUT_S, env=env,
        )
        result.elapsed_ms = (time.perf_counter() - start) * 1000
        if proc.returncode != 0:
            result.error = f"GAN CLI exited {proc.returncode}: {proc.stderr.strip()}"
            return result
        result.candidates = [
            line.strip() for line in proc.stdout.strip().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    except subprocess.TimeoutExpired:
        result.error = f"GAN CLI timed out ({GAN_TIMEOUT_S:.0f}s)"
    except OSError as exc:
        result.error = f"GAN CLI failed: {exc!r}"
    return result


# ---------------------------------------------------------------------------
# prokname side
# ---------------------------------------------------------------------------

def _invoke_prokname(stem: str, etymology_type: str, seed: int) -> ProknameRunResult:
    """Run prokname generation for one stem.

    The ``seed`` argument is accepted for signature symmetry only: prokname's
    generator is deterministic, which is exactly why its run-to-run spread is
    reported as a *determinism check* rather than as noise.  A generator
    crash is recorded as ``error`` and yields no candidate count — a crashed
    side must not enter the comparison as "produced 0 names".
    """
    start = time.perf_counter()
    try:
        candidates = generate(stem, etymology_type, rank="genus")
    except Exception as exc:  # noqa: BLE001 — reported per stem, never hidden
        return ProknameRunResult(
            stem=stem, etymology_type=etymology_type,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )
    return ProknameRunResult(
        stem=stem,
        etymology_type=etymology_type,
        candidates=[
            {
                "name": c.name,
                "epithet": c.epithet or "",
                "category": c.grammatical_category or "",
                "compliant": c.compliant,
                "referee_compliant": compliance_referee(c.name, etymology_type)["compliant"],
            }
            for c in candidates
        ],
        elapsed_ms=(time.perf_counter() - start) * 1000,
    )


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate_c_set(
    *,
    repetitions: int = 3,
    base_seed: int = 42,
    skip_gan: bool = False,
    gan_command: Sequence[str] | str | None = None,
) -> dict:
    """Run the C-set comparison protocol.

    Args:
        repetitions: repeated runs per stem (≥3 per the fairness protocol).
        base_seed: base seed; repetition ``i`` uses ``base_seed + i``.
        skip_gan: force the prokname-only path (CI default under GPL-3.0
            isolation).  Equivalent to not supplying a spec.
        gan_command: explicit, user-supplied GAN CLI spec (argv list or
            shell string) with ``{stem}`` and optionally
            ``{etymology_type}``/``{source_language}``/``{seed}``.

    Raises:
        ValueError: if a spec was supplied but is unusable (no ``{stem}``,
            unknown placeholder, empty).  A rejected interface is a caller
            error, and running it anyway would produce numbers about
            something other than the benchmark input — so the suite refuses
            loudly instead of reporting a hollow comparison.  A *valid*
            spec whose executable is missing is different: that is an honest
            runtime error, recorded per stem, and no GAN number is emitted.
    """
    spec: list[str] | None = None
    declared_spec: list[str] | None = None
    no_spec_reason: str | None = None
    if gan_command is not None:
        # Validated even when this run skips GAN: an unusable spec is a caller
        # error, and the skip flag must not swallow it.
        declared_spec = _validate_command_spec(gan_command)
        spec = None if skip_gan else declared_spec
    if skip_gan:
        spec_status = "skipped_by_flag"
        no_spec_reason = NO_SPEC_REASONS["skipped_by_flag"]
    elif spec is None:
        spec_status = "no_command_spec"
    else:
        spec_status = "user_supplied"

    cases = load_c_set()
    comparisons: list[StemComparison] = []
    for case in cases:
        comp = StemComparison(
            stem=case.stem,
            etymology_type=case.etymology_type,
            source_language=case.source_language,
        )
        for rep in range(repetitions):
            seed = base_seed + rep
            comp.gan_runs.append(
                _invoke_gan(
                    case.stem, case.etymology_type, seed,
                    spec=spec, source_language=case.source_language,
                    no_spec_reason=no_spec_reason,
                )
            )
            comp.prokname_runs.append(
                _invoke_prokname(case.stem, case.etymology_type, seed)
            )
        comparisons.append(comp)

    return _build_report(
        comparisons, repetitions=repetitions, base_seed=base_seed,
        skip_gan=skip_gan,
        # the *declared* spec is what the report records (even when this run
        # skipped GAN); the *invoked* one is the subset that actually ran
        spec=declared_spec, spec_status=spec_status,
    )


# ---------------------------------------------------------------------------
# Variance bookkeeping
# ---------------------------------------------------------------------------

def _spread(values: Sequence[float], *, ddof: int) -> dict:
    """mean ± dispersion with the estimator and n spelled out."""
    vals = [float(v) for v in values]
    if not vals:
        return {"mean": None, "dispersion": None, "n": 0, "estimator": "none"}
    if len(vals) < ddof + 1:
        dispersion, estimator = 0.0, "population sd (n < 2, sample sd undefined)"
    elif ddof == 0:
        dispersion, estimator = pstdev(vals), "population sd (ddof=0)"
    else:
        dispersion, estimator = stdev(vals), "sample sd (ddof=1)"
    return {
        "mean": round(fmean(vals), 4),
        "dispersion": round(dispersion, 4),
        "n": len(vals),
        "estimator": estimator,
    }


def _variance_report(
    comparisons: Sequence[StemComparison],
    side: str,
    *,
    deterministic: bool,
) -> dict:
    """Split the two things the old pooled ``mean_std()`` conflated.

    * ``between_stem`` — spread across different stems (a property of the
      benchmark inputs, NOT run-to-run variance).
    * ``within_stem_run_to_run`` — variation across repetitions for the same
      stem (this is what §3.3 promised).  For a deterministic system it is
      expected to be exactly 0 and is reported as a determinism check; for
      GAN it is the noise figure, with the small ``n`` stated.
    """
    per_stem_between: list[float] = []
    per_stem_within: list[dict] = []
    for comp in comparisons:
        counts: list[int | None] = (
            comp.gan_candidate_counts if side == "gan"
            else comp.prokname_candidate_counts
        )
        usable = [float(c) for c in counts if c is not None]
        if not usable:
            continue
        # first repetition represents the stem for the between-stem spread
        per_stem_between.append(usable[0])
        within = _spread(usable, ddof=1)
        per_stem_within.append({
            "stem": comp.stem,
            "etymology_type": comp.etymology_type,
            "values": usable,
            **within,
        })

    pooled = _spread(per_stem_between, ddof=1)
    nonzero_run_noise = [w for w in per_stem_within if (w["dispersion"] or 0) > 0]
    return {
        "between_stem": {
            **pooled,
            "meaning": "spread of candidate counts ACROSS STEMS (input "
                       "difficulty), not repeated-run variation",
        },
        "within_stem_run_to_run": {
            "per_stem": per_stem_within,
            "stems_with_nonzero_variation": len(nonzero_run_noise),
            "stems_measured": len(per_stem_within),
            "max_stem_dispersion": (
                round(max((w["dispersion"] or 0) for w in per_stem_within), 4)
                if per_stem_within else None
            ),
            "meaning": "variation across repeated runs of the SAME stem — the "
                       "quantity the fairness protocol's 'report variance' "
                       "clause refers to",
        },
        "deterministic_system": deterministic,
        "variance_note": (
            "prokname's generator is deterministic, so its run-to-run "
            "dispersion is 0 by construction and MUST NOT be shown next to "
            "GAN's non-zero figure as if both measured noise: the comparison "
            "is 'deterministic vs stochastic', not 'stable vs unstable'. A "
            "non-zero value on this side would indicate nondeterminism, i.e. "
            "a bug."
            if deterministic else
            "GAN-side dispersion is estimated from few repetitions "
            "(n = repetitions per stem); it is a repeatability diagnostic, "
            "not a precision estimate for the comparison."
        ),
    }


def names_of(run: GANRunResult | ProknameRunResult) -> list[str]:
    """Candidate names from either side's run record (str list or dict list)."""
    if run.candidates and isinstance(run.candidates[0], dict):
        return [c["name"] for c in run.candidates]
    return list(run.candidates)


def _stem_stats(
    stem_candidate_lists: Sequence[Sequence[Sequence[str]]],
    etymology_types: Sequence[str],
) -> dict:
    """Per-stem referee tally: shape pass, True, False, not_assessed."""
    shape_ok = referee_true = referee_decidable_stems = 0
    candidate_total = not_assessed = rejected = 0
    for candidates, etype in zip(stem_candidate_lists, etymology_types, strict=False):
        verdicts: list[bool | None] = []
        shapes: list[bool] = []
        for names in candidates:
            candidate_total += len(names)
            for name in names:
                res = compliance_referee(name, etype)
                verdicts.append(res["compliant"])
                shapes.append(_orthographic_guard(name))
                if res["compliant"] is None:
                    not_assessed += 1
                elif res["compliant"] is False:
                    rejected += 1
        shape_ok += int(any(shapes))
        referee_true += int(any(v is True for v in verdicts))
        referee_decidable_stems += int(any(v is not None for v in verdicts))
    return {
        "stems": len(stem_candidate_lists),
        "candidates_total": candidate_total,
        "stems_passing_shape_guard": shape_ok,
        "stems_with_refereed_compliant_candidate": referee_true,
        "stems_where_referee_could_decide": referee_decidable_stems,
        "candidates_not_assessed": not_assessed,
        "candidates_rejected": rejected,
    }


def _feasibility(stats: dict | None, executed: bool) -> dict | None:
    """Shape-only vs agreement-refereed feasibility, with denominators."""
    if stats is None or not executed:
        return None
    stems = stats["stems"]
    if not stems:
        return None
    out: dict = {
        "shape_only": round(stats["stems_passing_shape_guard"] / stems, 4),
        "shape_only_n": f"{stats['stems_passing_shape_guard']}/{stems}",
        "agreement_refereed": (
            round(stats["stems_with_refereed_compliant_candidate"] / stems, 4)
            if stats["stems_where_referee_could_decide"] else None
        ),
        "agreement_refereed_n": (
            f"{stats['stems_with_refereed_compliant_candidate']}/{stems}"
        ),
        "stems_where_referee_could_decide": stats["stems_where_referee_could_decide"],
        "candidates_not_assessed": stats["candidates_not_assessed"],
        "candidates_rejected": stats["candidates_rejected"],
    }
    if not stats["stems_where_referee_could_decide"]:
        out["not_assessed_reason"] = (
            "every candidate is a single-token genus name, where grammatical "
            "agreement is not applicable; the agreement referee abstains"
        )
    return out


def _build_report(
    comparisons: list[StemComparison],
    *,
    repetitions: int,
    base_seed: int,
    skip_gan: bool,
    spec: Sequence[str] | None,
    spec_status: str,
) -> dict:
    """Build the C-set comparison report."""
    total = len(comparisons)
    gan_executed = any(
        r.error is None for c in comparisons for r in c.gan_runs
    )

    # Feasibility, split by what the referee can actually decide.
    #
    # The C-set compares GENUS candidates (single tokens).  Grammatical
    # agreement is a species-level relation, so for those candidates the real
    # validator has nothing to check and returns `not_assessed`.  Reporting a
    # feasibility rate of 0.0 there would imply "judged non-compliant", which
    # is false; the field is therefore `null`, and the only number that IS
    # computable at genus rank — the weak orthographic shape pass — is
    # reported separately and labelled as weak.
    prok_stats = _stem_stats(
        [[names_of(run) for run in [c.prokname_runs[0]]]
         for c in comparisons],
        [c.etymology_type for c in comparisons],
    )
    # A stem whose every GAN run failed contributes no candidates to referee:
    # it is skipped (and stays visible in `per_stem_results[...]["errors"]`),
    # never silently substituted with an empty list scored as "0 % feasible".
    gan_stats_by_stem = [
        next((r for r in c.gan_runs if r.error is None), None)
        for c in comparisons
    ]
    gan_stats = (
        _stem_stats(
            [[names_of(run)] for run in gan_stats_by_stem if run is not None],
            [c.etymology_type for c, run in zip(comparisons, gan_stats_by_stem,
                                                strict=False)
             if run is not None],
        )
        if gan_executed else None
    )
    if gan_executed:
        gan_counts_report: dict | None = _variance_report(
            comparisons, "gan", deterministic=False
        )
    else:
        gan_counts_report = None

    executed_note = {
        "user_supplied": (
            "GAN was invoked through a user-supplied command spec. The spec's "
            "correctness is the user's responsibility until an M0 record of "
            "the tool's --help is checked into this repository."
            if gan_executed else
            "a spec was supplied but every invocation failed; no GAN numbers "
            "are reported."
        ),
        "no_command_spec": (
            "PROTOCOL ONLY, NEVER EXECUTED: no GAN command spec was supplied "
            "and the tool's interface is undocumented in this repository, so "
            "no GAN-side number exists. Any GAN figure reported anywhere while "
            "this field says `no_command_spec` is fabricated."
        ),
        "skipped_by_flag": (
            "PROTOCOL ONLY, NEVER EXECUTED in this run (skip_gan=True — the "
            "CI default, for GPL-3.0 isolation). Combined with "
            "`no_command_spec`, this means the C-set has never been executed "
            "end-to-end in CI; only its plumbing is tested, against a fake "
            "executable."
        ),
    }[spec_status]

    per_stem = []
    for c in comparisons:
        gan_errors = sorted({r.error for r in c.gan_runs if r.error})
        prok_errors = sorted({r.error for r in c.prokname_runs if r.error})
        per_stem.append({
            "stem": c.stem,
            "etymology_type": c.etymology_type,
            "source_language": c.source_language,
            # Hoisted to the stem level as well: a side that never ran has to
            # say so where a reader looks first, instead of leaving an empty
            # candidate list that reads like "produced nothing".
            "errors": gan_errors + prok_errors,
            "gan": {
                "candidate_counts": c.gan_candidate_counts,
                "referee_compliant_counts": c.gan_compliant_counts,
                "errors": gan_errors,
                "cli_commands": sorted({r.cli_command for r in c.gan_runs if r.cli_command}),
                "sample_candidates": (
                    c.gan_runs[0].candidates[:5] if c.gan_runs else []
                ),
            },
            "prokname": {
                "candidate_counts": c.prokname_candidate_counts,
                "referee_compliant_counts": c.prokname_compliant_counts,
                "errors": prok_errors,
                "engine_self_reported_compliant_counts": [
                    None if r.error else sum(
                        1 for cand in r.candidates if cand["compliant"] is True
                    )
                    for r in c.prokname_runs
                ],
                "sample_candidates": (
                    c.prokname_runs[0].candidates[:5] if c.prokname_runs else []
                ),
            },
        })

    return {
        "suite": "C-set (GAN coverage comparison)",
        "benchmark_version": "0.2",
        "status": "executed" if gan_executed else "protocol_only_never_executed",
        "repetitions": repetitions,
        "base_seed": base_seed,
        "gan_invocation": spec_status,
        "gan_command_spec": list(spec) if spec else None,
        "gan_spec_rejection_policy": (
            "an unusable spec (empty, no {stem}, unknown placeholder) raises "
            "ValueError before any stem is attempted — the adapter never runs a "
            "command that could measure something else"
        ),
        "gan_executed": gan_executed,
        "gan_interface_provenance": (
            "UNRECORDED — telatin/gan (GPL-3.0) is not vendored and its CLI "
            "surface is documented nowhere in this repository, so the adapter "
            "requires an explicit user-supplied spec instead of guessing flags"
        ),
        "protocol_executed_note": executed_note,
        "gan_isolation_note": (
            "GAN (GPL-3.0) is invoked via CLI subprocess only, with the user's "
            "own installation. No GAN source code is copied or imported. If "
            "GAN is absent or the call fails, the report says so and omits the "
            "GAN-side numbers rather than substituting anything."
        ),
        "total_stems": total,
        "feasibility_rate": {
            "gan": _feasibility(gan_stats, gan_executed),
            "prokname": _feasibility(prok_stats, executed=True),
            "gan_stems_excluded_from_denominator": (
                sum(1 for run in gan_stats_by_stem if run is None)
                if gan_executed else None
            ),
            "exclusion_note": (
                "a stem whose GAN runs all failed is dropped from the GAN "
                "denominator and listed per stem under `errors`; it is not "
                "scored as a stem GAN failed to cover"
                if gan_executed else
                "no GAN side ran, so there is no GAN denominator at all"
            ),
            "definitions": {
                "shape_only": "stem has >=1 candidate passing the orthographic "
                              "guard (capitalisation + ASCII). This is the "
                              "ONLY thing the pre-fix 'independent referee' "
                              "checked, so it is reported as a weak check, not "
                              "as compliance",
                "agreement_refereed": "stem has >=1 candidate the real "
                                      "validator explicitly accepts (True); a "
                                      "`not_assessed` verdict never counts",
            },
            "note": "a null agreement_refereed rate means the referee had "
                    "nothing it could decide at this rank — it does NOT mean "
                    "0 % compliance",
            "denominator_warning": (
                f"computed over {total} stems; one stem moves the rate by "
                f"{(1 / total if total else 0):.4f}"
            ),
        },
        "candidate_counts": {
            "gan": gan_counts_report,
            "prokname": _variance_report(comparisons, "prokname", deterministic=True),
            "note": "the two variance sources are reported separately "
                    "(between_stem vs within_stem_run_to_run); the old pooled "
                    "mean ± std mixed them",
        },
        "compliance_referee": {
            "function": "gan_compare.compliance_referee",
            "stages": ["orthographic_guard", "not_assessed_for_genus_rank",
                       "prokname.engine.validate.validate_agreement"],
            "honours_etymology_type": True,
            "person_gender_assumed": ASSUMED_PERSON_GENDER,
            "person_gender_note": (
                "person genitives cannot be judged without the sex of the "
                f"honoured person; the C-set carries none, so both sides are "
                f"judged as if it were `{ASSUMED_PERSON_GENDER}` — the "
                "assumption is symmetric, but it does decide those verdicts"
            ),
            "independent": False,
            "referee_limitations": (
                "Stage 1 alone (capitalisation + ASCII) was the previous "
                "'referee' and could not discriminate compliance at all. Stage "
                "2 now applies the real agreement rules, but it is prokname's "
                "own validator, so the referee is NOT independent of one "
                "competitor; genus-rank single-token candidates are reported "
                "as `not_assessed` rather than as passes, and person genitives "
                "rest on an assumed gender. Paper-grade use requires two human "
                "annotators with Cohen's κ."
            ),
        },
        "per_stem_results": per_stem,
        "note": (
            "v0.1 seed for CI and development. Paper-grade C-set: >=200 "
            "etymologies, a recorded GAN CLI interface (M0) plus a real GAN "
            "run, human annotators for independent compliance, Jaccard "
            "similarity on genus candidates, and the species-level boundary "
            "experiment."
        ),
    }
