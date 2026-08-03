"""Static analysis for pymake task graphs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .digest import severed_commit_wrapper
from .resolver import CyclicDependencyError, DependencyResolver
from .staleness import FLIP_WARN_THRESHOLD, KIND_NOUNS
from .state import DEFAULT_STATE_DIR, StateStore
from .task import Task, TaskRegistry


@dataclass
class Issue:
    """A problem found during static analysis."""

    severity: str  # "error" or "warning"
    task: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.task}: {self.message}"


class Doctor:
    """Static analyzer for task dependency graphs."""

    def __init__(
        self, registry: TaskRegistry, state_dir: str | Path | None = None
    ) -> None:
        self.registry = registry
        self.resolver = DependencyResolver(registry)
        self.state = StateStore(
            state_dir if state_dir is not None else DEFAULT_STATE_DIR
        )

    def check_all(self, target: Task | None = None, sweep: bool = False) -> list[Issue]:
        """Run all checks and collect issues.

        If target is provided, only check tasks reachable from that target.
        Otherwise, check all tasks.

        ``sweep=True`` adds the repo-wide sweeps the ``doctor`` command
        runs: flip-counter aggregation across recorded task states, and the
        migration-time severed-wrapper scan. Divergence and nondeterminism
        already warn inline during runs — doctor only aggregates what needs
        cross-run history.
        """
        issues: list[Issue] = []

        if target:
            try:
                tasks = self.resolver.resolve(target)
            except CyclicDependencyError as e:
                issues.append(Issue("error", target.name, str(e)))
                return issues
        else:
            tasks = list(self.registry.all_tasks())
            # Check for cycles in all tasks
            issues.extend(self._check_cycles(tasks))

        issues.extend(self._check_unproducible_inputs(tasks))

        if sweep:
            issues.extend(self._check_severed_run_if(tasks))
            issues.extend(self._check_flip_counters(tasks))

        return issues

    def _check_severed_run_if(self, tasks: list[Task]) -> list[Issue]:
        """The severed-wrapper signature on legacy run_if predicates."""
        issues: list[Issue] = []
        for task in tasks:
            digest = severed_commit_wrapper(task.run_if)
            if digest is not None:
                issues.append(
                    Issue(
                        "warning",
                        task.name,
                        f"run_if wraps a TreeDigest ({digest.digest_path}) "
                        "but exposes no .commit — the digest never settles "
                        "and the task re-runs forever; pass the digest's "
                        ".changed directly, or migrate to "
                        "inputs=[git(...)/value(...)]",
                    )
                )
        return issues

    def _check_flip_counters(self, tasks: list[Task]) -> list[Issue]:
        """Aggregate recorded flip counters across task states."""
        issues: list[Issue] = []
        for task in tasks:
            record = self.state.load(task.name)
            if record is None:
                continue
            for kind, counters in sorted(record.flips.items()):
                for name, count in sorted(counters.items()):
                    if count < FLIP_WARN_THRESHOLD:
                        continue
                    issues.append(
                        Issue(
                            "warning",
                            task.name,
                            f"{KIND_NOUNS[kind]} {name} has changed on every "
                            f"one of the last {count} runs — nondeterministic "
                            "value or self-mutating task?",
                        )
                    )
        return issues

    def _check_cycles(self, tasks: list[Task]) -> list[Issue]:
        """Check for cyclic dependencies."""
        issues: list[Issue] = []
        checked: set[str] = set()

        for task in tasks:
            if task.name in checked:
                continue
            try:
                resolved = self.resolver.resolve(task)
                checked.update(t.name for t in resolved)
            except CyclicDependencyError as e:
                issues.append(Issue("error", task.name, str(e)))
                checked.add(task.name)

        return issues

    def _check_unproducible_inputs(self, tasks: list[Task]) -> list[Issue]:
        """Check for inputs that don't exist and no task produces."""
        issues: list[Issue] = []
        seen: set[tuple[str, str]] = set()

        for task in tasks:
            for input_path in task.inputs:
                key = (task.name, str(input_path))
                if key in seen:
                    continue
                seen.add(key)

                if not input_path.exists():
                    producing_task = self.registry.by_output(input_path)
                    if not producing_task:
                        # Unresolved "Ns.method" task references get their own
                        # wording — they are typos or unregistered groups,
                        # not missing files.
                        message = task.ref_hint(input_path) or (
                            f"input '{input_path}' does not exist "
                            "and no task produces it"
                        )
                        issues.append(Issue("error", task.name, message))

        return issues
