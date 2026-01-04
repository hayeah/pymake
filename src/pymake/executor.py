"""Task execution engine for pymake."""

from __future__ import annotations

import concurrent.futures
import sys
import threading
from typing import TextIO

from .resolver import CyclicDependencyError, DependencyResolver
from .task import Task, TaskRegistry


class ExecutionError(Exception):
    """Raised when task execution fails."""

    def __init__(self, task_name: str, original: Exception) -> None:
        self.task_name = task_name
        self.original = original
        super().__init__(f"Task '{task_name}' failed: {original}")


class MissingInputError(Exception):
    """Raised when a task's input file is missing."""

    def __init__(self, task_name: str, input_path: str) -> None:
        self.task_name = task_name
        self.input_path = input_path
        super().__init__(
            f"Task '{task_name}' requires input '{input_path}' which does not exist"
        )


class MissingOutputError(Exception):
    """Raised when a task fails to produce a declared output."""

    def __init__(self, task_name: str, output_path: str) -> None:
        self.task_name = task_name
        self.output_path = output_path
        super().__init__(
            f"Task '{task_name}' did not produce declared output '{output_path}'"
        )


class UnproducibleInputError(Exception):
    """Raised when an input file doesn't exist and no task produces it."""

    def __init__(self, task_name: str, input_path: str) -> None:
        self.task_name = task_name
        self.input_path = input_path
        super().__init__(
            f"Task '{task_name}' requires input '{input_path}' which does not exist "
            f"and no task produces it"
        )


class Executor:
    """Executes tasks with dependency resolution."""

    def __init__(
        self,
        registry: TaskRegistry,
        *,
        parallel: bool = False,
        max_workers: int | None = None,
        force: bool = False,
        verbose: bool = True,
        output: TextIO | None = None,
    ) -> None:
        self.registry = registry
        self.resolver = DependencyResolver(registry)
        self.parallel = parallel
        self.max_workers = max_workers
        self.force = force
        self.verbose = verbose
        self.output = output or sys.stdout
        self._lock = threading.Lock()

    def log(self, message: str) -> None:
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            with self._lock:
                print(message, file=self.output)

    def run(self, target: str | Task) -> bool:
        """
        Run a target task and all its dependencies.

        Returns True if any task was executed.
        """
        if isinstance(target, str):
            task = self.registry.find_target(target)
            if not task:
                raise ValueError(f"Unknown target: {target}")
        else:
            task = target

        # Resolve dependencies
        try:
            execution_order = self.resolver.resolve(task)
        except CyclicDependencyError:
            raise

        # Validate all inputs are either existing or producible
        self._validate_inputs_producible(execution_order)

        if self.parallel:
            return self._run_parallel(execution_order)
        else:
            return self._run_sequential(execution_order)

    def _validate_inputs_producible(self, tasks: list[Task]) -> None:
        """Validate that all input files either exist or have a producing task."""
        for task in tasks:
            for input_path in task.inputs:
                if not input_path.exists():
                    # Check if any task produces this file
                    producing_task = self.registry.get_by_output(input_path)
                    if not producing_task:
                        raise UnproducibleInputError(task.name, str(input_path))

    def _run_sequential(self, tasks: list[Task]) -> bool:
        """Run tasks sequentially in dependency order."""
        any_executed = False

        for task in tasks:
            executed = self._execute_task(task)
            if executed:
                any_executed = True

        return any_executed

    def _run_parallel(self, tasks: list[Task]) -> bool:
        """Run tasks in parallel where possible."""
        # Build a map of task -> set of dependency task names
        task_deps: dict[str, set[str]] = {}
        for task in tasks:
            deps = self.resolver.get_dependencies(task)
            task_deps[task.name] = {d.name for d in deps}

        # Track completed tasks
        completed: set[str] = set()
        completed_lock = threading.Lock()
        any_executed = False
        executed_lock = threading.Lock()

        # Track failed tasks
        failed: set[str] = set()
        first_error: ExecutionError | None = None
        error_lock = threading.Lock()

        task_map = {t.name: t for t in tasks}

        def can_run(task_name: str) -> bool:
            """Check if all dependencies are completed."""
            with completed_lock:
                return task_deps[task_name].issubset(completed)

        def mark_completed(task_name: str) -> None:
            with completed_lock:
                completed.add(task_name)

        def execute_wrapper(task: Task) -> bool:
            """Wrapper to execute a task and handle errors."""
            nonlocal any_executed, first_error

            # Check if any dependency failed
            with error_lock:
                if failed:
                    return False

            try:
                executed = self._execute_task(task)
                if executed:
                    with executed_lock:
                        any_executed = True
                mark_completed(task.name)
                return True
            except Exception as e:
                with error_lock:
                    failed.add(task.name)
                    if first_error is None:
                        if isinstance(e, ExecutionError):
                            first_error = e
                        else:
                            first_error = ExecutionError(task.name, e)
                return False

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:
            pending = set(task_map.keys())
            futures: dict[concurrent.futures.Future[bool], str] = {}

            while pending or futures:
                # Submit ready tasks
                ready = [name for name in pending if can_run(name)]
                for name in ready:
                    pending.remove(name)
                    future = executor.submit(execute_wrapper, task_map[name])
                    futures[future] = name

                if not futures:
                    break

                # Wait for at least one task to complete
                done, _ = concurrent.futures.wait(
                    futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )

                for future in done:
                    del futures[future]

                # Check for errors
                with error_lock:
                    if first_error:
                        # Cancel pending futures
                        for f in futures:
                            f.cancel()
                        raise first_error

        return any_executed

    def _execute_task(self, task: Task) -> bool:
        """
        Execute a single task if needed.

        Returns True if the task was executed.
        """
        # Check if task should run based on file timestamps
        if not task.should_run(self.force):
            self.log(f"[skip] {task.name} (up to date)")
            return False

        # Check custom run_if condition
        if task.run_if is not None:
            try:
                if not task.run_if():
                    self.log(f"[skip] {task.name} (run_if returned False)")
                    return False
            except Exception as e:
                raise ExecutionError(task.name, e) from e

        # Validate all input files exist before running
        for input_path in task.inputs:
            if not input_path.exists():
                raise MissingInputError(task.name, str(input_path))

        # Execute the task
        self.log(f"[run] {task.name}")
        try:
            task.func()
        except Exception as e:
            raise ExecutionError(task.name, e) from e

        # Validate all output files were created (excluding touch file)
        for output_path in task.outputs:
            if task.touch and output_path == task.touch:
                continue  # Touch file is created by executor, not the task
            if not output_path.exists():
                raise MissingOutputError(task.name, str(output_path))

        # Touch file if specified
        if task.touch:
            task.touch.parent.mkdir(parents=True, exist_ok=True)
            task.touch.touch()

        return True

    def run_multiple(self, targets: list[str]) -> bool:
        """Run multiple targets."""
        any_executed = False
        for target in targets:
            if self.run(target):
                any_executed = True
        return any_executed
