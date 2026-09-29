"""prokname CLI.

Subcommands + POSIX-style flags; dual output modes (human-readable tables via
rich, machine-readable via --json); stable exit codes:

0  success / no conflict (possibly with warnings)
1  runtime error (bad input handled by a command, corrupt data, ...)
2  usage / argument error (raised by the CLI framework itself)
3  check verdict: blocked (authority unavailable)
4  check verdict: conflict (authority reports the name is taken)

Exit 2 is consumed by typer/click for argument errors, so the conflict
verdict MUST stay on 4 — a script could otherwise not tell "typo" from
"name already published".
"""

from __future__ import annotations

import contextvars
import enum
import json as jsonlib

import typer
from rich.console import Console
from rich.table import Table

from . import DISCLAIMER, __version__, diagnostics
from .benchmark import (
    DEFAULT_B1_TARGET_FNR,
    DEFAULT_MIN_ABSTENTION_CASES,
    DEFAULT_MIN_NEGATIVES_PER_BRANCH,
    check_holdout,
    run_full_benchmark,
)
from .dedup import Verdict, check_name
from .engine import data as data_assets
from .engine.bench import run_bench
from .engine.gender import Gender
from .engine.generate import generate
from .presentation.decision import role_style, verdict_style
from .routing import RouteSource
from .routing import route as route_paths
from .storage import Candidate, ProjectLoadError, ProjectStore

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    # invoke_without_command lets the root callback handle `--version`
    # (newer typer/click fails with "Missing command." otherwise).
    invoke_without_command=True,
    help="Prokaryotic nomenclature assistant (ICNP / SeqCode). "
         "Decision support only — name validity is decided by formal publication.",
)
diagnostics.ensure_reportable_output()


console = Console()

EXIT_ERROR, EXIT_BLOCKED, EXIT_CONFLICT = 1, 3, 4


class NearMatchMode(str, enum.Enum):
    """Local parahomonym scan caliber (see dedup.nearmatch)."""

    whole = "whole"
    stem = "stem"
    both = "both"


PROJECT_ACTIONS = ("list", "create", "add", "show", "rate", "export", "delete")

_json_mode: contextvars.ContextVar = contextvars.ContextVar(
    "prokname_json_mode", default=False
)


def _emit(payload: dict, human_render, *, exit_code: int = 0) -> None:
    if _json_mode.get():
        payload = {**payload, "disclaimer": DISCLAIMER}
        typer.echo(jsonlib.dumps(payload, ensure_ascii=False, indent=2))
    else:
        human_render()
        console.print(f"[dim]{DISCLAIMER}[/dim]")
    raise typer.Exit(exit_code)


def _set_json(json_out: bool) -> None:
    _json_mode.set(json_out)


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", help="Show version and exit."
    ),
):
    if version:
        console.print(f"prokname {__version__}")
        raise typer.Exit()


@app.command()
def gen(
    stem: str = typer.Option(..., help="Etymology stem, e.g. 'Wukong', 'Boyd', 'Beijing'."),
    type: str = typer.Option("feature", "--type", help="place | person | thing | feature."),
    rank: str = typer.Option(
        "species",
        help="phylum|class|subclass|order|suborder|family|subfamily|tribe|"
             "subtribe|genus|species|subspecies.",
    ),
    genus: str = typer.Option(
        None, "--genus",
        help="Target genus (species/subspecies); for subspecies a full binomial "
             "like 'Bacillus subtilis'.",
    ),
    person_gender: str = typer.Option(
        None, "--person-gender",
        help="Gender of the HONOURED PERSON (male|female) — required for "
             "person etymologies; NOT the genus gender.",
    ),
    gender: str = typer.Option(
        None, "--gender", help="Override genus gender (m|f|n) after expert review."
    ),
    genus_suffix: str = typer.Option(
        None, "--genus-suffix", help="Optional suggestive genus ending (e.g. monas)."
    ),
    adjective_formation: str = typer.Option(
        None, "--adjective-formation",
        help="place|second_declension|third_declension|loving|nourishing.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Machine-readable JSON output (scripts/pipelines)."
    ),
):
    """Generate candidate names from an etymology stem."""
    _set_json(json_out)
    try:
        candidates = generate(
            stem,
            type,
            rank,
            genus=genus,
            person_gender=person_gender,
            gender_override=Gender(gender) if gender else None,
            genus_suffix=genus_suffix,
            adjective_formation=adjective_formation,
        )
    except (ValueError, KeyError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from exc

    payload = {
        "command": "gen",
        "input": {
            "stem": stem, "type": type, "rank": rank, "genus": genus,
            "person_gender": person_gender, "gender_override": gender,
            "adjective_formation": adjective_formation,
        },
        "candidates": [c.as_dict() for c in candidates],
    }

    def render() -> None:
        table = Table(title=f"Candidates for stem '{stem}' (rank: {rank})")
        table.add_column("name", style="bold")
        table.add_column("category")
        table.add_column("gender")
        table.add_column("compliant")
        table.add_column("derivation / warnings", overflow="fold")
        for c in candidates:
            comp = (
                "[green]yes[/green]" if c.compliant
                else "[red]no[/red]" if c.compliant is False
                else "[yellow]review[/yellow]"
            )
            note = c.derivation
            if c.warnings:
                note += "\n[yellow]" + "\n".join(f"⚠ {w}" for w in c.warnings) + "[/yellow]"
            table.add_row(
                c.name, c.grammatical_category or "-", c.gender or "-", comp, note
            )
        console.print(table)

    _emit(payload, render)


@app.command()
def check(
    name: list[str] = typer.Argument(
        ..., help="Candidate name, e.g. 'Wukomonas beijingensis'. The words of "
             "an unquoted binomial are joined, so  check Wukomonas beijingensis  "
             "and  check \"Wukomonas beijingensis\"  are equivalent.",
    ),
    online: bool = typer.Option(
        False, "--online", help="Query authority APIs (needs credentials; M0-gated)."
    ),
    debug: bool = typer.Option(
        False, "--debug",
        help="Echo the third-party client output prokname swallows (LPSN "
             "retries, rejected queries, token failures) to stderr. Distinguishes "
             "'LPSN answered not found' from 'the request never succeeded', "
             "which the exit code alone cannot. Changes no verdict. Also "
             "PROKNAME_DEBUG=1.",
    ),
    no_near_match: bool = typer.Option(
        False, "--no-near-match", help="Skip the local parahomonym scan."
    ),
    max_distance: int = typer.Option(
        2, "--max-distance", min=0, max=4,
        help="Near-match edit-distance threshold (default 2: catches the "
             "classic -ensis/-ense confusables).",
    ),
    near_match_mode: NearMatchMode = typer.Option(
        NearMatchMode.whole, "--near-match-mode",
        help="Parahomonym scan caliber: whole (v1, full-name distance ≤ "
             "--max-distance) | stem (v2, inflectional endings stripped, "
             "distance ≤ 1, Taxamatch-style) | both (union of the two).",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Machine-readable JSON output (scripts/pipelines)."
    ),
):
    """Two-tier dedup check: authority status + local near-match scan."""
    _set_json(json_out)
    diagnostics.configure(debug)
    full_name = " ".join(name)  # tolerate an unquoted binomial
    report = check_name(
        full_name, online=online, near_match=not no_near_match,
        max_distance=max_distance, near_match_mode=near_match_mode.value,
    )
    exit_code = {
        Verdict.CONFLICT: EXIT_CONFLICT,
        Verdict.BLOCKED: EXIT_BLOCKED,
    }.get(report.verdict, 0)
    def render() -> None:
        # One palette for both entry points: the verdict colour comes from
        # prokname.presentation.decision, the same map Studio renders from.
        # This block used to carry its own Verdict→rich-colour dict, which
        # drifted from Studio's (BLOCKED was "bold yellow" here and
        # COLOR_DANGER_BRIGHT there) and had no fail-safe for a verdict the
        # CLI had never seen — a new Verdict member raised KeyError at print
        # time, i.e. the tool crashed on the output of a name it checked fine.
        style = verdict_style(report.verdict)
        console.print(
            f"verdict: [{style.color}]{report.verdict.value}[/]"
            f"  (checked at {report.checked_at})"
        )
        if report.verdict is Verdict.BLOCKED:
            console.print(
                "[dim]exit code 3 = authorities unavailable (adjudication "
                "blocked); any local near-match hits below are warnings, "
                "not a ruling[/dim]"
            )
        table = Table(title="sources")
        table.add_column("source")
        table.add_column("tier")
        table.add_column("status")
        table.add_column("detail", overflow="fold")
        for s in report.sources:
            color = "green" if s.status == "not_found" else (
                "red" if s.status.startswith("found") else "yellow"
            )
            table.add_row(s.name, s.tier, f"[{color}]{s.status}[/]", s.detail)
        console.print(table)
        if report.near_matches:
            caliber = (
                (report.near_match_corpus or {}).get("scan_mode", "whole")
            )
            caliber_note = {
                "whole": f"whole-name ≤{max_distance} edits",
                "stem": "stemmed ≤1 edit (suffixes stripped)",
                "both": f"union: whole-name ≤{max_distance} / stemmed ≤1",
            }.get(caliber, f"whole-name ≤{max_distance} edits")
            nm = Table(title=f"near matches — {caliber_note}")
            nm.add_column("corpus name")
            nm.add_column(f"distance ({caliber})")
            nm.add_column("source")
            for m in report.near_matches:
                nm.add_row(m.corpus_name, str(m.distance), m.source)
            console.print(nm)
        for w in report.warnings:
            console.print(f"[yellow]⚠ {w}[/yellow]")

    _emit(report.as_dict(), render, exit_code=exit_code)


def _route_style(role: str | None) -> str:
    """Rich style for a router role, resolved through the shared decision map.

    ``prokname.presentation.decision`` is the single place that decides
    what a role looks like: ``default`` is the only green, ``only-viable`` is a
    *constraint* (MAG/SAG ⇒ SeqCode is the sole channel) and therefore renders
    as a warning, and a role it has never heard of fails safe to the muted
    "unknown" token.  The CLI used to hard-code
    ``"bold green" if role in ("default", "only-viable") else "cyan"``, which
    both read a constrained path as a success and let any newly added role
    inherit "recommended" here while Studio showed it as unknown.
    """
    return f"bold {role_style(role).color}"


@app.command()
def route(
    source: RouteSource = typer.Option(
        ..., "--source", help="pure_culture | MAG | SAG | unknown."
    ),
    candidatus: bool = typer.Option(False, "--candidatus"),
    icnp_occupied: str = typer.Option(
        "auto", "--icnp-occupied",
        help="auto (not checked) | yes | no — from `prokname check` against LPSN.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Machine-readable JSON output (scripts/pipelines)."
    ),
):
    """Route between ICNP and SeqCode; outputs viable paths + trade-offs."""
    _set_json(json_out)
    occupied = {"auto": None, "yes": True, "no": False}.get(icnp_occupied)
    if occupied is None and icnp_occupied != "auto":
        console.print("[red]error:[/red] --icnp-occupied must be auto|yes|no")
        raise typer.Exit(EXIT_ERROR)
    result = route_paths(source, candidatus=candidatus, icnp_occupied=occupied)

    def render() -> None:
        for p in result.viable_paths:
            console.print(
                f"[{_route_style(p.role)}]{p.code}[/] ({p.role})"
            )
            for t in p.tradeoffs:
                console.print(f"  • {t}")
        for w in result.warnings:
            console.print(f"[yellow]⚠ {w}[/yellow]")
        for n in result.notes:
            console.print(f"[dim]note: {n}[/dim]")

    _emit(result.as_dict(), render)


BENCH_OUTPUT_DEFAULT = "benchmark_results.json"


def _fmt(m: dict) -> str:
    """Render a metric dict as `value [interval] (n) — interval_label`, never
    bare: the number and the kind of interval it carries are printed
    together so an accuracy CI can never be read as a macro-F1 CI.
    """
    val = m.get("value")
    label = m.get("interval_label") or m.get("interval_method") or ""
    if val is None:
        return f"n/a [dim]({label or 'not computable'})[/dim]"
    txt = f"{val:.4f}"
    if m.get("interval"):
        txt += (
            f" [{m['interval']['lower']:.4f}, {m['interval']['upper']:.4f}]"
        )
    if m.get("n") is not None:
        txt += f" (n={m['n']}" + (
            f", {m['events']} events)" if m.get("events") is not None else ")"
        )
    if label:
        txt += f" [dim]— {label}[/dim]"
    return txt


def _interval_kind(m: dict) -> str:
    return m.get("interval_method", "none")


BENCH_OUTPUT_MARKER = "@@__BENCH_OUTPUT_PROVENANCE__@@"


def _bench_report_text(report: dict) -> str:
    """Serialise a bench report for disk: pretty JSON, provenance on one line.

    The provenance block is the only part of the artefact that legitimately
    varies between two runs of the same code and data (it names the file it
    was written to), so it is kept compact and carries the run stamp next to
    the path.  Everything else — every metric, interval and gate field — must
    be byte-identical across runs, which is what makes the artefact quotable.
    """
    provenance = report["bench_output"]
    report["bench_output"] = BENCH_OUTPUT_MARKER
    try:
        text = jsonlib.dumps(
            report, ensure_ascii=False, indent=2, sort_keys=True
        )
    finally:
        report["bench_output"] = provenance
    return text.replace(
        jsonlib.dumps(BENCH_OUTPUT_MARKER),
        jsonlib.dumps(provenance, ensure_ascii=False, sort_keys=True),
    )


def _write_bench_output(report: dict, output: str) -> dict:
    """Write the benchmark report to disk deterministically (review: evidence).

    Nothing in the repository used to persist these numbers, so a reviewer
    could not check them.  The file is written with sorted keys and a trailing
    newline; apart from the top-level ``run_at`` stamp the bytes are
    reproducible for a given code+data state (bootstrap seeds are fixed).

    The provenance block is injected *before* serialisation so the JSON on
    stdout and the JSON on disk are byte-identical.
    """
    import subprocess
    from pathlib import Path

    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    ignored: bool | None = None
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--quiet", str(path)],
            cwd=path.parent, capture_output=True, timeout=10,
        )
        ignored = proc.returncode == 0
    except OSError:
        ignored = None
    # The artefact is meant to leave the machine it was built on (attached to a
    # release, handed to an auditor), so it names itself relative to wherever the
    # command was run from. An absolute path here would carry a username.
    try:
        shown_path = str(path.relative_to(Path.cwd()))
    except ValueError:
        shown_path = str(path)
    provenance = {
        "path": shown_path,
        # the run stamp travels with the path so that the single line which
        # legitimately differs between two artefacts is the "when/where
        # written" line, and nothing else (see _bench_report_text)
        "run_at": report.get("run_at"),
        "written": True,
        "git_ignored": ignored,
        "note": (
            "this path is git-ignored: an auditor will not see the evidence "
            "unless it is force-added or written elsewhere"
            if ignored else
            "commit this file (or attach it to the release) so the reported "
            "numbers are checkable"
            if ignored is False else
            "written; git availability unknown (not a git checkout?)"
        ),
    }
    report["bench_output"] = provenance
    path.write_text(
        _bench_report_text(report) + "\n",
        encoding="utf-8",
    )
    return provenance


@app.command()
def bench(
    full: bool = typer.Option(
        False, "--full",
        help="Run the full benchmark suite (A/B/C/D sets with baselines and CIs).",
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON report."),
    output: str = typer.Option(
        BENCH_OUTPUT_DEFAULT, "--output", "-o",
        help="With --full: write the machine-readable report here (default "
             f"{BENCH_OUTPUT_DEFAULT} in the working directory; use "
             "--no-output to skip writing).",
    ),
    no_output: bool = typer.Option(
        False, "--no-output", help="With --full: do not write a result file.",
    ),
    require_complete: bool = typer.Option(
        False, "--require-complete",
        help="Milestone mode: also fail when a gate's target cannot be tested "
             "with the shipped data (status `insufficient_evidence`). By "
             "default that debt is reported loudly but does not fail the run, "
             "so a permanent red CI cannot hide a real regression. Neither "
             "mode ever certifies an untested target.",
    ),
    bootstrap_resamples: int = typer.Option(
        2000, "--bootstrap-resamples", min=100,
        help="Case-level bootstrap resamples for the macro-F1 intervals.",
    ),
    bootstrap_seed: int = typer.Option(
        42, "--bootstrap-seed", help="Bootstrap seed (keeps output reproducible)."
    ),
    b1_min_negatives: int = typer.Option(
        DEFAULT_MIN_NEGATIVES_PER_BRANCH, "--b1-min-negatives", min=0,
        help="B1 gate: minimum negative (violation) cases each etymology_type "
             "branch must contain for the miss-rate target to be certifiable. "
             "0 reinstates the vacuous pass and is itself reported as a "
             "disclosure.",
    ),
    b1_target_fnr: float = typer.Option(
        DEFAULT_B1_TARGET_FNR, "--b1-target-fnr", min=0.0, max=1.0,
        help="B1 gate: the miss-rate target being tested (default 0.01, the "
             "value documented in USAGE §6). A relaxed value is echoed in "
             "gate.target_fnr and thresholds.target_fnr.",
    ),
    min_abstention_cases: int = typer.Option(
        DEFAULT_MIN_ABSTENTION_CASES, "--min-abstention-cases", min=1,
        help="A-set: cases with a true `unknown` label needed before the "
             "abstention metrics count as evidence.",
    ),
    gan_command: str = typer.Option(
        "", "--gan-command",
        help="C-set: explicit GAN CLI command spec, e.g. "
             "\"gan gen {stem} --seed {seed}\". Required for any GAN number to "
             "exist at all — the adapter refuses to guess a competing tool's "
             "flags.",
    ),
    gan_repetitions: int = typer.Option(
        3, "--gan-repetitions", min=1,
        help="C-set: repetitions per stem (fairness protocol asks for >=3).",
    ),
):
    """Run the built-in engine regression seed or the full benchmark suite.

    ``--full`` also writes ``benchmark_results.json`` (``--output``) so the
    numbers quoted from this run have a checkable artifact behind them, and
    gates the exit code on holdout integrity, the B1 miss-rate target and D
    ICNP-preemption correctness.  Two outcomes are kept distinct, because
    collapsing them made every push red without saying anything about the code:

    * a gate the engine **violated** fails the run, always;
    * a gate whose target the shipped data **cannot test**
      (``insufficient_evidence``) is reported as evidence debt and fails only
      under ``--require-complete``, the milestone mode.

    Neither mode certifies an untested target: the debt is printed, carried in
    the JSON, and the claim stays unavailable.
    """
    _set_json(json_out)
    if full:
        try:
            report = run_full_benchmark(
                n_resamples=bootstrap_resamples,
                seed=bootstrap_seed,
                min_negatives_per_branch=b1_min_negatives,
                target_fnr=b1_target_fnr,
                min_abstention_cases=min_abstention_cases,
                repetitions=gan_repetitions,
                skip_gan=not gan_command,
                gan_command=gan_command or None,
                require_complete=require_complete,
            )
        except ValueError as exc:
            # A rejected GAN command spec is a caller error: running a command
            # that does not take the stem would produce numbers about
            # something else entirely, so the suite refuses instead of
            # reporting a hollow comparison.
            console.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(EXIT_ERROR) from exc
        gates_block = report["gates_summary"]
        gates = gates_block["gates"]
        exit_code = 0 if gates_block["passed"] else EXIT_ERROR

        # The disclaimer travels with the report so the JSON on stdout and the
        # JSON on disk are the same payload (review: quotable evidence).
        report["disclaimer"] = DISCLAIMER
        bench_output = None if no_output else _write_bench_output(report, output)

        def render() -> None:
            a = report["a_set"]
            ai = a["a_inference"]
            console.print("[bold]A-set: gender determination[/bold]")
            console.print(
                f"  lookup coverage: {_fmt(a['a_lookup']['coverage'])} "
                "[dim](data-asset metric, not accuracy)[/]"
            )
            console.print(f"  inference cases: {a['inference_cases']}")
            for key, label in (
                ("engine", "engine"),
                ("majority_class_baseline", "majority baseline"),
                ("naive_ending_baseline", "naive-ending baseline"),
            ):
                s = ai[key]
                dec = s["decidable_subset"]
                console.print(
                    f"  {label}: decidable macro-F1={_fmt(dec['macro_f1'])} "
                    f"raw acc={_fmt(s['accuracy'])} "
                    f"refusal-aware acc={_fmt(s['refusal_aware_accuracy'])}"
                )
                console.print(
                    f"    [dim]refusal-aware macro-F1={_fmt(s['refusal_aware_macro_f1'])}; "
                    f"needs_review={_fmt(s['needs_review_rate'])}[/dim]"
                )
            eng = ai["engine"]
            console.print(
                f"  [dim]interval kinds: macro-F1="
                f"{_interval_kind(eng['decidable_subset']['macro_f1'])}, "
                f"accuracy={_interval_kind(eng['accuracy'])} — the two are "
                f"different intervals and belong to different metrics[/dim]"
            )
            console.print(
                f"  [dim]raw true-domain macro-F1 (diagnostic only, see JSON "
                f"note): {eng['raw_true_domain_macro_f1']['value']:.4f}[/dim]"
            )
            if ai.get("engine_error_cases"):
                console.print(
                    f"[red]  engine raised on {len(ai['engine_error_cases'])} "
                    "A-set case(s)[/] (scored as refusals, listed in the JSON)"
                )
            ae = eng["abstention_evidence"]
            if ae["insufficient_evidence"]:
                console.print(
                    f"[yellow]  abstention evidence: only "
                    f"{ae['true_unknown_cases']} case(s) with a true `unknown` "
                    f"label ({', '.join(ae['true_unknown_genera']) or '-'}); "
                    f"below {ae['min_cases_for_evidence']} — cannot tell "
                    "'abstains correctly' from 'never abstains'[/]"
                )
            for base in ("majority_class_baseline", "naive_ending_baseline"):
                sig = ai["significance_vs_baselines"][f"engine_vs_{base}"]
                acc = sig[f"engine_vs_{base}_accuracy"]
                mf1 = sig[f"engine_vs_{base}_macro_f1"]
                console.print(
                    f"  paired vs {base}: Δacc={acc['point_difference']:+.4f} "
                    f"CI[{acc['difference_interval']['lower']:.4f}, "
                    f"{acc['difference_interval']['upper']:.4f}] "
                    f"McNemar p={acc['mcnemar_exact']['p_value']:.3g}; "
                    f"Δmacro-F1={mf1['point_difference']:+.4f} "
                    f"CI[{mf1['difference_interval']['lower']:.4f}, "
                    f"{mf1['difference_interval']['upper']:.4f}]"
                )
            h = a["holdout_check"]
            hcolor = "green" if h["passed"] else "red"
            console.print(f"  holdout: [{hcolor}]{h['message']}[/]")

            console.print()
            console.print("[bold]B1-set: agreement validation[/bold]")
            b1 = report["b1_set"]
            console.print(
                f"  accuracy (conservative, abstention != correct): "
                f"{_fmt(b1['accuracy'])}"
            )
            console.print(
                f"  accuracy among verdicts issued: "
                f"{_fmt(b1['accuracy_among_verdicts_issued'])}; "
                f"verdict coverage {_fmt(b1['verdict_coverage'])}"
            )
            console.print(
                f"  miss rate (abstention counted as a miss): "
                f"{_fmt(b1['false_negative_rate'])}"
            )
            fnr = b1["false_negative_rate"]
            if fnr.get("one_sided_upper_bound_label"):
                console.print(
                    f"    [yellow]{fnr['one_sided_upper_bound_label']}[/yellow]"
                )
            console.print(
                f"  missed violations: "
                f"{b1['gate']['missed_violation_cases']}/"
                f"{b1['negative_cases']} negative cases — the gate requires "
                f"zero (no_missed_violations="
                f"{b1['gate']['no_missed_violations']}); abstentions on "
                "violations count as misses"
            )
            console.print(
                "  negatives per etymology_type: "
                + ", ".join(
                    f"{t}={v['negative_cases']}"
                    for t, v in b1["negatives_per_etymology_type"].items()
                )
                + f" (gate floor {b1['gate']['min_negatives_per_branch']}"
                + (
                    " — DISABLED: vacuous pass allowed"
                    if b1["gate"]["min_negatives_per_branch"] == 0 else ""
                )
                + f"; {b1['negatives_required_to_test_target']} needed for a "
                + f"{b1['target_fnr']:.0%} claim)"
            )
            console.print(
                f"  unverified (engine has no rule there, e.g. `thing` "
                f"genitives): {b1['structurally_unverifiable_cases']['count']}"
            )
            gcolor = "green" if b1["gate"]["passed"] else "red"
            console.print(
                f"  gate b1_fnr: [{gcolor}]{b1['gate']['status']}[/] "
                f"passed={b1['gate']['passed']}"
            )
            for reason in b1["gate"]["reasons"]:
                console.print(f"    [dim]why: {reason}[/dim]")
            console.print(f"    [dim]{b1['gate']['expansion_required']}[/dim]")

            console.print()
            console.print("[bold]B2-set: generation exact-match[/bold]")
            b2 = report["b2_set"]
            console.print(f"  exact-match {_fmt(b2['exact_match_rate'])}")
            console.print(f"  top-3 hit   {_fmt(b2['top3_hit_rate'])}")

            console.print()
            console.print("[bold]C-set: GAN coverage comparison[/bold]")
            c = report["c_set"]
            ccolor = "green" if c["gan_executed"] else "yellow"
            console.print(
                f"  [{ccolor}]{c['status']}[/] (invocation: "
                f"{c['gan_invocation']}; repetitions={c['repetitions']})"
            )
            console.print(f"  [dim]{c['protocol_executed_note']}[/dim]")
            console.print(
                f"  prokname feasibility shape-only="
                f"{(c['feasibility_rate']['prokname'] or {}).get('shape_only')} "
                f"agreement-refereed="
                f"{(c['feasibility_rate']['prokname'] or {}).get('agreement_refereed')} "
                f"(not_assessed="
                f"{(c['feasibility_rate']['prokname'] or {}).get('candidates_not_assessed')})"
            )
            console.print(
                f"  gan feasibility: {c['feasibility_rate']['gan']}"
            )
            console.print(
                "  [dim]candidate counts — between-stem (input difficulty) vs "
                "within-stem run-to-run (repeat noise) are reported "
                "separately; see JSON[/dim]"
            )

            console.print()
            console.print("[bold]D-set: dual-code routing[/bold]")
            d = report["d_set"]
            console.print(f"  accuracy {_fmt(d['accuracy'])}")
            ic = d["icnp_preemption"]
            iccolor = "green" if ic["meets_target"] else "red"
            console.print(
                f"  ICNP preemption: [{iccolor}]{ic['correct']}/{ic['total']} "
                f"= {_fmt(ic['accuracy'])}[/] (target: 100%, hard per-case "
                f"constraint)"
            )
            if d["engine_error_cases"]:
                console.print(
                    f"[red]  routing raised on {len(d['engine_error_cases'])} "
                    "case(s)[/]"
                )

            debt = gates_block.get("evidence_debt") or []
            console.print(
                "\n[bold]gates[/bold]: "
                + ", ".join(
                    # A gate that merely did not fail is never printed as a
                    # bare "pass": untested reads as `pass(NOT CERTIFIED)`.
                    f"[{'green' if ok else 'red'}]{k}="
                    + ("pass" if ok else "FAIL")
                    + ("(NOT CERTIFIED)" if k in debt else "")
                    + "[/]"
                    for k, ok in sorted(gates.items())
                )
            )
            failed = gates_block["failed"]
            if failed:
                console.print(
                    f"\n[red]gate FAILED:[/red] {', '.join(failed)} "
                    "(non-zero exit)"
                )
                console.print(f"[dim]{gates_block['note']}[/dim]")
            elif debt:
                console.print(
                    f"\n[yellow]gates passed, but {', '.join(debt)} "
                    "is NOT certified:[/] the shipped set cannot test its "
                    "target. The claim stays unmade — add expert-labelled "
                    "negative cases, do not read this as a pass. "
                    "Run with --require-complete (milestone mode) to make the "
                    "debt fail."
                )
                console.print(
                    f"[dim]{gates_block['b1_gate_detail']['expansion_required']}[/dim]"
                )
            else:
                console.print("\n[green]all gates passed, all targets certified[/green]")
            if bench_output:
                console.print(
                    f"[dim]report written: {bench_output['path']} — "
                    f"{bench_output['note']}[/dim]"
                )
            console.print(f"\n[dim]{report['note']}[/dim]")

        _emit(report, render, exit_code=exit_code)
    else:
        report = run_bench()
        exit_code = 0 if report["failed"] == 0 else EXIT_ERROR

        def render() -> None:
            color = "green" if report["failed"] == 0 else "red"
            console.print(
                f"[{color}]passed {report['passed']}/{report['total']}[/] "
                f"({report['suite']})"
            )
            for r in report["results"]:
                if not r["passed"]:
                    console.print(f"[red]FAIL[/red] {jsonlib.dumps(r, ensure_ascii=False)}")
            console.print(f"[dim]{report['note']}[/dim]")

        _emit(report, render, exit_code=exit_code)


@app.command()
def holdout(
    json_out: bool = typer.Option(False, "--json", help="JSON report."),
) -> None:
    """Check holdout integrity: A-set ∩ published lexicon must be empty."""
    _set_json(json_out)
    report = check_holdout()
    exit_code = 0 if report["passed"] else EXIT_ERROR

    def render() -> None:
        color = "green" if report["passed"] else "red"
        console.print(f"[{color}]{report['message']}[/]")
        if report["violations"]:
            console.print(f"violations: {report['violations']}")

    _emit(report, render, exit_code=exit_code)


@app.command()
def data(json_out: bool = typer.Option(
    False, "--json", help="Machine-readable JSON output (scripts/pipelines)."
)) -> None:
    """Show versions, gating status and whether each packaged asset is live.

    "Shipped" and "used" are different claims, and this command used to print
    only the version — which let `stems.json` and `rate_limit_budget.json` look
    like working configuration when no engine code consumed them.
    Each row now says who reads it, how many of its assertions are still
    unverified, and how to make a change take effect.
    """
    _set_json(json_out)
    status = data_assets.asset_status()
    hooks = data_assets.invalidate_hooks()

    def render() -> None:
        # Four columns, not five: a free-text "status" column made the table
        # wider than an 80-column terminal, and rich then wrapped the *file
        # names*, so `prokname data` on a laptop hid the very thing it lists.
        # The long status prose moves below the table, per asset.
        table = Table(title="data assets")
        table.add_column("file", no_wrap=True)
        table.add_column("version")
        table.add_column("consumed by", overflow="fold")
        table.add_column("unverified", justify="right")
        notes: list[tuple[str, str]] = []
        for filename, info in status.items():
            if not info.get("present"):
                table.add_row(filename, "-", "[red]unreadable[/red]", "-")
                notes.append((filename, str(info.get("error", ""))))
                continue
            unverified = info.get("unverified_rows")
            if unverified is None:
                mark = "[dim]n/a[/dim]"
            elif unverified == 0:
                mark = "[green]0[/green]"
            else:
                mark = f"[yellow]{unverified}[/yellow]"
            consumed = info.get("consumed_by", "unknown")
            style = ("dim" if consumed.startswith("NOT CONNECTED")
                     else "yellow" if consumed.startswith("PARTIAL") else "")
            table.add_row(
                filename, info.get("version") or "-",
                f"[{style}]{consumed}[/{style}]" if style else consumed, mark)
            detail = info.get("status", "")
            if detail:
                notes.append((filename, detail))
        console.print(table)
        for filename, detail in notes:
            console.print(f"  [dim]{filename}:[/dim] {detail}")
        seq = status.get("seqcode_registered.json", {})
        if seq.get("present"):
            console.print(
                f"[dim]SeqCode occupancy snapshot: {seq.get('names_recorded')} "
                f"names, retrieved {seq.get('retrieved_at')}; rebuild with "
                "`python scripts/build_seqcode_snapshot.py`[/dim]"
            )
        console.print(
            f"[dim]assets are returned read-only, and {len(hooks)} derived "
            f"cache(s) follow a reload"
            f"{': ' + ', '.join(hooks) if hooks else ''}. After replacing a "
            "file, call prokname.reload_data() — no restart needed.[/dim]"
        )
        unverified_total = sum(
            v.get("unverified_rows") or 0 for v in status.values()
        )
        if unverified_total:
            console.print(
                f"[yellow]{unverified_total} asset assertion(s) remain "
                "unverified; M0 sign-off is what flips them, and until then "
                "rules built on them report needs_review.[/yellow]"
            )

    _emit({"assets": status,
           "invalidate_hooks": list(hooks),
           "reload_entry_point": "prokname.reload_data()"}, render)


@app.command()
def config(
    offline_snapshot: str = typer.Option(
        None, "--offline-snapshot",
        help="Path to a local LPSN official export directory (FR-10 offline mode); "
             "persisted to the prokname config file.",
    ),
    taxdump_dir: str = typer.Option(
        None, "--taxdump-dir",
        help="Path to a local NCBI taxdump directory; persisted to the prokname "
             "config file.",
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Clear persisted settings (taxdump/snapshot paths)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Machine-readable JSON output (scripts/pipelines)."
    ),
) -> None:
    """LPSN credential management and offline data import (FR-10)."""
    import os

    _set_json(json_out)
    persisted: dict = {}

    if clear:
        from . import config as config_mod

        removed = config_mod.clear_config()
        persisted["cleared"] = removed

    if offline_snapshot:
        # FR-10: import local LPSN official export (via registered downloads channel)
        path = os.path.expanduser(offline_snapshot)
        if not os.path.isdir(path):
            console.print(f"[red]error:[/red] snapshot directory not found: {path}")
            raise typer.Exit(EXIT_ERROR)
        # Persist for future invocations (env var still takes precedence)
        from . import config as config_mod

        persisted["lpsn_snapshot"] = config_mod.save_config(
            {"lpsn_snapshot": path}
        )["lpsn_snapshot"]
        if not json_out:
            console.print(
                "[yellow]note:[/yellow] the LPSN offline snapshot is a "
                "reserved FR-10 setting — no adapter consumes it yet "
                "(M0 deliverable); check/route behaviour is unchanged."
            )

    if taxdump_dir:
        path = os.path.expanduser(taxdump_dir)
        if not os.path.isdir(path):
            console.print(f"[red]error:[/red] taxdump directory not found: {path}")
            raise typer.Exit(EXIT_ERROR)
        from . import config as config_mod

        persisted["taxdump_dir"] = config_mod.save_config(
            {"taxdump_dir": path}
        )["taxdump_dir"]

    if persisted and not json_out:
        console.print("[green]saved to prokname config:[/green] " +
                      ", ".join(f"{k}={v}" for k, v in sorted(persisted.items())
                                if k != "cleared"))

    env_user = os.environ.get("PROKNAME_LPSN_USER")
    env_snapshot = os.environ.get("PROKNAME_LPSN_SNAPSHOT")
    env_taxdump = os.environ.get("PROKNAME_TAXDUMP_DIR")
    from . import config as config_mod

    cfg = config_mod.load_config()
    try:
        import keyring  # noqa: F401

        keyring_ok = True
    except Exception:
        keyring_ok = False

    payload = {
        "keyring_available": keyring_ok,
        "env": {
            "PROKNAME_LPSN_USER": bool(env_user),
            "PROKNAME_LPSN_SNAPSHOT": env_snapshot or cfg.get("lpsn_snapshot") or "",
            "PROKNAME_TAXDUMP_DIR": env_taxdump or cfg.get("taxdump_dir") or "",
        },
        "config_file": str(config_mod.config_path()),
        "config": cfg,
        "saved": persisted,
        "credentials_help": (
            "Store credentials via OS keyring (prokname service) or "
            "PROKNAME_LPSN_USER / PROKNAME_LPSN_PASSWORD environment variables. "
            "Free registration: https://api.lpsn.dsmz.de/"
        ),
    }

    def render() -> None:
        console.print(f"keyring available: {keyring_ok}")
        console.print(f"PROKNAME_LPSN_USER set: {bool(env_user)}")
        snapshot_set = bool(env_snapshot or cfg.get("lpsn_snapshot"))
        console.print(f"PROKNAME_LPSN_SNAPSHOT set: {snapshot_set}")
        if snapshot_set:
            console.print(
                "  [yellow](reserved: no adapter consumes the snapshot yet — M0)[/]"
            )
        console.print(
            f"PROKNAME_TAXDUMP_DIR set: {bool(env_taxdump or cfg.get('taxdump_dir'))}"
        )
        console.print(f"config file: {config_mod.config_path()}")
        console.print(payload["credentials_help"])

    _emit(payload, render)


@app.command()
def project(
    action: str = typer.Argument(
        ...,
        help="list | create | add | show | rate | export | delete",
    ),
    name: str = typer.Argument(
        None, help="Project name (for create/add/show/rate/export/delete)."
    ),
    stem: str = typer.Option(
        None, "--stem",
        help="Etymology stem (for 'add' action: generates and adds candidates).",
    ),
    etymology_type: str = typer.Option(
        "feature", "--type", help="place|person|thing|feature (for 'add')."
    ),
    rank: str = typer.Option("species", help="rank (for 'add')."),
    genus: str = typer.Option(None, "--genus", help="Genus (for 'add')."),
    person_gender: str = typer.Option(
        None, "--person-gender", help="male|female (for 'add' with person type)."
    ),
    score: int = typer.Option(0, "--score", help="0-5 rating (for 'rate' action)."),
    candidate: str = typer.Option(
        None, "--candidate",
        help="Candidate name to rate (for 'rate': prokname project rate "
             "<project> --candidate <name> --score N).",
    ),
    fmt: str = typer.Option(
        "json", "--format", help="json|csv|markdown (for 'export' action)."
    ),
    data_source: str = typer.Option(
        "", "--data-source", help="pure_culture|MAG|SAG (for 'create')."
    ),
    target_code: str = typer.Option(
        "", "--target-code", help="ICNP|SeqCode|both (for 'create')."
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
) -> None:
    """Candidate management: create, add, rate, list, export (FR-08)."""
    _set_json(json_out)
    store = ProjectStore()

    if action == "list":
        names = store.list_projects()
        def render_list() -> None:
            if not names:
                console.print("[dim]no projects found[/dim]")
                return
            t = Table(title="projects")
            t.add_column("name")
            for n in names:
                t.add_row(n)
            console.print(t)
        _emit({"projects": names}, render_list)
        return

    if action == "create":
        if not name:
            console.print("[red]error:[/red] project name required")
            raise typer.Exit(EXIT_ERROR)
        project_obj = store.create(name, data_source=data_source, target_code=target_code)
        def render_create() -> None:
            console.print(f"[green]created project:[/green] {project_obj.name}")
        _emit(project_obj.as_dict(), render_create)
        return

    if action == "add":
        if not name or not stem:
            console.print("[red]error:[/red] project name and --stem required for 'add'")
            raise typer.Exit(EXIT_ERROR)
        try:
            candidates = generate(
                stem, etymology_type, rank, genus=genus, person_gender=person_gender,
            )
        except (ValueError, KeyError) as exc:
            console.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(EXIT_ERROR) from exc
        try:
            project_obj = store.load(name)
        except ProjectLoadError as exc:
            console.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(EXIT_ERROR) from exc
        if project_obj is None:
            project_obj = store.create(name)
        for c in candidates:
            stored = Candidate(
                name=c.name, epithet=c.epithet, rank=c.rank,
                grammatical_category=c.grammatical_category,
                gender=c.gender, derivation=c.derivation,
                compliant=c.compliant, warnings=c.warnings,
            )
            project_obj.candidates.append(stored)
        store.save(project_obj)
        def render_add() -> None:
            console.print(f"[green]added {len(candidates)} candidate(s) to {name}[/green]")
            for c in candidates:
                comp = "✓" if c.compliant else "✗" if c.compliant is False else "?"
                console.print(f"  {comp} {c.name}")
        _emit({
            "project": name, "added": len(candidates),
            "candidates": [c.as_dict() for c in candidates],
        }, render_add)
        return

    if action == "show":
        if not name:
            console.print("[red]error:[/red] project name required for 'show'")
            raise typer.Exit(EXIT_ERROR)
        try:
            project_obj = store.load(name)
        except ProjectLoadError as exc:
            console.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(EXIT_ERROR) from exc
        if project_obj is None:
            console.print(f"[red]error:[/red] project {name!r} not found")
            raise typer.Exit(EXIT_ERROR)
        def render_show() -> None:
            console.print(f"[bold]{project_obj.name}[/bold] "
                          f"({len(project_obj.candidates)} candidates)")
            t = Table(title="candidates")
            t.add_column("name", style="bold")
            t.add_column("category")
            t.add_column("compliant")
            t.add_column("score")
            for c in project_obj.candidates:
                comp = "✓" if c.compliant else "✗" if c.compliant is False else "?"
                t.add_row(c.name, c.grammatical_category or "-", comp, str(c.score))
            console.print(t)
        _emit(project_obj.as_dict(), render_show)
        return

    if action == "rate":
        # 'rate <project> --candidate <name> --score N'
        if not name or not candidate:
            console.print(
                "[red]error:[/red] usage: prokname project rate <project> "
                "--candidate <name> --score N"
            )
            raise typer.Exit(EXIT_ERROR)
        try:
            project_obj = store.rate_candidate(name, candidate, score)
        except (FileNotFoundError, ValueError, ProjectLoadError) as exc:
            console.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(EXIT_ERROR) from exc
        rated = next(
            (c for c in project_obj.candidates if c.name == candidate), None
        )
        if rated is None:
            # `rate_candidate()` raises ValueError for an unknown
            # candidate, so this used to be an unreachable `else` that fell
            # back to echoing the *requested* score as if it had been stored.
            # It is now an explicit inconsistency error: the CLI never reports
            # a rating it did not read back from the store.
            console.print(
                f"[red]error:[/red] internal inconsistency: project {name!r} "
                f"saved but candidate {candidate!r} is not in it — the rating "
                "was NOT confirmed"
            )
            raise typer.Exit(EXIT_ERROR)

        def render_rate() -> None:
            console.print(
                f"[green]rated:[/green] {candidate} in project '{name}' → "
                f"{rated.score}/5"
            )

        _emit(
            {
                "project": name,
                "candidate": candidate,
                "score": rated.score,
            },
            render_rate,
        )
        return

    if action == "export":
        if not name:
            console.print("[red]error:[/red] project name required for 'export'")
            raise typer.Exit(EXIT_ERROR)
        if fmt not in ("json", "csv", "markdown"):
            console.print(
                f"[red]error:[/red] unknown --format {fmt!r}; use json|csv|markdown"
            )
            raise typer.Exit(EXIT_ERROR)
        try:
            project_obj = store.load(name)
        except ProjectLoadError as exc:
            console.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(EXIT_ERROR) from exc
        if project_obj is None:
            console.print(f"[red]error:[/red] project {name!r} not found")
            raise typer.Exit(EXIT_ERROR)
        if fmt == "csv":
            content = store.export_csv(name)
        elif fmt == "markdown":
            content = store.export_markdown(name)
        else:
            content = store.export_json(name)

        def render_export() -> None:
            console.print(content)

        # Human mode renders the export verbatim; --json wraps it
        # ({"project", "format", "content", "disclaimer"}) so CSV/Markdown
        # payloads stay machine-readable.
        payload = {"project": name, "format": fmt, "content": content}
        _emit(payload, render_export)
        return

    if action == "delete":
        if not name:
            console.print("[red]error:[/red] project name required for 'delete'")
            raise typer.Exit(EXIT_ERROR)
        deleted = store.delete(name)
        def render_del() -> None:
            if deleted:
                console.print(f"[green]deleted:[/green] {name}")
            else:
                console.print(f"[red]not found:[/red] {name}")
        _emit({"deleted": deleted, "name": name}, render_del)
        return

    console.print(
        f"[red]error:[/red] unknown action {action!r}; "
        f"use {'|'.join(PROJECT_ACTIONS)}"
    )
    raise typer.Exit(EXIT_ERROR)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
