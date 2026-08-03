"""Fingerprint computation for the executor's staleness decision.

Staleness is one rule, with nothing to interleave and nothing severable:

    run = cli_force or fingerprints_changed or outputs_missing or no_record

Fingerprints are kind-partitioned into three namespaces — ``paths``
(declared file inputs, ``mtime_ns:size``), ``deps`` (a dependency's
declared outputs' fingerprints, so a consumer sees when a dep's artifact
actually changed), and ``inputs`` (Input objects, by id). Records are
per-task (see :mod:`pymake.state`); computation is per-run — one
fingerprint evaluation per Input id per invocation, memoized in
:class:`FingerprintCache`.
"""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

from .inputs import Input
from .state import KINDS, TaskState
from .task import Task, TaskRegistry

#: Human noun for each fingerprint kind, used in decision lines and warnings.
KIND_NOUNS = {"paths": "path", "deps": "dep", "inputs": "input"}

#: Warn once an input has changed on this many consecutive recorded runs —
#: the empirical signature of a nondeterministic value or a self-mutating
#: task.
FLIP_WARN_THRESHOLD = 3


def path_fingerprint(path: Path) -> str:
    """``mtime_ns:size`` of *path*, or ``"missing"``."""
    try:
        st = path.stat()
    except OSError:
        return "missing"
    return f"{st.st_mtime_ns}:{st.st_size}"


def outputs_fingerprint(task: Task) -> str:
    """Combined fingerprint of a task's declared outputs.

    This is what a task-ref input contributes to its consumer's staleness:
    the consumer re-runs when the dep's artifact actually changed, without
    hand-listing the artifact's path a second time. A task with no outputs
    contributes a constant.
    """
    return ";".join(f"{out.as_posix()}={path_fingerprint(out)}" for out in task.outputs)


class FingerprintCache:
    """Per-run memoization of Input-object fingerprints, keyed by id.

    Records are per-task, but the computation is shared: one walk per input
    id per invocation. ``fresh=True`` bypasses and refreshes the memo — the
    post-run recording walk must observe the CURRENT world, not the cached
    pre-run one.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()

    def fingerprint(self, obj: Input, *, fresh: bool = False) -> str:
        if not fresh:
            with self._lock:
                cached = self._cache.get(obj.id)
            if cached is not None:
                return cached
        # Compute outside the lock: a fingerprint may shell out (git).
        fp = obj.fingerprint()
        with self._lock:
            self._cache[obj.id] = fp
        return fp


@dataclasses.dataclass
class Snapshot:
    """Current fingerprints of one task's inputs, kind-partitioned."""

    paths: dict[str, str] = dataclasses.field(default_factory=dict)
    deps: dict[str, str] = dataclasses.field(default_factory=dict)
    inputs: dict[str, str] = dataclasses.field(default_factory=dict)

    def kind(self, kind: str) -> dict[str, str]:
        result: dict[str, str] = getattr(self, kind)
        return result


def take_snapshot(
    task: Task,
    registry: TaskRegistry,
    cache: FingerprintCache,
    *,
    fresh: bool = False,
) -> Snapshot:
    """Fingerprint all of *task*'s inputs as the world stands right now."""
    paths = {p.as_posix(): path_fingerprint(p) for p in task.inputs}

    deps: dict[str, str] = {}
    for dep_name in task.depends:
        dep = registry.get(dep_name)
        deps[dep_name] = outputs_fingerprint(dep) if dep is not None else ""

    inputs = {obj.id: cache.fingerprint(obj, fresh=fresh) for obj in task.input_objects}
    return Snapshot(paths=paths, deps=deps, inputs=inputs)


@dataclasses.dataclass(frozen=True)
class Change:
    """One fingerprint difference between a record and a snapshot."""

    kind: str  # "paths" | "deps" | "inputs"
    name: str
    what: str  # "changed" | "added" | "removed"

    def describe(self) -> str:
        noun = KIND_NOUNS[self.kind]
        if self.kind == "deps" and self.what == "changed":
            return f"dep {self.name} outputs changed"
        return f"{noun} {self.name} {self.what}"


def diff_record(record: TaskState, snap: Snapshot) -> list[Change]:
    """All differences between the recorded and current fingerprints.

    Added/removed keys count as changes too: editing a task's input list
    re-triggers it.
    """
    changes: list[Change] = []
    for kind in KINDS:
        old = record.fingerprints(kind)
        new = snap.kind(kind)
        for name, fp in new.items():
            if name not in old:
                changes.append(Change(kind, name, "added"))
            elif old[name] != fp:
                changes.append(Change(kind, name, "changed"))
        for name in old:
            if name not in new:
                changes.append(Change(kind, name, "removed"))
    return changes


def change_reason(changes: list[Change]) -> str:
    """Decision-line reason naming the first change (and counting the rest)."""
    reason = changes[0].describe()
    if len(changes) > 1:
        reason += f", +{len(changes) - 1} more"
    return reason
