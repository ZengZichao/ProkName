"""Persistent user configuration for prokname.

Environment variables (PROKNAME_TAXDUMP_DIR, PROKNAME_LPSN_SNAPSHOT, ...)
always take precedence; this module adds a persistent fallback so that
`prokname config --taxdump-dir ...` survives across CLI invocations.
Settings live in config.json under the prokname config directory
(XDG_CONFIG_HOME or ~/.config). No credentials are ever stored here —
LPSN passwords belong in the OS keyring.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ._atomic import atomic_write_json

CONFIG_FILENAME = "config.json"

# Keys permitted in the config file; anything else is dropped on save.
ALLOWED_KEYS = ("taxdump_dir", "lpsn_snapshot")


def config_dir() -> Path:
    """Directory holding the persistent config file."""
    base = os.environ.get(
        "XDG_CONFIG_HOME",
        os.path.join(os.path.expanduser("~"), ".config"),
    )
    return Path(base) / "prokname"


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def load_config() -> dict:
    """Load persistent settings; empty dict when absent/corrupt."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in ALLOWED_KEYS and v}


def save_config(settings: dict) -> dict:
    """Merge and persist settings; returns the resulting config."""
    merged = {**load_config(), **{k: v for k, v in settings.items() if v}}
    merged = {k: v for k, v in merged.items() if k in ALLOWED_KEYS and v}
    atomic_write_json(config_path(), merged)
    return merged


def clear_config(keys: tuple[str, ...] | None = None) -> int:
    """Remove settings (all, or the named keys); returns count removed."""
    current = load_config()
    if keys is None:
        removed = len(current)
        merged = {}
    else:
        merged = {k: v for k, v in current.items() if k not in keys}
        removed = len(current) - len(merged)
    if merged:
        atomic_write_json(config_path(), merged)
    elif config_path().exists():
        config_path().unlink()
    return removed


def resolved_setting(key: str, env_var: str) -> str | None:
    """Resolve a setting: environment variable first, config file second."""
    env = os.environ.get(env_var)
    if env:
        return env
    return load_config().get(key)
