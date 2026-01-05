"""Task definition and registry for pymake."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from pathlib import Path


@dataclasses.dataclass
class Task:
    """A build task with inputs, outputs, and execution function."""

    name: str
    func: Callable[[], None]
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]
    run_if: Callable[[], bool] | None = None
    run_if_not: Callable[[], bool] | None = None
    doc: str | None = None
    touch: Path | None = None
    depends: tuple[str, ...] = ()

    @property
    def is_phony(self) -> bool:
        """Task is phony if it has no outputs (always runs)."""
        return len(self.outputs) == 0

    def should_run(self, force: bool = False) -> bool:
        """Determine if this task should run based on file timestamps."""
        if force:
            return True

        # No outputs = phony target, always run
        if self.is_phony:
            return True

        # Check if any output is missing
        for out in self.outputs:
            if not out.exists():
                return True

        # No inputs = only run if output doesn't exist (already checked above)
        if not self.inputs:
            return False

        # Get the oldest output mtime
        oldest_output = min(out.stat().st_mtime for out in self.outputs)

        # Check if any input is newer than the oldest output
        for inp in self.inputs:
            if inp.exists() and inp.stat().st_mtime > oldest_output:
                return True

        return False


class TaskRegistry:
    """Registry for all tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._output_to_task: dict[Path, str] = {}
        self._default: str | None = None

    def default(self, name: str | Callable[[], None]) -> None:
        """Set the default task to run when no target is specified."""
        if callable(name):
            self._default = name.__name__
        else:
            self._default = name

    def default_task(self) -> str | None:
        """Get the default task name."""
        return self._default

    def register(
        self,
        func: Callable[[], None],
        *,
        name: str | None = None,
        inputs: Sequence[str | Path | Callable[[], None]] = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
    ) -> Task:
        """Register a task with the given parameters."""
        task_name = name or func.__name__

        # Separate callable inputs (task dependencies) from path inputs
        input_paths: list[Path] = []
        task_depends: list[str] = []
        for inp in inputs:
            if callable(inp):
                task_depends.append(inp.__name__)
            else:
                input_paths.append(Path(inp))

        output_paths = tuple(Path(p) for p in outputs)
        touch_path = Path(touch) if touch else None

        # Touch file is also an output
        if touch_path:
            output_paths = (*output_paths, touch_path)

        # Check for output conflicts
        for out in output_paths:
            out_resolved = out.resolve()
            if out_resolved in self._output_to_task:
                existing = self._output_to_task[out_resolved]
                raise ValueError(
                    f"Output file '{out}' is already produced by task '{existing}'. "
                    f"Cannot register task '{task_name}'."
                )

        # Create and store task
        task = Task(
            name=task_name,
            func=func,
            inputs=tuple(input_paths),
            outputs=output_paths,
            run_if=run_if,
            run_if_not=run_if_not,
            doc=func.__doc__,
            touch=touch_path,
            depends=tuple(task_depends),
        )

        if task_name in self._tasks:
            raise ValueError(f"Task '{task_name}' is already registered.")

        self._tasks[task_name] = task

        # Map outputs to task
        for out in output_paths:
            self._output_to_task[out.resolve()] = task_name

        return task

    def __call__(
        self,
        inputs: Sequence[str | Path | Callable[[], None]] = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
    ) -> Callable[[Callable[[], None]], Callable[[], None]]:
        """Decorator to register a task."""

        def decorator(func: Callable[[], None]) -> Callable[[], None]:
            self.register(
                func,
                inputs=inputs,
                outputs=outputs,
                run_if=run_if,
                run_if_not=run_if_not,
                touch=touch,
            )
            return func

        return decorator

    def get(self, name: str) -> Task | None:
        """Get a task by name."""
        return self._tasks.get(name)

    def by_output(self, path: str | Path) -> Task | None:
        """Get a task that produces the given output file."""
        resolved = Path(path).resolve()
        task_name = self._output_to_task.get(resolved)
        if task_name:
            return self._tasks.get(task_name)
        return None

    def find_target(self, target: str) -> Task | None:
        """Find a task by name or by output file."""
        # First try by name
        task = self.get(target)
        if task:
            return task

        # Then try by output file
        return self.by_output(target)

    def all_tasks(self) -> list[Task]:
        """Return all registered tasks."""
        return list(self._tasks.values())

    def named_tasks(self) -> list[Task]:
        """Return tasks registered with @task decorator."""
        # For now, return all tasks. In practice, we might want to distinguish
        # between decorator-registered and dynamically-registered tasks.
        return list(self._tasks.values())

    def clear(self) -> None:
        """Clear all registered tasks."""
        self._tasks.clear()
        self._output_to_task.clear()
        self._default = None


# Global task registry
task = TaskRegistry()
