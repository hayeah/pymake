"""Redo command for pymake CLI."""

from __future__ import annotations

import argparse

from ..executor import Executor
from ..task import Task
from .context import CommandContext


class RedoCommand:
    """Force re-run a target and its dependents."""

    def __init__(self, ctx: CommandContext) -> None:
        self.ctx = ctx

    @staticmethod
    def add_arguments(subparsers: argparse._SubParsersAction) -> None:
        """Add redo command arguments."""
        parser = subparsers.add_parser(
            "redo", help="Force re-run a target and its dependents"
        )
        parser.add_argument("target", help="Task name or output file to redo")
        parser.add_argument(
            "--only",
            action="store_true",
            help="Only redo target, not its dependents",
        )

    def execute(self) -> None:
        """Force re-run a target and optionally its dependents."""
        found_task = self.ctx.find_target(self.ctx.args.target)
        self.ctx.check_before_run(found_task)

        if self.ctx.args.only:
            self._redo_only(found_task)
        else:
            self._redo_with_dependents(found_task)

    def _redo_only(self, found_task: Task) -> None:
        """Only redo the target task, not its dependents."""
        executor = Executor(
            self.ctx.registry,
            vars_resolver=self.ctx.vars_resolver,
            parallel=False,
            force=True,
            verbose=self.ctx.verbose,
        )

        # First, run dependencies (not forced) so inputs are ready
        deps = self.ctx.resolver.resolve(found_task)
        for dep in deps:
            if dep.name != found_task.name:
                executor.force = False
                executor._execute_task(dep)

        # Then force-run the target
        executor.force = True
        executed = executor._execute_task(found_task)
        if not executed and self.ctx.verbose:
            print(f"Warning: {found_task.name} was skipped (run_if condition).")

    def _redo_with_dependents(self, found_task: Task) -> None:
        """Redo target and all its dependents."""
        resolver = self.ctx.resolver

        # Get all tasks that transitively depend on this one
        dependent_names = resolver.transitive_dependents(found_task)

        # Build execution order: first the target's dependencies, then target,
        # then all dependents in topological order
        tasks_to_run: list[Task] = []
        seen: set[str] = set()

        # Add the target and its dependencies first
        target_deps = resolver.resolve(found_task)
        for t in target_deps:
            if t.name not in seen:
                tasks_to_run.append(t)
                seen.add(t.name)

        # Then add all dependents
        for dep_name in dependent_names:
            if dep_name not in seen:
                dep_task = self.ctx.registry.get(dep_name)
                if dep_task:
                    tasks_to_run.append(dep_task)
                    seen.add(dep_name)

        executor = Executor(
            self.ctx.registry,
            vars_resolver=self.ctx.vars_resolver,
            parallel=self.ctx.parallel,
            max_workers=self.ctx.args.jobs,
            force=False,  # We'll selectively force
            verbose=self.ctx.verbose,
        )

        any_executed = False
        for t in tasks_to_run:
            # Force tasks that are the target or its dependents
            force_this = t.name in dependent_names
            if force_this:
                # Temporarily set force for this task
                old_force = executor.force
                executor.force = True
                if executor._execute_task(t):
                    any_executed = True
                executor.force = old_force
            else:
                # Run normally (dependencies of target)
                if executor._execute_task(t):
                    any_executed = True

        if not any_executed and self.ctx.verbose:
            print("Nothing to do.")
