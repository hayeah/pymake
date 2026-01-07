"""Command-line interface for pymake."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import NoReturn

from rich.console import Console
from rich.tree import Tree

from .executor import (
    ExecutionError,
    Executor,
    MissingInputError,
    MissingOutputError,
    UnproducibleInputError,
)
from .resolver import CyclicDependencyError, DependencyResolver
from .task import Task, task


class CLI:
    """Command-line interface handler for pymake."""

    SUBCOMMANDS = {"list", "graph", "run", "which", "redo", "help"}

    def __init__(self, argv: list[str] | None = None) -> None:
        self.argv = argv if argv is not None else sys.argv[1:]
        self.registry = task
        self.parser: argparse.ArgumentParser | None = None
        self.args: argparse.Namespace | None = None

    @property
    def parallel(self) -> bool:
        """Whether to run tasks in parallel."""
        assert self.args is not None
        return self.args.parallel or self.args.jobs is not None

    def run(self) -> NoReturn:
        """Main entry point - parse args and dispatch to appropriate command."""
        try:
            if self._is_target_mode():
                self._run_target_mode()
            else:
                self._run_subcommand_mode()
        except (
            CyclicDependencyError,
            UnproducibleInputError,
            MissingInputError,
            MissingOutputError,
            ExecutionError,
            ValueError,
        ) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    def _is_target_mode(self) -> bool:
        """Check if first positional arg is a target (not a subcommand)."""
        for i, arg in enumerate(self.argv):
            if not arg.startswith("-"):
                # Skip value for options that take arguments
                prev = self.argv[i - 1] if i > 0 else ""
                if prev in ("-f", "--file", "-C", "--directory", "-j", "--jobs"):
                    continue
                return arg not in self.SUBCOMMANDS
        return False

    def _build_base_parser(self) -> argparse.ArgumentParser:
        """Build the base argument parser with common options."""
        parser = argparse.ArgumentParser(
            prog="pymake",
            description="Python Makefile alternative",
        )
        parser.add_argument(
            "-f",
            "--file",
            default="Makefile.py",
            help="Path to Makefile.py (default: Makefile.py)",
        )
        parser.add_argument(
            "-C",
            "--directory",
            default=None,
            help="Change to directory before doing anything",
        )
        parser.add_argument(
            "-j",
            "--jobs",
            type=int,
            default=None,
            help="Number of parallel jobs (implies --parallel)",
        )
        parser.add_argument(
            "-p",
            "--parallel",
            action="store_true",
            help="Enable parallel execution",
        )
        parser.add_argument(
            "-B",
            "--force",
            action="store_true",
            help="Force rerun of all tasks",
        )
        parser.add_argument(
            "-q",
            "--quiet",
            action="store_true",
            help="Quiet mode (suppress output)",
        )
        return parser

    def _add_subparsers(self, parser: argparse.ArgumentParser) -> None:
        """Add subcommand parsers."""
        subparsers = parser.add_subparsers(dest="command", help="Commands")

        # list command
        list_parser = subparsers.add_parser("list", help="List registered tasks")
        list_parser.add_argument(
            "-a",
            "--all",
            action="store_true",
            dest="all_tasks",
            help="Include dynamically registered tasks",
        )

        # graph command
        graph_parser = subparsers.add_parser(
            "graph", help="Generate DOT graph for a target"
        )
        graph_parser.add_argument("target", help="Target to graph")

        # run command
        run_parser = subparsers.add_parser("run", help="Run specified targets")
        run_parser.add_argument("targets", nargs="+", help="Targets to run")

        # which command
        which_parser = subparsers.add_parser(
            "which", help="Show dependency tree for a task or output"
        )
        which_parser.add_argument("target", help="Task name or output file to trace")
        which_parser.add_argument(
            "-d",
            "--dependents",
            action="store_true",
            help="Show tasks that depend on this target instead of its dependencies",
        )

        # redo command
        redo_parser = subparsers.add_parser(
            "redo", help="Force re-run a target and its dependents"
        )
        redo_parser.add_argument("target", help="Task name or output file to redo")
        redo_parser.add_argument(
            "--only",
            action="store_true",
            help="Only redo target, not its dependents",
        )

        # help command
        subparsers.add_parser("help", help="Show help")

    def _change_directory(self) -> None:
        """Change to the specified directory if -C was given."""
        assert self.args is not None
        if self.args.directory:
            try:
                os.chdir(self.args.directory)
            except OSError as e:
                msg = f"Error: Cannot change to directory '{self.args.directory}': {e}"
                print(msg, file=sys.stderr)
                sys.exit(1)

    def _load_makefile(self) -> None:
        """Load and execute the Makefile.py."""
        assert self.args is not None
        path = Path(self.args.file)

        if not path.exists():
            print(f"Error: {path} not found", file=sys.stderr)
            sys.exit(1)

        code = path.read_text()
        globals_dict = {
            "__name__": "__main__",
            "__file__": str(path.resolve()),
        }

        # Add the Makefile's directory to sys.path
        makefile_dir = str(path.parent.resolve())
        if makefile_dir not in sys.path:
            sys.path.insert(0, makefile_dir)

        try:
            exec(compile(code, path, "exec"), globals_dict)
        except Exception as e:
            print(f"Error loading {path}: {e}", file=sys.stderr)
            sys.exit(1)

    def _run_target_mode(self) -> NoReturn:
        """Handle direct target execution (e.g., `pymake build`)."""
        self.parser = self._build_base_parser()
        self.parser.add_argument("targets", nargs="+", help="Targets to run")
        self.args = self.parser.parse_args(self.argv)

        self._change_directory()
        self.registry.clear()
        self._load_makefile()
        self._cmd_run(self.args.targets)
        sys.exit(0)

    def _run_subcommand_mode(self) -> NoReturn:
        """Handle subcommand execution (e.g., `pymake list`)."""
        self.parser = self._build_base_parser()
        self._add_subparsers(self.parser)
        self.args = self.parser.parse_args(self.argv)

        self._change_directory()
        self.registry.clear()
        self._load_makefile()
        self._dispatch_command()

    def _dispatch_command(self) -> NoReturn:
        """Dispatch to the appropriate command handler."""
        assert self.args is not None
        assert self.parser is not None

        command = self.args.command

        if command == "list":
            self._cmd_list(self.args.all_tasks)
        elif command == "graph":
            self._cmd_graph(self.args.target)
        elif command == "run":
            self._cmd_run(self.args.targets)
        elif command == "which":
            self._cmd_which(self.args.target, self.args.dependents)
        elif command == "redo":
            self._cmd_redo(self.args.target, self.args.only)
        elif command == "help":
            self._cmd_help()
        else:
            # No command - try default task or show help
            default_target = self.registry.default_task()
            if default_target:
                self._cmd_run([default_target])
            else:
                self._cmd_help()

        sys.exit(0)

    def _cmd_list(self, all_tasks: bool) -> None:
        """List registered tasks."""
        tasks = self.registry.all_tasks()

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

        default_name = self.registry.default_task()

        if named:
            print("Tasks:")
            # Sort with default task first
            sorted_named = sorted(named, key=lambda x: (x.name != default_name, x.name))
            for t in sorted_named:
                doc = f" - {t.doc}" if t.doc else ""
                default_marker = " (default)" if t.name == default_name else ""
                print(f"  {t.name}{default_marker}{doc}")

        if all_tasks and dynamic:
            print("\nDynamic tasks:")
            for t in sorted(dynamic, key=lambda x: x.name):
                doc = f" - {t.doc}" if t.doc else ""
                print(f"  {t.name}{doc}")

    def _cmd_graph(self, target: str) -> None:
        """Generate a DOT graph for a target."""
        found_task = self.registry.find_target(target)
        if not found_task:
            print(f"Error: Unknown target: {target}", file=sys.stderr)
            sys.exit(1)

        resolver = DependencyResolver(self.registry)
        dot = resolver.to_dot(found_task)
        print(dot)

    def _cmd_which(self, target: str, show_dependents: bool) -> None:
        """Show dependency tree for a task or output file."""
        console = Console()

        # Find task by name or output file
        found_task = self.registry.find_target(target)

        if not found_task:
            print(f"Error: Unknown target: {target}", file=sys.stderr)
            sys.exit(1)

        resolver = DependencyResolver(self.registry)
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

        console.print(tree)

    def _cmd_run(self, targets: list[str]) -> None:
        """Run specified targets."""
        assert self.args is not None

        if not targets:
            print("Error: No targets specified", file=sys.stderr)
            sys.exit(1)

        executor = Executor(
            self.registry,
            parallel=self.parallel,
            max_workers=self.args.jobs,
            force=self.args.force,
            verbose=not self.args.quiet,
        )

        any_executed = False
        for target in targets:
            if executor.run(target):
                any_executed = True

        if not any_executed and not self.args.quiet:
            print("Nothing to do (all targets up to date).")

    def _cmd_redo(self, target: str, only: bool) -> None:
        """Force re-run a target and optionally its dependents."""
        assert self.args is not None

        found_task = self.registry.find_target(target)
        if not found_task:
            print(f"Error: Unknown target: {target}", file=sys.stderr)
            sys.exit(1)

        resolver = DependencyResolver(self.registry)

        if only:
            # Only redo this one task
            executor = Executor(
                self.registry,
                parallel=False,
                force=True,
                verbose=not self.args.quiet,
            )
            # First, run dependencies (not forced) so inputs are ready
            deps = resolver.resolve(found_task)
            for dep in deps:
                if dep.name != found_task.name:
                    executor.force = False
                    executor._execute_task(dep)

            # Then force-run the target
            executor.force = True
            executed = executor._execute_task(found_task)
            if not executed and not self.args.quiet:
                print(f"Warning: {found_task.name} was skipped (run_if condition).")
        else:
            # Redo target and all dependents
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

            # Then add all dependents (we need to resolve each one to get proper order)
            for dep_name in dependent_names:
                if dep_name not in seen:
                    dep_task = self.registry.get(dep_name)
                    if dep_task:
                        tasks_to_run.append(dep_task)
                        seen.add(dep_name)

            executor = Executor(
                self.registry,
                parallel=self.parallel,
                max_workers=self.args.jobs,
                force=False,  # We'll selectively force
                verbose=not self.args.quiet,
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

            if not any_executed and not self.args.quiet:
                print("Nothing to do.")

    def _cmd_help(self) -> None:
        """Show help."""
        assert self.parser is not None
        self.parser.print_help()


def main(argv: list[str] | None = None) -> NoReturn:
    """Main entry point."""
    CLI(argv).run()


if __name__ == "__main__":
    main()
