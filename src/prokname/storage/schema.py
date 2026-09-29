"""Versioned project files, and the only path that reads one.

Why this exists. ProkName writes one JSON file per project into
``~/.config/prokname/projects/`` and those files are the user's working notes —
candidate names, etymologies, provenance, ratings. Until now nothing in the
payload said which structure it used, so the moment a field is renamed or its
meaning changes, the only possible behaviours are worse than each other: read
the old file and misinterpret it, or refuse to open it and lose the user's work
with a "corrupt file" error. There was no third option to reach for.

There is one now:

    {
      "schema_version": 1,
      "name": "...",
      ...
    }

* A file with no ``schema_version`` is version **0** — that is what is on
  users' disks today, and it is a real, supported version, not an error.
* Upgrades run one step at a time (0→1→2…), each a small named function, so a
  file can travel any distance and each hop stays reviewable.
* A file from the **future** is refused with an explicit message rather than
  partially read. Guessing at a structure that does not exist yet is exactly
  how a nomenclature tool ends up printing a confident wrong answer about a
  name.

The version is written on every save, so the round-trip is self-consistent;
``tests/test_storage_schema.py`` covers the migration table directly, because a
migration that no test reaches is a migration that will be deleted by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: The structure this build writes and understands natively.
SCHEMA_VERSION = 1

#: Files older than this cannot be upgraded because their migration was never
#: written (i.e. there is no such version yet). Bump only with a real reason.
MIN_SUPPORTED_VERSION = 0

KEY = "schema_version"

_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def migration(previous_version: int) -> Callable:
    """Register the step that upgrades ``previous_version`` by one.

    Decorator. Each function must be total: it receives the payload as read
    from disk (possibly with fields missing) and returns a payload valid for
    ``previous_version + 1``. It must not invent data — filling a field with a
    plausible value is how a project file starts lying about a name.
    """
    def _register(fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable:
        if previous_version in _MIGRATIONS:
            raise RuntimeError(
                f"duplicate migration for version {previous_version}")
        _MIGRATIONS[previous_version] = fn
        return fn

    return _register


def current_version() -> int:
    return SCHEMA_VERSION


def stamp(payload: dict[str, Any]) -> dict[str, Any]:
    """Return ``payload`` with the schema version this build writes.

    Inserted first rather than last: a reader that inspects the raw JSON (grep,
    a notebook, a diff in a bug report) should see the version at a glance.
    """
    return {KEY: SCHEMA_VERSION, **payload}


def read_version(payload: dict[str, Any]) -> int:
    """The version a payload declares; absent key means the pre-versioning 0.

    A non-integer or negative marker is refused rather than coerced: ``"1"`` as
    a string, or a float, means some other tool wrote this file, and guessing
    which shape to expect next is the failure mode this module exists to avoid.
    """
    if KEY not in payload:
        return 0
    raw = payload[KEY]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(
            f"{KEY} must be an integer, got {raw!r} "
            f"({type(raw).__name__}); this file was not written by prokname")
    if raw < 0:
        raise ValueError(f"{KEY} must be >= 0, got {raw}")
    return int(raw)


def upgrade(payload: dict[str, Any]) -> dict[str, Any]:
    """Bring an on-disk project payload up to ``SCHEMA_VERSION``.

    Raises ValueError for a file this build cannot understand, which
    ``ProjectStore.load`` surfaces as ProjectLoadError — the same channel every
    other malformed-file case uses, so callers keep one error type to handle.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"project file is not a JSON object: {type(payload).__name__}")
    version = read_version(payload)
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"project file declares {KEY}={version}, but this prokname build "
            f"understands at most {SCHEMA_VERSION}. Upgrade prokname rather "
            "than opening the file with an older version: reading a newer "
            "structure with the wrong assumptions can silently change what a "
            "candidate's provenance claims.")
    if version < MIN_SUPPORTED_VERSION:
        raise ValueError(
            f"project file declares {KEY}={version}, which this build no "
            f"longer migrates from (minimum supported "
            f"{MIN_SUPPORTED_VERSION}).")
    data = dict(payload)
    while version < SCHEMA_VERSION:
        step = _MIGRATIONS.get(version)
        if step is None:
            raise ValueError(
                f"no migration registered from {KEY}={version} to "
                f"{version + 1}; refusing to guess how to read this file")
        data = step(data)
        version = read_version(data) if KEY in data else version + 1
        data[KEY] = version
    return data


# ---------------------------------------------------------------------------
# The migration table
# ---------------------------------------------------------------------------

@migration(0)
def _from_unversioned(payload: dict[str, Any]) -> dict[str, Any]:
    """0 → 1: the shape that shipped in v0.1.0, plus the version marker.

    Field-for-field this is a no-op on purpose: version 1 *is* the pre-version
    layout, with ``schema_version`` added. Writing an upgrade that also
    "tidies" fields would make the first real migration impossible to review
    against a known-good baseline.
    """
    data = dict(payload)
    data[KEY] = 1
    return data
