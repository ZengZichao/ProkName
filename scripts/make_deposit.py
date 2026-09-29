#!/usr/bin/env python3
"""Build an archival release bundle of the data this tool ships with.

prokname's deliverable includes versioned rule tables and annotated benchmark sets, so a
release is only reproducible if those artefacts can be handed to an archive (Zenodo,
figshare, an institutional repository) as one snapshot rather than re-derived from a git
checkout by whoever needs them. This script assembles that snapshot.

    python3 scripts/make_deposit.py --out ../ProkName-数据存档包

Contents written:
    rule_assets/       src/prokname/data/*.json          (versioned nomenclatural rules)
    benchmark_sets/    src/prokname/benchmark/data/*.json (annotated cases + provenance)
    corpus/            corpus_seed.json + build script    (de-duplication corpus)
    results/           benchmark_results.json             (one gated full-benchmark run)
    code/              sha256 manifest of src/ + tests/   (so the snapshot is checkable)
    DEPOSIT.md         what this is, licence terms, version, run command, DOI placeholders

No network access, no deletion, idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "src" / "prokname" / "data"
BENCH = REPO / "src" / "prokname" / "benchmark" / "data"
SCRIPTS = REPO / "scripts"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_tree(files, dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for f in sorted(files):
        shutil.copy2(f, dest / f.name)
        written.append(f.name)
    return written


def run_full_benchmark(dest_results: Path) -> dict:
    """Execute the gated full benchmark once and store its machine-readable output."""
    env = {"PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"}
    py = sys.executable
    proc = subprocess.run(
        [py, "-m", "prokname.cli", "bench", "--full", "--json"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=900,
    )
    dest_results.mkdir(parents=True, exist_ok=True)
    payload = proc.stdout.strip()
    ok = proc.returncode in (0, 1) and payload.startswith(("{", "["))
    (dest_results / "benchmark_results.json").write_text(payload or "{}\n")
    (dest_results / "bench_stderr.txt").write_text(proc.stderr)
    return {"captured": ok, "returncode": proc.returncode, "bytes": len(payload)}


def manifest(root: Path, patterns=("*.py",)) -> list[dict]:
    out = []
    for f in sorted(root.rglob("*")):
        if f.is_file() and any(f.match(p) for p in patterns) and "__pycache__" not in f.parts:
            out.append(
                {
                    "path": str(f.relative_to(REPO)),
                    "sha256": sha256(f),
                    "bytes": f.stat().st_size,
                }
            )
    return out


def main() -> int:
    # Console code pages are not a safe assumption for redirected output;
    # see prokname.diagnostics.ensure_reportable_output.
    from prokname.diagnostics import ensure_reportable_output
    ensure_reportable_output()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO.parent / "ProkName-数据存档包"))
    ap.add_argument("--skip-bench", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from prokname import __version__  # noqa: E402  (needs PYTHONPATH=src)

    rules = copy_tree(DATA.glob("*.json"), out / "rule_assets")
    sets_ = copy_tree(BENCH.glob("*.json"), out / "benchmark_sets")
    copy_tree([DATA / "corpus_seed.json", SCRIPTS / "rebuild_corpus.py"], out / "corpus")
    bench_info = {"skipped": True}
    if not args.skip_bench:
        bench_info = run_full_benchmark(out / "results")

    man = {"src": manifest(REPO / "src"), "tests": manifest(REPO / "tests")}
    (out / "code").mkdir(exist_ok=True)
    (out / "code" / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    n_code_files = len(man["src"]) + len(man["tests"])

    notes = f"""# ProkName research-data deposit package (v{__version__})

Generated: {datetime.now(UTC).isoformat(timespec='seconds')}
Built by: `python3 scripts/make_deposit.py` (deterministic; no network access).

## What is in here
| Path | Contents | Files |
|---|---|---|
| `rule_assets/` | versioned nomenclatural rule and lexicon JSON | {len(rules)} |
| `benchmark_sets/` | annotated benchmark cases A/B1/B2/C/D + per-case provenance | {len(sets_)} |
| `corpus/` | de-duplication corpus snapshot + its rebuild script | 2 |
| `results/` | one gated full-benchmark run (`benchmark_results.json`) | {bench_info} |
| `code/manifest.json` | sha256 of every shipped source and test file | {n_code_files} entries |

## Licence terms of the payload
* Code: MIT.
* `rule_assets/genus_gender.json` and LPSN-derived corpus rows: CC BY-SA 4.0 (attribution +
  ShareAlike) - see `DATA_LICENSE` in the code repository.
* `rule_assets/rules.json`, `gender_endings.json`, `person_genitive.json`, `stems.json`: CC0 1.0.
* `rule_assets/seqcode_registered.json`: **CC BY 4.0** — the SeqCode Registry publishes its
  contributed information under those terms (quoted in `DATA_LICENSE`, retrieved 2026-09-25),
  so this snapshot ships it with the Registry's attribution and the per-name `https://seqco.de/i:<id>`
  URIs retained in `_meta`.

## Before upload (maintainer actions)
1. Upload this directory to Zenodo (or figshare) and **reserve** a DOI. Do not publish
   before the M0 LPSN batch verification if the licence audit is unfinished.
2. Record the `concept DOI` + `version DOI` in `CITATION.cff`, so the archive record and
   the repository cite the same release.
3. Re-run `prokname bench --full --json` and confirm it reproduces `results/` byte for byte.

## Reproduction command
```
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/prokname bench --full --json > benchmark_results.json
.venv/bin/prokname holdout --json
```
"""
    (out / "DEPOSIT.md").write_text(notes)
    print(
        json.dumps(
            {
                "out": str(out),
                "rules": len(rules),
                "sets": len(sets_),
                "bench": bench_info,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "src"))
    raise SystemExit(main())
