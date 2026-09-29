"""Tests for the shared atomic-write discipline.

Headless: no Qt, no GUI extra needed.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from prokname import _atomic
from prokname._atomic import (
    DEFAULT_FILE_MODE,
    atomic_write_json,
    atomic_write_text,
    mkstemp_for,
)


def _umask() -> int:
    """Current process umask (the only way to read it is set/restore)."""
    previous = os.umask(0o077)
    os.umask(previous)
    return previous


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --------------------------------------------------------------------------- #
# Permission discipline
# --------------------------------------------------------------------------- #
# The mode contract asserted below is a POSIX contract: Windows has no per-owner
# mode bits — os.chmod there only toggles the read-only attribute — so comparing
# 0o600 against 0o644 would test nothing about this code. These cases run on Linux
# and macOS; skipping them elsewhere is about the platform not having the feature,
# not about an optional dependency being absent.
POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix",
    reason="no per-owner mode bits on this platform; the preserved-mode contract is POSIX-only",
)


@POSIX_ONLY
def test_new_file_defaults_to_0644_respecting_umask(tmp_path):
    target = tmp_path / "fresh.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    assert _mode(target) == DEFAULT_FILE_MODE & ~_umask()


@pytest.mark.parametrize("mode", [0o644, 0o604, 0o600, 0o755])
@POSIX_ONLY
def test_overwrite_preserves_existing_mode(tmp_path, mode):
    """An overwrite must never silently downgrade a shared 0644 file to 0600
    (mkstemp's default) — collaborators must keep being able to read it."""
    target = tmp_path / "project.json"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, mode)
    assert _mode(target) == mode

    atomic_write_text(target, "new content")

    assert target.read_text(encoding="utf-8") == "new content"
    assert _mode(target) == mode


@POSIX_ONLY
def test_json_writer_keeps_mode_too(tmp_path):
    target = tmp_path / "payload.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o640)
    atomic_write_json(target, {"a": 1})
    assert _mode(target) == 0o640


@POSIX_ONLY
def test_mkstemp_for_applies_target_mode(tmp_path):
    target = tmp_path / "sub" / "out.csv"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    os.chmod(target, 0o644)

    fd, tmp_name = mkstemp_for(target)
    try:
        assert Path(tmp_name).parent == target.parent  # same filesystem
        assert _mode(Path(tmp_name)) == 0o644
    finally:
        os.close(fd)
        Path(tmp_name).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# the declared contract: temp in same dir + fsync + os.replace
# --------------------------------------------------------------------------- #

def test_contract_temp_in_same_dir_fsync_then_replace(tmp_path, monkeypatch):
    target = tmp_path / "notes.md"
    target.write_text("original", encoding="utf-8")
    os.chmod(target, 0o644)

    calls: list[str] = []
    real_mkstemp = tempfile.mkstemp
    real_fsync = os.fsync
    real_replace = os.replace

    def spy_mkstemp(**kwargs):
        calls.append(f"mkstemp(dir={Path(kwargs['dir']).name})")
        return real_mkstemp(**kwargs)

    def spy_fsync(fd):
        calls.append("fsync")
        return real_fsync(fd)

    def spy_replace(src, dst):
        calls.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)
    monkeypatch.setattr(_atomic.os, "fsync", spy_fsync)
    monkeypatch.setattr(_atomic.os, "replace", spy_replace)

    atomic_write_text(target, "rewritten")

    assert calls[0].startswith("mkstemp(dir=")
    assert "fsync" in calls, "data must be fsynced before the rename"
    assert calls[-1] == "replace" or "replace" in calls
    assert calls.index("fsync") < calls.index("replace")
    assert target.read_text(encoding="utf-8") == "rewritten"
    if os.name == "posix":
        assert _mode(target) == 0o644
    # The second fsync is the *directory*, so the rename is durable. Windows
    # cannot open a directory that way and _fsync_directory says so by doing
    # nothing, which leaves the data fsync in place and the guarantee intact
    # for the platform. Only the data fsync is assertable there.
    assert calls.count("fsync") >= (2 if os.name == "posix" else 1)


def test_no_temp_file_is_left_behind(tmp_path):
    target = tmp_path / "clean.txt"
    atomic_write_text(target, "data")
    leftovers = [p.name for p in tmp_path.iterdir() if p != target]
    assert leftovers == []


def test_failed_replace_keeps_original_and_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / "deliverable.md"
    atomic_write_text(target, "good content")
    before = target.read_text(encoding="utf-8")

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(_atomic.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "truncated-ish new content")

    assert target.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("*.tmp")) == [], "temp file must not survive a failure"


def test_parent_directory_is_created(tmp_path):
    target = tmp_path / "deep" / "deeper" / "x.json"
    atomic_write_json(target, {"ok": True})
    assert target.is_file()
