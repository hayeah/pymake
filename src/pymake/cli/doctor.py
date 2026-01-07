"""Doctor command for pymake CLI."""

from __future__ import annotations

import argparse
import sys

from ..doctor import Doctor
from .context import CommandContext


class DoctorCommand:
    """Check for dependency issues."""

    def __init__(self, ctx: CommandContext) -> None:
        self.ctx = ctx

    @staticmethod
    def add_arguments(subparsers: argparse._SubParsersAction) -> None:
        """Add doctor command arguments."""
        parser = subparsers.add_parser("doctor", help="Check for dependency issues")
        parser.add_argument(
            "target",
            nargs="?",
            help="Target to check (default: all tasks)",
        )

    def execute(self) -> None:
        """Check for dependency issues."""
        # Find target task if specified
        target_task = None
        if self.ctx.args.target:
            target_task = self.ctx.find_target(self.ctx.args.target)

        doctor = Doctor(self.ctx.registry)
        issues = doctor.check_all(target_task)

        if not issues:
            self.ctx.console.print("[green]No issues found.[/green]")
            return

        # Group by severity
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]

        for issue in errors:
            self.ctx.console.print(f"[red]error[/red]: {issue.task}: {issue.message}")

        for issue in warnings:
            self.ctx.console.print(
                f"[yellow]warning[/yellow]: {issue.task}: {issue.message}"
            )

        self.ctx.console.print()
        if errors:
            self.ctx.console.print(f"[red]{len(errors)} error(s)[/red]", end="")
            if warnings:
                self.ctx.console.print(f", [yellow]{len(warnings)} warning(s)[/yellow]")
            else:
                self.ctx.console.print()
            sys.exit(1)
        else:
            self.ctx.console.print(f"[yellow]{len(warnings)} warning(s)[/yellow]")
