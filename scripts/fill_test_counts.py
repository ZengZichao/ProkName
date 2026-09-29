#!/usr/bin/env python3
"""Fill (or check) the `<!-- FINAL:TESTCOUNT -->` placeholder in the docs.

The documents quote a test count, and a hand-typed figure quietly goes
stale — the history here is literally "350, then 246, and neither matched either
environment". Rather than assert a number, the docs carry a placeholder and this
script generates the real one from the suite itself:

    python scripts/fill_test_counts.py           # rewrite the placeholders
    python scripts/fill_test_counts.py --check   # CI: fail if any remain
    python scripts/fill_test_counts.py --verify  # CI: also fail if stale

The count is measured, not asserted: it runs `pytest --collect-only` and uses
whatever the suite actually yields.

It used to measure *two* counts, because three Studio modules were gated on
PySide6 and `.[dev]` / `.[dev,gui]` collected different totals. ProkName Studio is
a separate project now, so this repository has one suite and one number; the
GUI's own count is generated the same way in that repository.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
#: The living documents: what a reader acts on today.
#:
#: CHANGELOG is deliberately absent. An entry there is a dated record of what the
#: suite collected on the day it was written; rewriting history to match today's
#: collection would make the entry false in the other direction. The change in the
#: count is itself recorded, in prose, by the entry that causes it.
DOCS = ["README.md", "README.zh.md", "USAGE.md", "USAGE.zh.md",
        "CONTRIBUTING.md", "CONTRIBUTING.zh.md"]
MARKER = "<!-- FINAL:TESTCOUNT -->"
PATTERN = re.compile(r"<!--\s*FINAL:TESTCOUNT\s*-->")


def _collect_only() -> int:
    """Run `pytest --collect-only` and read the count it reports."""
    out = subprocess.run(
        # No -q: at this project's default verbosity the summary line
        # ("N tests collected") is suppressed, so a -q parse yields nothing.
        [sys.executable, "-m", "pytest", "--collect-only",
         "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, check=False)
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    if not lines:
        raise SystemExit(f"collection produced no output:\n{out.stderr[-500:]}")
    for line in reversed(lines):
        match = re.search(r"(\d+)\s+tests?(?:/\d+)?\s+collected", line)
        if match:
            return int(match.group(1))
    raise SystemExit(
        "no 'N tests collected' summary found; last lines were:\n  "
        + "\n  ".join(lines[-3:])
        + f"\nstderr: {out.stderr[-300:]}")


#: Phrases that introduce a test count, in the languages these docs use.
#:
#: The cue-word form covers README/USAGE ("collects 958 tests", "收集 958 项").
#: The trailing-cue forms exist because CHANGELOG wrote "1061 with [gui], 954
#: without": the cue sits *after* the number there, so a cue-only pattern found
#: nothing and `--verify` passed over a stale figure. A checker that only sees one
#: spelling is a checker that only guards one file.
COUNT_PATTERNS = (
    re.compile(r"(?:collects|收集|为|→|即)\s*(\d{3,5})\s*(?:tests?|项)"),
    re.compile(r"(\d{3,5})\s+tests?\b"),
)


def _counts_in(text: str) -> set[int]:
    """Every documented count reachable in `text`, whatever spelling carries it."""
    found: set[int] = set()
    for pattern in COUNT_PATTERNS:
        for match in pattern.finditer(text):
            found.update(int(group) for group in match.groups() if group)
    return found


def _refresh_text(text: str, old: int, new: int) -> str:
    for pattern in COUNT_PATTERNS:
        text = pattern.sub(lambda m: m.group(0).replace(str(old), str(new)), text)
    return text


def _documented_counts() -> tuple[set[int], list[str]]:
    """Every count the docs currently quote, and which files quote them."""
    found: set[int] = set()
    names: list[str] = []
    for name in DOCS:
        path = REPO / name
        if not path.exists():
            continue
        hits = _counts_in(path.read_text(encoding="utf-8"))
        if hits:
            found |= hits
            names.append(name)
    return found, names


def _fill_placeholders(count: int) -> int:
    for name in DOCS:
        path = REPO / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if not PATTERN.search(text):
            continue
        # Bare number: each placeholder sits where a count belongs, with the unit
        # already written next to it in the document's own language
        # ("collects N tests", "收集 N 项"). Appending "tests" here produced
        # "1061 tests tests" in English and "1061 tests 项" in Chinese.
        path.write_text(text.replace(MARKER, str(count)), encoding="utf-8")
        print(f"filled {name}: {count} tests")
    return 0


def _rewrite(count: int) -> int:
    """Refresh counts the docs already quote, not only unfilled placeholders.

    Anything but exactly one distinct number is refused rather than guessed: a
    second figure means a count this script does not own was written by hand, and
    rewriting it blindly would launder that fact.
    """
    if any(PATTERN.search((REPO / n).read_text(encoding="utf-8"))
           for n in DOCS if (REPO / n).exists()):
        return _fill_placeholders(count)
    documented, names = _documented_counts()
    if len(documented) != 1:
        print(f"docs quote {sorted(documented)} — expected exactly one count. "
              "Restore a placeholder or fix the prose.", file=sys.stderr)
        return 1
    (old,) = documented
    if old == count:
        print(f"documentation test count already current ({count})")
        return 0
    for name in names:
        path = REPO / name
        path.write_text(
            _refresh_text(path.read_text(encoding="utf-8"), old, count),
            encoding="utf-8")
        print(f"rewrote {name}: {old}→{count}")
    return 0


def _verify() -> int:
    """Fail if a number written into the docs no longer matches reality.

    `--check` alone only catches a placeholder that was never filled. The subtler
    failure is a count that was right when generated and went stale the moment
    someone added a test file — which is how the old hand-typed "350, then 246"
    numbers came to disagree with both environments. So every count this script
    has written is re-found by context and compared.
    """
    count = _collect_only()
    stale = []
    for name in DOCS:
        path = REPO / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if PATTERN.search(text):
            stale.append(f"{name}: unfilled placeholder remains")
        for value in sorted(_counts_in(text)):
            if value != count:
                stale.append(f"{name}: quotes {value}, measured {count}")
    if stale:
        print("documentation test counts are stale:", file=sys.stderr)
        for line in dict.fromkeys(stale):
            print("  -", line, file=sys.stderr)
        print("run: python scripts/fill_test_counts.py", file=sys.stderr)
        return 1
    print(f"documentation test counts match reality ({count} collected)")
    return 0


def main() -> int:
    # Console code pages are not a safe assumption for redirected output;
    # see prokname.diagnostics.ensure_reportable_output.
    from prokname.diagnostics import ensure_reportable_output
    ensure_reportable_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if any placeholder remains")
    parser.add_argument("--verify", action="store_true",
                        help="also fail if a written-in count has gone stale")
    args = parser.parse_args()
    if args.verify:
        return _verify()
    if args.check:
        unfilled = [
            f"{name}: {len(PATTERN.findall(text))} placeholder(s)"
            for name in DOCS if (REPO / name).exists()
            and (text := (REPO / name).read_text(encoding="utf-8"))
            and PATTERN.search(text)
        ]
        if unfilled:
            print("unfilled documentation placeholders:", file=sys.stderr)
            for line in unfilled:
                print("  -", line, file=sys.stderr)
            print("run: python scripts/fill_test_counts.py", file=sys.stderr)
            return 1
        print("documentation test counts are filled in")
        return 0

    return _rewrite(_collect_only())


if __name__ == "__main__":
    raise SystemExit(main())
