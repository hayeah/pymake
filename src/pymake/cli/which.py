"""Which command for pymake CLI."""

from __future__ import annotations

import argparse

from rich.tree import Tree

from ..task import Task
from .context import CommandContext


class WhichCommand:
    """Show dependency tree for a task or output file."""

    def __init__(self, ctx: CommandContext) -> None:
        self.ctx = ctx

    @staticmethod
    def add_arguments(subparsers: argparse._SubParsersAction) -> None:
        """Add which command arguments."""
        parser = subparsers.add_parser(
            "which", help="Show dependency tree for a task or output"
        )
        parser.add_argument("target", help="Task name or output file to trace")
        parser.add_argument(
            "-d",
            "--dependents",
            action="store_true",
            help="Show tasks that depend on this target instead of its dependencies",
        )

    def execute(self) -> None:
        """Show dependency tree for a task or output file."""
        found_task = self.ctx.find_target(self.ctx.args.target)
        show_dependents = self.ctx.args.dependents
        resolver = self.ctx.resolver
        printed: set[str] = set()

        def task_label(t: Task) -> str:
            """Format task name, red with (*) if it would run."""
            if t.should_run():
                return f"[red]{t.name}[/red] (*)"
            return t.name

        def add_subtree(parent: Tree, t: Task) -> None:
            if t.name in printed:
                return

            printed.add(t.name)

            # Create node for this task
            node = parent.add(task_label(t))

            if show_dependents:
                # Show tasks that depend on this one
                deps = resolver.dependents(t)
                # Filter out already-printed deps
                printable_deps = [d for d in deps if d.name not in printed]
            else:
                # Show dependencies (what this task depends on)
                deps = resolver.dependencies(t)
                # Filter deps, accounting for what each subtree will cover
                printable_deps = []
                covered: set[str] = set()
                for dep in deps:
                    if dep.name not in printed and dep.name not in covered:
                        printable_deps.append(dep)
                        covered |= resolver.transitive_deps(dep)

            # Show inputs (←) and outputs (→)
            for inp in t.inputs:
                node.add(f"[dim]← {inp}[/dim]")
            for out in t.outputs:
                node.add(f"[dim]→ {out}[/dim]")

            # Recurse into children
            for dep in printable_deps:
                add_subtree(node, dep)

        # Build the tree starting from the target
        tree = Tree(task_label(found_task))

        if show_dependents:
            # Show tasks that depend on target
            deps = resolver.dependents(found_task)
            printable_deps = [d for d in deps if d.name not in printed]
        else:
            # Show dependencies
            deps = resolver.dependencies(found_task)
            printable_deps = []
            covered: set[str] = set()
            for dep in deps:
                if dep.name not in printed and dep.name not in covered:
                    printable_deps.append(dep)
                    covered |= resolver.transitive_deps(dep)

        # Add inputs/outputs to root
        for inp in found_task.inputs:
            tree.add(f"[dim]← {inp}[/dim]")
        for out in found_task.outputs:
            tree.add(f"[dim]→ {out}[/dim]")

        printed.add(found_task.name)

        # Add subtrees for dependencies/dependents
        for dep in printable_deps:
            add_subtree(tree, dep)

        self.ctx.console.print(tree)
