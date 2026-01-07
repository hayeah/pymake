"""Shared context for CLI commands."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console

from ..doctor import Doctor
from ..resolver import DependencyResolver
from ..task import Task, TaskRegistry

if TYPE_CHECKING:
    import argparse


class CommandContext:
    """Shared context for CLI commands.

    Provides access to common resources and utilities needed by commands.
    Commands receive this via composition rather than inheritance.
    """

    def __init__(
        self,
        registry: TaskRegistry,
        args: argparse.Namespace,
    ) -> None:
        self.registry = registry
        self.args = args
        self.console = Console()
        self._resolver: DependencyResolver | None = None

    @property
    def resolver(self) -> DependencyResolver:
        """Lazily create and cache the dependency resolver."""
        if self._resolver is None:
            self._resolver = DependencyResolver(self.registry)
        return self._resolver

    @property
    def parallel(self) -> bool:
        """Whether to run tasks in parallel."""
        return getattr(self.args, "parallel", False) or self.args.jobs is not None

    @property
    def verbose(self) -> bool:
        """Whether to show verbose output."""
        return not getattr(self.args, "quiet", False)

    def find_target(self, target: str) -> Task:
        """Find target by name or output file, raising ValueError if not found."""
        return self.registry.find_target_or_raise(target)

    def check_before_run(self, target: Task) -> None:
        """Run doctor check before execution. Exit if issues found."""
        doctor = Doctor(self.registry)
        issues = doctor.check_all(target)
        if issues:
            for issue in issues:
                self.console.print(f"[red]error[/red]: {issue.task}: {issue.message}")
            self.console.print(f"\n[red]{len(issues)} error(s)[/red]")
            sys.exit(1)
