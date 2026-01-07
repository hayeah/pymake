"""Run command for pymake CLI."""

from __future__ import annotations

import argparse
import sys

from ..executor import Executor
from ..task import Task
from .context import CommandContext


class RunCommand:
    """Run specified targets."""

    def __init__(self, ctx: CommandContext) -> None:
        self.ctx = ctx

    @staticmethod
    def add_arguments(subparsers: argparse._SubParsersAction) -> None:
        """Add run command arguments."""
        parser = subparsers.add_parser("run", help="Run specified targets")
        parser.add_argument("targets", nargs="+", help="Targets to run")

    def execute(self) -> None:
        """Run specified targets."""
        targets = self.ctx.args.targets
        if not targets:
            print("Error: No targets specified", file=sys.stderr)
            sys.exit(1)

        # Find all target tasks and run doctor check
        target_tasks: list[Task] = []
        for target in targets:
            found = self.ctx.find_target(target)
            target_tasks.append(found)

        for t in target_tasks:
            self.ctx.check_before_run(t)

        executor = Executor(
            self.ctx.registry,
            parallel=self.ctx.parallel,
            max_workers=self.ctx.args.jobs,
            force=self.ctx.args.force,
            verbose=self.ctx.verbose,
        )

        any_executed = False
        for t in target_tasks:
            if executor.run(t):
                any_executed = True

        if not any_executed and self.ctx.verbose:
            print("Nothing to do (all targets up to date).")
