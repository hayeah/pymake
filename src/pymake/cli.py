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
from .task import TaskRegistry, task


def load_makefile(path: Path) -> None:
    """Load and execute a Makefile.py."""
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


def cmd_list(registry: TaskRegistry, all_tasks: bool) -> None:
    """List registered tasks."""
    tasks = registry.all_tasks()

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

    default_name = registry.get_default()

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


def cmd_graph(registry: TaskRegistry, target: str) -> None:
    """Generate a DOT graph for a target."""
    task = registry.find_target(target)
    if not task:
        print(f"Error: Unknown target: {target}", file=sys.stderr)
        sys.exit(1)

    resolver = DependencyResolver(registry)
    dot = resolver.to_dot(task)
    print(dot)


def cmd_run(
    registry: TaskRegistry,
    targets: list[str],
    *,
    parallel: bool,
    jobs: int | None,
    force: bool,
    quiet: bool,
) -> None:
    """Run specified targets."""
    if not targets:
        print("Error: No targets specified", file=sys.stderr)
        sys.exit(1)

    executor = Executor(
        registry,
        parallel=parallel,
        max_workers=jobs,
        force=force,
        verbose=not quiet,
    )

    try:
        any_executed = False
        for target in targets:
            if executor.run(target):
                any_executed = True

        if not any_executed and not quiet:
            print("Nothing to do (all targets up to date).")

    except CyclicDependencyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except UnproducibleInputError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except MissingInputError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except MissingOutputError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ExecutionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_help(parser: argparse.ArgumentParser) -> None:
    """Show help."""
    parser.print_help()


def main(argv: list[str] | None = None) -> NoReturn:
    """Main entry point."""
    if argv is None:
        argv = sys.argv[1:]

    # Known subcommands
    subcommands = {"list", "graph", "run", "help"}

    # Check if first non-option arg is a subcommand
    # If not, treat all non-option trailing args as targets
    first_positional = None
    for i, arg in enumerate(argv):
        if not arg.startswith("-"):
            # Skip value for options that take arguments
            if i > 0 and argv[i - 1] in ("-f", "--file", "-j", "--jobs"):
                continue
            first_positional = arg
            break

    # If first positional is not a known subcommand, treat it as a target
    is_target_mode = (
        first_positional is not None and first_positional not in subcommands
    )

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

    if is_target_mode:
        # Parse targets directly
        parser.add_argument("targets", nargs="+", help="Targets to run")
        args = parser.parse_args(argv)

        # Clear and load the Makefile
        task.clear()
        load_makefile(Path(args.file))

        # Handle -j implying parallel
        parallel = args.parallel or args.jobs is not None

        cmd_run(
            task,
            args.targets,
            parallel=parallel,
            jobs=args.jobs,
            force=args.force,
            quiet=args.quiet,
        )
        sys.exit(0)

    # Subcommand mode
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

    # help command
    subparsers.add_parser("help", help="Show help")

    args = parser.parse_args(argv)

    # Clear and load the Makefile
    task.clear()
    load_makefile(Path(args.file))

    # Handle -j implying parallel
    parallel = args.parallel or args.jobs is not None

    # Dispatch commands
    if args.command == "list":
        cmd_list(task, args.all_tasks)
        sys.exit(0)

    elif args.command == "graph":
        cmd_graph(task, args.target)
        sys.exit(0)

    elif args.command == "run":
        cmd_run(
            task,
            args.targets,
            parallel=parallel,
            jobs=args.jobs,
            force=args.force,
            quiet=args.quiet,
        )
        sys.exit(0)

    elif args.command == "help":
        cmd_help(parser)
        sys.exit(0)

    else:
        # No command - try default task or show help
        default_target = task.get_default()
        if default_target:
            cmd_run(
                task,
                [default_target],
                parallel=parallel,
                jobs=args.jobs,
                force=args.force,
                quiet=args.quiet,
            )
            sys.exit(0)
        cmd_help(parser)
        sys.exit(0)


if __name__ == "__main__":
    main()
