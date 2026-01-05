"""Command-line interface for pymake."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from .executor import (
    ExecutionError,
    Executor,
    MissingInputError,
    MissingOutputError,
    UnproducibleInputError,
)
from .resolver import CyclicDependencyError, DependencyResolver
from .task import Task, TaskRegistry, task


class CLI:
    """Command-line interface handler for pymake."""

    SUBCOMMANDS = {"list", "graph", "run", "which", "help"}

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
        if self._is_target_mode():
            self._run_target_mode()
        else:
            self._run_subcommand_mode()

    def _is_target_mode(self) -> bool:
        """Check if first positional arg is a target (not a subcommand)."""
        for i, arg in enumerate(self.argv):
            if not arg.startswith("-"):
                # Skip value for options that take arguments
                if i > 0 and self.argv[i - 1] in ("-f", "--file", "-j", "--jobs"):
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
            "which", help="Show reverse dependency tree for an output"
        )
        which_parser.add_argument("output", help="Output file to trace")

        # help command
        subparsers.add_parser("help", help="Show help")

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

        self.registry.clear()
        self._load_makefile()
        self._cmd_run(self.args.targets)
        sys.exit(0)

    def _run_subcommand_mode(self) -> NoReturn:
        """Handle subcommand execution (e.g., `pymake list`)."""
        self.parser = self._build_base_parser()
        self._add_subparsers(self.parser)
        self.args = self.parser.parse_args(self.argv)

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
            self._cmd_which(self.args.output)
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

    def _cmd_which(self, output: str) -> None:
        """Show reverse dependency tree for an output file."""
        output_path = Path(output)
        found_task = self.registry.by_output(output_path)

        if not found_task:
            print(f"Error: No task produces '{output}'", file=sys.stderr)
            sys.exit(1)

        resolver = DependencyResolver(self.registry)
        printed: set[str] = set()

        def print_tree(t: Task, prefix: str = "", is_last: bool = True) -> None:
            if t.name in printed:
                return

            printed.add(t.name)
            connector = "└── " if is_last else "├── "
            print(f"{prefix}{connector}{t.name}")

            child_prefix = prefix + ("    " if is_last else "│   ")
            deps = resolver.dependencies(t)

            # Filter deps, accounting for what each subtree will cover
            printable_deps = []
            covered: set[str] = set()
            for dep in deps:
                if dep.name not in printed and dep.name not in covered:
                    printable_deps.append(dep)
                    covered |= resolver.transitive_deps(dep)

            has_deps = len(printable_deps) > 0

            # Inputs (←)
            for inp in t.inputs:
                vert = "│" if has_deps else " "
                print(f"{child_prefix}{vert} ← {inp}")

            # Outputs (→)
            for out in t.outputs:
                vert = "│" if has_deps else " "
                print(f"{child_prefix}{vert} → {out}")

            # Dependencies (recursive)
            for i, dep in enumerate(printable_deps):
                print_tree(dep, child_prefix, i == len(printable_deps) - 1)

        # Print the output file first, then the producing task
        print(output_path)
        print_tree(found_task)

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

        try:
            any_executed = False
            for target in targets:
                if executor.run(target):
                    any_executed = True

            if not any_executed and not self.args.quiet:
                print("Nothing to do (all targets up to date).")

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

    def _cmd_help(self) -> None:
        """Show help."""
        assert self.parser is not None
        self.parser.print_help()


def main(argv: list[str] | None = None) -> NoReturn:
    """Main entry point."""
    CLI(argv).run()


if __name__ == "__main__":
    main()
