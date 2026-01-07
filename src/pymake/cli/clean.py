"""Clean command for pymake CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..task import Task
from .context import CommandContext


class CleanCommand:
    """Clean output files of tasks."""

    def __init__(self, ctx: CommandContext) -> None:
        self.ctx = ctx

    @staticmethod
    def add_arguments(subparsers: argparse._SubParsersAction) -> None:
        """Add clean command arguments."""
        parser = subparsers.add_parser("clean", help="Clean output files of tasks")
        parser.add_argument(
            "target",
            nargs="?",
            help="Target task to clean (required unless --all)",
        )
        parser.add_argument(
            "--up",
            action="store_true",
            help="Also clean output files of dependencies (upstream tasks)",
        )
        parser.add_argument(
            "--down",
            action="store_true",
            help="Also clean output files of dependents (downstream tasks)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_tasks",
            help="Clean all known output files",
        )
        parser.add_argument(
            "--dry",
            action="store_true",
            help="Show what would be deleted without deleting",
        )

    def execute(self) -> None:
        """Clean output files."""
        target = self.ctx.args.target
        clean_all = self.ctx.args.all_tasks
        clean_up = self.ctx.args.up
        clean_down = self.ctx.args.down
        dry_run = self.ctx.args.dry

        # Validate arguments
        if clean_all and target:
            print("Error: Cannot specify both --all and a target", file=sys.stderr)
            sys.exit(1)
        if not clean_all and not target:
            print("Error: Must specify a target or --all", file=sys.stderr)
            sys.exit(1)
        if clean_all and (clean_up or clean_down):
            print("Error: --up and --down cannot be used with --all", file=sys.stderr)
            sys.exit(1)

        # Collect files to clean
        files_to_clean: set[Path] = set()

        if clean_all:
            # Clean all output files from all tasks
            for task in self.ctx.registry.all_tasks():
                files_to_clean.update(task.outputs)
        else:
            # Find the target task
            found_task = self.ctx.find_target(target)
            tasks_to_clean: list[Task] = [found_task]

            if clean_up:
                # Add dependencies (upstream)
                deps = self.ctx.resolver.resolve(found_task)
                for dep in deps:
                    if dep.name != found_task.name:
                        tasks_to_clean.append(dep)

            if clean_down:
                # Add dependents (downstream)
                dependent_names = self.ctx.resolver.transitive_dependents(found_task)
                for dep_name in dependent_names:
                    if dep_name != found_task.name:
                        dep_task = self.ctx.registry.get(dep_name)
                        if dep_task:
                            tasks_to_clean.append(dep_task)

            # Collect output files
            for task in tasks_to_clean:
                files_to_clean.update(task.outputs)

        # Filter to only existing files
        existing_files = sorted(f for f in files_to_clean if f.exists())

        if not existing_files:
            self.ctx.console.print("Nothing to clean.")
            return

        # Perform cleaning
        if dry_run:
            self.ctx.console.print("[dim]Dry run - would delete:[/dim]")
            for f in existing_files:
                self.ctx.console.print(f"  {f}")
            self.ctx.console.print(f"\n[dim]{len(existing_files)} file(s) would be deleted[/dim]")
        else:
            for f in existing_files:
                f.unlink()
                self.ctx.console.print(f"[red]deleted[/red] {f}")
            self.ctx.console.print(f"\n{len(existing_files)} file(s) deleted")
