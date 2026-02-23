"""Task definition and registry for pymake."""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable, Sequence
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

SUPPORTED_VAR_TYPES = {str, int, float, bool, Path}


@dataclasses.dataclass(frozen=True)
class TaskVar:
    """A variable extracted from a task function signature."""

    name: str
    type: type[Any]
    default: Any
    is_optional: bool


def _is_optional(annotation: Any) -> bool:
    """True if annotation is exactly T | None / Optional[T]."""
    origin = get_origin(annotation)
    if origin not in (Union, UnionType):
        return False

    args = get_args(annotation)
    if len(args) != 2:
        return False

    return any(arg is type(None) for arg in args)


def _unwrap_optional(annotation: Any) -> Any:
    """Return T from Optional[T]."""
    args = get_args(annotation)
    for arg in args:
        if arg is not type(None):
            return arg
    return annotation


def vars_from_signature(func: Callable[..., None]) -> tuple[TaskVar, ...]:
    """Extract and validate task variables from function signature."""
    signature = inspect.signature(func)
    type_hints = get_type_hints(func)
    result: list[TaskVar] = []

    for param in signature.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise ValueError(f"Task '{func.__name__}': *args/**kwargs not supported")

        annotation: Any = type_hints.get(param.name, param.annotation)
        is_optional = False

        if annotation is not inspect.Parameter.empty and _is_optional(annotation):
            annotation = _unwrap_optional(annotation)
            is_optional = True

        if annotation is inspect.Parameter.empty:
            annotation = str

        if annotation not in SUPPORTED_VAR_TYPES:
            raise ValueError(
                f"Task '{func.__name__}': unsupported type {annotation} "
                f"for var '{param.name}'"
            )

        if param.default is inspect.Parameter.empty:
            if not is_optional:
                raise ValueError(
                    f"Task '{func.__name__}': var '{param.name}' "
                    "must have a default value or be Optional"
                )
            default = None
        else:
            default = param.default

        result.append(
            TaskVar(
                name=param.name,
                type=annotation,
                default=default,
                is_optional=is_optional,
            )
        )

    return tuple(result)


@dataclasses.dataclass
class Task:
    """A build task with inputs, outputs, and execution function."""

    name: str
    func: Callable[..., None]
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]
    vars: tuple[TaskVar, ...] = ()
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

    def default(self, name: str | Callable[..., None]) -> None:
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
        func: Callable[..., None],
        *,
        name: str | None = None,
        inputs: Sequence[str | Path | Callable[..., None]] = (),
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

        task_vars = vars_from_signature(func)

        # Create and store task
        task = Task(
            name=task_name,
            func=func,
            inputs=tuple(input_paths),
            outputs=output_paths,
            vars=task_vars,
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
        inputs: Sequence[str | Path | Callable[..., None]] = (),
        outputs: Sequence[str | Path] = (),
        run_if: Callable[[], bool] | None = None,
        run_if_not: Callable[[], bool] | None = None,
        touch: str | Path | None = None,
    ) -> Callable[[Callable[..., None]], Callable[..., None]]:
        """Decorator to register a task."""

        def decorator(func: Callable[..., None]) -> Callable[..., None]:
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

    def find_target_or_raise(self, target: str) -> Task:
        """Find a task by name or output file, raising ValueError if not found."""
        task = self.find_target(target)
        if not task:
            raise ValueError(f"Unknown target: {target}")
        return task

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
