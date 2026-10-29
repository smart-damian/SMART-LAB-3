"""Save and load the alert memory (small JSON files, one per timeframe).

One file per timeframe is deliberate: only that timeframe's run ever WRITES it
(other runs may READ it). With a single writer per file, two jobs running at
the same moment can never overwrite each other's update.

A missing or damaged file is never fatal: we start empty. The worst case is
one repeated alert, which is far better than a crashed job that sends nothing.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from alerts import empty_state


def state_path(state_dir: str | Path, tf_name: str) -> Path:
    return Path(state_dir) / f"{tf_name}.json"


def load_state(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return empty_state()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        print(f"Warning: could not read {path}; starting with empty state.", file=sys.stderr)
        return empty_state()
    if (not isinstance(data, dict) or not isinstance(data.get("zones", {}), dict)
            or not isinstance(data.get("confluence", {}), dict)):
        print(f"Warning: {path} has an unexpected shape; starting with empty state.", file=sys.stderr)
        return empty_state()
    state = empty_state()
    state.update(data)
    return state


def save_state(path: str | Path, state: dict) -> None:
    """Write atomically: write a temp file, then rename it over the real one.
    If the process dies half-way, the old file is intact instead of half-written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, path)
