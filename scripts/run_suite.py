#!/usr/bin/env python3
"""Run the whole suite and report pass/fail counts from JUnit XML.

The project's pytest addopts already carry `-q`, so passing another `-q` makes
the summary line disappear entirely — a `grep passed` on such output finds
nothing and *looks* like a green run. Parse the machine-readable report instead.

    .venv/bin/python scripts/run_suite.py            # one run
    .venv/bin/python scripts/run_suite.py --repeat 3 # hunt an intermittent
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def once(index: int) -> tuple[int, int, int, list[str]]:
    report = REPO / f".suite-{index}.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", f"--junitxml={report.name}",
         "--tb=short"],
        cwd=REPO, capture_output=True, text=True, check=False,
        env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"})
    if not report.exists():
        # pytest died before writing a report (INTERNALERROR, bad interpreter,
        # collection crash). Silent zero is how a broken run looks green.
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
        raise SystemExit(
            f"pytest produced no JUnit report (exit {proc.returncode}); "
            "last output:\n  " + "\n  ".join(tail))
    suite = ET.parse(report).getroot()
    cases = list(suite.iter("testcase"))
    failed = [f"{c.get('classname')}.{c.get('name')}"
              for c in cases
              if c.find("failure") is not None or c.find("error") is not None]
    skipped = sum(1 for c in cases if c.find("skipped") is not None)
    report.unlink(missing_ok=True)
    if suite.find("failure") is not None or suite.get("errors") not in (None, "0"):
        pass  # collection-level errors show up as <error> cases above
    return len(cases) - skipped - len(failed), skipped, len(failed), failed


def main() -> int:
    # Console code pages are not a safe assumption for redirected output;
    # see prokname.diagnostics.ensure_reportable_output.
    from prokname.diagnostics import ensure_reportable_output
    ensure_reportable_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    worst = 0
    for i in range(1, args.repeat + 1):
        passed, skipped, failed, names = once(i)
        worst = max(worst, failed)
        print(f"run {i}: {passed} passed, {skipped} skipped, {failed} failed")
        for name in names:
            print("   FAILED", name)
        sys.stdout.flush()
    if worst:
        print(f"UNSTABLE: {worst} failure(s) seen across {args.repeat} run(s)")
        return 1
    print("suite green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
