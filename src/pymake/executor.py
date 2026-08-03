"""Task execution engine for pymake."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import sys
import threading
from pathlib import Path
from typing import TextIO

from .resolver import CyclicDependencyError, DependencyResolver
from .staleness import (
    FingerprintCache,
    Snapshot,
    change_reason,
    diff_record,
    take_snapshot,
)
from .state import StateStore, TaskState
from .task import Task, TaskRegistry
from .vars import VarsResolver

#: Default per-task state root, relative to the working directory.
DEFAULT_STATE_DIR = Path(".pymake") / "state"


@dataclasses.dataclass
class Decision:
    """Whether (and why) a task runs, plus the data the decision was made on."""

    run: bool
    reason: str
    # Decision-time fingerprints — recorded on bootstrap, and the baseline
    # for the post-run divergence check. None when the task records nothing.
    pre: Snapshot | None = None
    # Record the pre-run snapshot without running (legacy mtime migration).
    bootstrap: bool = False


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

    def __init__(
        self, task_name: str, input_path: str, message: str | None = None
    ) -> None:
        self.task_name = task_name
        self.input_path = input_path
        super().__init__(
            message
            or (
                f"Task '{task_name}' requires input '{input_path}' which does not "
                f"exist and no task produces it"
            )
        )


class Executor:
    """Executes tasks with dependency resolution."""

    def __init__(
        self,
        registry: TaskRegistry,
        *,
        vars_resolver: VarsResolver | None = None,
        parallel: bool = False,
        max_workers: int | None = None,
        force: bool = False,
        verbose: bool = True,
        output: TextIO | None = None,
        state_dir: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self.resolver = DependencyResolver(registry)
        self.parallel = parallel
        self.max_workers = max_workers
        self.force = force
        self.verbose = verbose
        self.output = output or sys.stdout
        self.vars_resolver = vars_resolver or VarsResolver()
        self.state = StateStore(
            state_dir if state_dir is not None else DEFAULT_STATE_DIR
        )
        self._fingerprints = FingerprintCache()
        self._vars_validated = False
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

        self._validate_vars_once()

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
                    producing_task = self.registry.by_output(input_path)
                    if not producing_task:
                        # A dotted, separator-free string input is a task
                        # reference that resolved to nothing — say so.
                        hint = task.ref_hint(input_path)
                        message = f"Task '{task.name}': {hint}" if hint else None
                        raise UnproducibleInputError(
                            task.name, str(input_path), message
                        )

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
            deps = self.resolver.dependencies(task)
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

    def _decide(self, task: Task) -> Decision:
        """The single staleness rule:

        run = cli_force or fingerprints_changed or outputs_missing or no_record

        Legacy tasks — no Input objects — keep their historical semantics:
        a task with no outputs always runs (phony), and a task with no
        fingerprint record yet falls back to the mtime comparison once,
        bootstrapping a record so subsequent runs use fingerprints.
        """
        # Records exist for tasks that participate in fingerprint gating.
        trackable = bool(task.outputs or task.input_objects)
        fingerprintable = trackable and bool(
            task.inputs or task.depends or task.input_objects
        )
        pre = (
            take_snapshot(task, self.registry, self._fingerprints)
            if fingerprintable
            else None
        )

        if self.force:
            return Decision(True, "forced", pre)

        if not trackable:
            # Legacy phony: no outputs and no Input objects — always runs.
            return Decision(True, "", None)

        for out in task.outputs:
            if not out.exists():
                return Decision(True, f"output {out} missing", pre)

        if pre is None:
            # Outputs exist and there is nothing to fingerprint.
            return Decision(False, "up to date", None)

        record = self.state.load(task.name)
        if record is None:
            if task.input_objects:
                return Decision(True, "no record", pre)
            # Transparent migration for path/dep-only tasks: decide by the
            # historical mtime rule once, and record fingerprints either way
            # (post-run below, or via bootstrap here on a skip).
            if task.should_run(False):
                return Decision(True, "stale", pre)
            return Decision(False, "up to date", pre, bootstrap=True)

        changes = diff_record(record, pre)
        if changes:
            return Decision(True, change_reason(changes), pre)
        return Decision(False, "unchanged", pre)

    def _record_state(self, task: Task, snap: Snapshot) -> None:
        self.state.save(
            task.name,
            TaskState(paths=snap.paths, deps=snap.deps, inputs=snap.inputs),
        )

    def _record_after_run(self, task: Task, decision: Decision) -> None:
        """Re-walk every input FRESH and record the fingerprints.

        Recording is the executor's job, part of the contract — there is no
        commit protocol to discover, nothing a wrapper function can
        disconnect. Recording post-run (not from the pre-run walk) means a
        task that touches its own inputs settles instead of looping.
        """
        if decision.pre is None:
            return
        post = take_snapshot(task, self.registry, self._fingerprints, fresh=True)
        self._record_state(task, post)

    def _execute_task(self, task: Task) -> bool:
        """
        Execute a single task if needed.

        Returns True if the task was executed.
        """
        self._validate_vars_once()

        decision = self._decide(task)
        if not decision.run:
            if decision.bootstrap and decision.pre is not None:
                self._record_state(task, decision.pre)
            self.log(f"[skip] {task.name} ({decision.reason})")
            return False

        # --force bypasses run_if / run_if_not entirely: force means force.
        if not self.force:
            if task.run_if is not None:
                try:
                    if not task.run_if():
                        self.log(f"[skip] {task.name} (run_if returned False)")
                        return False
                except Exception as e:
                    raise ExecutionError(task.name, e) from e

            if task.run_if_not is not None:
                try:
                    if task.run_if_not():
                        self.log(f"[skip] {task.name} (run_if_not returned True)")
                        return False
                except Exception as e:
                    raise ExecutionError(task.name, e) from e

        # Validate all input files exist before running
        for input_path in task.inputs:
            if not input_path.exists():
                raise MissingInputError(task.name, str(input_path))

        # Execute the task; the decision line says why it runs.
        suffix = f" ({decision.reason})" if decision.reason else ""
        self.log(f"[run] {task.name}{suffix}")
        try:
            kwargs = self.vars_resolver.resolve(task)
            task.func(**kwargs)
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

        # After a successful run, commit any stateful run_if predicate
        # (e.g. TreeDigest.changed) so the next invocation sees the updated
        # snapshot. ``run_if`` is usually a bound method like
        # ``digest.changed`` — look for ``commit`` on the method itself
        # first, then fall back to the bound instance (``__self__``). Plain
        # lambdas and functions have neither and are unaffected.
        commit = getattr(task.run_if, "commit", None)
        if commit is None:
            owner = getattr(task.run_if, "__self__", None)
            if owner is not None:
                commit = getattr(owner, "commit", None)
        if callable(commit):
            try:
                commit()
            except Exception as e:
                raise ExecutionError(task.name, e) from e

        # Record fingerprints from a fresh post-run walk. Forced runs record
        # normally, so a forced build settles state and the next run skips.
        self._record_after_run(task, decision)

        return True

    def run_multiple(self, targets: list[str]) -> bool:
        """Run multiple targets."""
        any_executed = False
        for target in targets:
            if self.run(target):
                any_executed = True
        return any_executed

    def _validate_vars_once(self) -> None:
        if self._vars_validated:
            return
        self.vars_resolver.validate_tasks(self.registry.all_tasks())
        self._vars_validated = True
