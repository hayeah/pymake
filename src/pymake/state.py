"""Per-task fingerprint state files.

Each task records the fingerprints of its inputs — kind-partitioned into
``paths`` / ``deps`` / ``inputs`` — in its own JSON file under the state
root (default ``.pymake/state/``). Records are per-TASK: two tasks sharing
one Input object each carry their own last-seen fingerprint, so one task
running never silently satisfies another task's staleness.

Writes are atomic (temp file + rename), like all pymake writes, which also
keeps parallel-executor writers contention-free. The whole directory is
``rm -rf``-able; the only cost of deleting it is one rebuild.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from pathlib import Path

STATE_VERSION = 1

KINDS = ("paths", "deps", "inputs")


@dataclasses.dataclass
class TaskState:
    """Recorded fingerprints (and flip counters) for one task."""

    paths: dict[str, str] = dataclasses.field(default_factory=dict)
    deps: dict[str, str] = dataclasses.field(default_factory=dict)
    inputs: dict[str, str] = dataclasses.field(default_factory=dict)
    # Per-input flip counters, kind-partitioned like the fingerprints:
    # incremented when the input changed since the last record, reset to 0
    # when it held still. Feeds the inline nondeterminism warning.
    flips: dict[str, dict[str, int]] = dataclasses.field(default_factory=dict)

    def fingerprints(self, kind: str) -> dict[str, str]:
        """The fingerprint map for *kind* ("paths" / "deps" / "inputs")."""
        result: dict[str, str] = getattr(self, kind)
        return result

    def flip_count(self, kind: str, name: str) -> int:
        return self.flips.get(kind, {}).get(name, 0)


def _sanitize(name: str) -> str:
    """Encode a task name into a safe, unambiguous file name."""
    out: list[str] = []
    for ch in name:
        if ch.isascii() and (ch.isalnum() or ch in "._-"):
            out.append(ch)
        else:
            out.append(f"%{ord(ch):04x}")
    return "".join(out)


def _str_map(data: object) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


class StateStore:
    """Loads and saves per-task :class:`TaskState` files under a root dir."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, task_name: str) -> Path:
        return self.root / f"{_sanitize(task_name)}.json"

    def load(self, task_name: str) -> TaskState | None:
        """The recorded state for *task_name*, or None.

        A missing, unreadable, or format-mismatched file all read as "no
        record" — the task runs and re-records, which is the loud, safe
        failure mode.
        """
        path = self.path_for(task_name)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            return None

        flips_raw = data.get("flips")
        flips: dict[str, dict[str, int]] = {}
        if isinstance(flips_raw, dict):
            for kind, counters in flips_raw.items():
                if kind in KINDS and isinstance(counters, dict):
                    flips[str(kind)] = {
                        str(k): int(v)
                        for k, v in counters.items()
                        if isinstance(v, int)
                    }

        return TaskState(
            paths=_str_map(data.get("paths")),
            deps=_str_map(data.get("deps")),
            inputs=_str_map(data.get("inputs")),
            flips=flips,
        )

    def save(self, task_name: str, state: TaskState) -> None:
        """Atomically write *state* (temp file + rename)."""
        path = self.path_for(task_name)
        payload = {
            "version": STATE_VERSION,
            "paths": state.paths,
            "deps": state.deps,
            "inputs": state.inputs,
            "flips": {k: v for k, v in state.flips.items() if v},
        }
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.root, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=1, sort_keys=True)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
