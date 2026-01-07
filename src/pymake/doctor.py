"""Static analysis for pymake task graphs."""

from __future__ import annotations

from dataclasses import dataclass

from .resolver import CyclicDependencyError, DependencyResolver
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

    def __init__(self, registry: TaskRegistry) -> None:
        self.registry = registry
        self.resolver = DependencyResolver(registry)

    def check_all(self, target: Task | None = None) -> list[Issue]:
        """Run all checks and collect issues.

        If target is provided, only check tasks reachable from that target.
        Otherwise, check all tasks.
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
                        issues.append(
                            Issue(
                                "error",
                                task.name,
                                f"input '{input_path}' does not exist and no task produces it",
                            )
                        )

        return issues

