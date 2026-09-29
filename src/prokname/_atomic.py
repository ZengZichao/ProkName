"""Shared atomic-write helpers.

One implementation for "never leave a half-written file behind", used by the
dedup cache, project storage, the persistent config, and Studio exports:

    mkstemp in the TARGET DIRECTORY → write → flush → os.fsync
    → os.replace → fsync the directory

A crash between write and replace leaves only an orphaned .tmp file, never a
half-written target; the fsyncs make the rename survive a power loss. All
writers must go through this module so the project keeps a single, auditable
write discipline.

Mode discipline
---------------
``tempfile.mkstemp`` creates its file with mode 0600. Replacing a target with
such a temp file silently *downgrades* an existing 0644 project/export file, so
collaborators and downstream tools lose read access (and a shared export becomes
unreadable to the co-authors it was produced for). :func:`mkstemp_for` therefore
pre-applies :func:`target_mode` to the temp file: the mode of the file being
overwritten, or 0644 masked by the process umask for a new file.

Every writer in the package goes through :func:`atomic_write_text` /
:func:`atomic_write_json` (project store, dedup cache, Studio exports). A writer
that genuinely needs a raw fd or binary mode should build its temp file with
:func:`mkstemp_for` rather than calling ``tempfile.mkstemp`` itself, so the mode
rule above cannot drift.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path

#: Mode a brand-new file gets before the process umask is applied.
DEFAULT_FILE_MODE = 0o644

_UMASK_LOCK = threading.Lock()
_UMASK_CACHE: int | None = None


def _process_umask() -> int:
    """The process umask, read once and cached (it never changes in-process).

    There is no non-destructive umask syscall: reading it requires a set/restore
    pair. The window is held under a lock and probes with the *most restrictive*
    mask, so any concurrent file creation in that window can only get fewer
    permissions than it would have had — never more.
    """
    global _UMASK_CACHE
    with _UMASK_LOCK:
        if _UMASK_CACHE is None:
            previous = os.umask(0o077)
            os.umask(previous)
            _UMASK_CACHE = previous
    return _UMASK_CACHE


def target_mode(path: Path | str) -> int:
    """Mode the finished file must carry.

    An existing file keeps its own mode (an overwrite must not quietly become a
    permission change); a new file gets 0644 minus the umask.
    """
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        return DEFAULT_FILE_MODE & ~_process_umask()


def mkstemp_for(
    path: Path | str,
    prefix: str | None = None,
    suffix: str = ".tmp",
) -> tuple[int, str]:
    """Create a temp file for an atomic write of ``path``.

    Returns ``(fd, tmp_name)`` like :func:`tempfile.mkstemp`, but the temp file
    lives in the target's directory (so ``os.replace`` stays on one filesystem)
    and already carries :func:`target_mode` instead of mkstemp's 0600.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target_mode(target)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=target.name if prefix is None else prefix,
        suffix=suffix,
    )
    try:
        os.fchmod(fd, mode)
    except AttributeError:
        # Windows has no os.fchmod, so the descriptor cannot be chmodded while it
        # is open. Chmod by name instead: there it only toggles the read-only bit,
        # which is enough for the guarantee that matters — a freshly written
        # project file must not be left at mkstemp's 0600.
        try:
            os.chmod(tmp_name, mode)
        except OSError:  # pragma: no cover — filesystem without chmod
            pass
    except OSError:  # pragma: no cover — filesystem without chmod
        os.close(fd)
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return fd, tmp_name


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync of a directory, so the rename itself is durable.

    Not supported on Windows; there the rename is durable enough for our
    purposes and swallowing the error keeps every platform writable.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover — platform dependent
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover — platform dependent
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    Temp file in the same directory → flush + ``os.fsync`` → ``os.replace`` →
    directory fsync. The target's permissions are preserved (or defaulted to
    0644 & ~umask), never silently downgraded to mkstemp's 0600.
    """
    target = Path(path)
    fd, tmp_name = mkstemp_for(target)
    committed = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        committed = True
    finally:
        if not committed:
            Path(tmp_name).unlink(missing_ok=True)
    _fsync_directory(target.parent)


def atomic_write_json(path: Path, payload: dict | list) -> None:
    """Write ``payload`` as pretty JSON atomically."""
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
