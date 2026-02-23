"""List command for pymake CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..task import TaskVar
from .context import CommandContext


class ListCommand:
    """List registered tasks."""

    def __init__(self, ctx: CommandContext) -> None:
        self.ctx = ctx

    @staticmethod
    def add_arguments(subparsers: argparse._SubParsersAction) -> None:
        """Add list command arguments."""
        parser = subparsers.add_parser("list", help="List registered tasks")
        parser.add_argument(
            "-a",
            "--all",
            action="store_true",
            dest="all_tasks",
            help="Include dynamically registered tasks",
        )

    def execute(self) -> None:
        """List registered tasks."""
        tasks = self.ctx.registry.all_tasks()

        if not tasks:
            print("No tasks registered.")
            return

        # Separate named tasks (from decorator) and dynamic tasks
        named = []
        dynamic = []

        for t in tasks:
            # Heuristic: tasks with ':' or '/' in name are likely dynamic
            if ":" in t.name or "/" in t.name:
                dynamic.append(t)
            else:
                named.append(t)

        default_name = self.ctx.registry.default_task()

        if named:
            print("Tasks:")
            # Sort with default task first
            sorted_named = sorted(named, key=lambda x: (x.name != default_name, x.name))
            for t in sorted_named:
                doc = f" - {t.doc}" if t.doc else ""
                default_marker = " (default)" if t.name == default_name else ""
                print(f"  {t.name}{default_marker}{doc}")
                if t.vars:
                    formatted_vars = ", ".join(self._format_var(v) for v in t.vars)
                    print(f"             vars: {formatted_vars}")

        if self.ctx.args.all_tasks and dynamic:
            print("\nDynamic tasks:")
            for t in sorted(dynamic, key=lambda x: x.name):
                doc = f" - {t.doc}" if t.doc else ""
                print(f"  {t.name}{doc}")
                if t.vars:
                    formatted_vars = ", ".join(self._format_var(v) for v in t.vars)
                    print(f"             vars: {formatted_vars}")

    def _format_var(self, var: TaskVar) -> str:
        type_label = var.type.__name__
        if var.is_optional:
            type_label = f"{type_label}?"

        show_default = not (var.is_optional and var.default is None)
        if not show_default:
            return f"{var.name} ({type_label})"
        return f"{var.name} ({type_label}={self._format_default(var.default)})"

    def _format_default(self, value: object) -> str:
        if isinstance(value, str):
            return f'"{value}"'
        if isinstance(value, Path):
            return f'"{value}"'
        if isinstance(value, bool):
            return "true" if value else "false"
        return repr(value)
