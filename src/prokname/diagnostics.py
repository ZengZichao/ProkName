"""Diagnostics switch: the third-party client chatter we swallow, on demand.

This module exists because of one specific, reproducible support problem,
not as a logging framework.

``prokname check --online`` runs the official LPSN client inside
``contextlib.redirect_stdout`` (see ``dedup/lpsn.py``): it prints progress and
HTTP errors to stdout, and machine-readable output must not be polluted by it.
That is the right default. The consequence is that when an online check fails,
the *reason* is sometimes only in the text we discarded, and the user is left
with exit code 3 and a one-line summary — indistinguishable between "LPSN
answered: not found", "the query was rejected", and "authentication never
succeeded".

``--debug`` closes that gap without changing any default:

    prokname check "Bacillus subtilis" --online --debug

The rule this module keeps to deliberately:

* it is a *sink for already-captured diagnostics*, not a general logger. There
  is no `getLogger(__name__)` sprinkled through the engine, because an
  auditable decision-support tool's output contract is the table and the exit
  code, not log lines, and a logging habit grows into log text that users read
  as findings;
* nothing is buffered unboundedly. Captured text is truncated, and in the
  default (non-debug) mode it is dropped rather than accumulated, so a long
  pipeline cannot grow memory through a diagnostics path nobody reads;
* it never changes a verdict. Diagnostics observe; they do not rule.
"""

from __future__ import annotations

import os
import sys

#: Cap on how much swallowed output a single diagnostic may carry.
MAX_NOTE_CHARS = 4000

_enabled: bool | None = None
_notes_written = 0



def ensure_reportable_output() -> None:
    """Make piped or redirected output UTF-8, whatever the console code page is.

    The reports carry box-drawing characters and check-mark / warning glyphs. On a
    terminal that is fine — Windows renders a console through UTF-16 — but as soon as
    stdout is a pipe or a file, Python encodes with the locale code page (cp1252 on a
    default Windows install), where those characters have no byte at all. The result
    was a command dying with ``UnicodeEncodeError`` while writing the answer it had
    already computed: exit 1, no report. Scripting a CLI means redirecting it, so the
    transport is fixed here rather than by telling users to set PYTHONIOENCODING.

    Terminal streams are left alone: reconfiguring the console-backed writer would
    replace the UTF-16 path that makes the interactive case work, and an already
    UTF-capable stream needs nothing.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None or stream.isatty():
                continue
            encoding = str(getattr(stream, "encoding", "") or "")
        except (AttributeError, ValueError):  # pragma: no cover - closed stream
            continue
        normalised = encoding.lower().replace("-", "").replace("_", "")
        if normalised.startswith("utf"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):  # pragma: no cover - exotic stream
            pass

def _env_says_debug() -> bool:
    return os.environ.get("PROKNAME_DEBUG", "").strip().lower() in {
        "1", "true", "yes", "on"}


def configure(debug: bool | None = None) -> bool:
    """Turn diagnostics on or off; returns the resulting state.

    ``None`` means "decide from the environment", which is what lets a test
    runner or a container turn it on without threading a flag through every
    call site. Explicit True/False always wins.
    """
    global _enabled
    if debug is None:
        debug = _env_says_debug() if _enabled is None else _enabled
    _enabled = bool(debug)
    if _enabled:
        sys.stderr.write(
            "[prokname] diagnostics enabled: swallowed client output will be "
            "echoed to stderr. Verdicts and exit codes are unchanged.\n")
        sys.stderr.flush()
    return _enabled


def enabled() -> bool:
    return bool(_enabled)


def note(label: str, text: str = "", *, always: bool = False) -> None:
    """Emit one diagnostic line to stderr when diagnostics are on.

    ``always=True`` is reserved for messages the user must see regardless of
    mode; nothing in the dedup path uses it, because that path's contract is
    the SourceResult detail.
    """
    global _notes_written
    if not _enabled and not always:
        return
    body = (text or "").strip()
    if len(body) > MAX_NOTE_CHARS:
        body = body[:MAX_NOTE_CHARS] + f" …[{len(body) - MAX_NOTE_CHARS} more chars]"
    line = f"[prokname:{label}] {body}" if body else f"[prokname:{label}]"
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
        _notes_written += 1
    except (ValueError, OSError):  # pragma: no cover - closed/broken stderr
        pass


def notes_written() -> int:
    """How many diagnostics were emitted (a test seam, not a feature)."""
    return _notes_written
