"""Command-line interface for pymake."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import NoReturn

from ..executor import ExecutionError, MissingOutputError
from ..task import task
from .clean import CleanCommand
from .context import CommandContext
from .doctor import DoctorCommand
from .graph import GraphCommand
from .list_cmd import ListCommand
from .redo import RedoCommand
from .run import RunCommand
from .which import WhichCommand

# Map command names to their handler classes
COMMANDS = {
    "list": ListCommand,
    "graph": GraphCommand,
    "run": RunCommand,
    "which": WhichCommand,
    "redo": RedoCommand,
    "doctor": DoctorCommand,
    "clean": CleanCommand,
}


class CLI:
    """Command-line interface handler for pymake."""

    SUBCOMMANDS = {"list", "graph", "run", "which", "redo", "doctor", "clean", "help"}

    def __init__(self, argv: list[str] | None = None) -> None:
        self.argv = argv if argv is not None else sys.argv[1:]
        self.registry = task
        self.parser: argparse.ArgumentParser | None = None
        self.args: argparse.Namespace | None = None

    def run(self) -> NoReturn:
        """Main entry point - parse args and dispatch to appropriate command."""
        try:
            if self._is_target_mode():
                self._run_target_mode()
            else:
                self._run_subcommand_mode()
        except (MissingOutputError, ExecutionError, ValueError) as e:
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

        # Add arguments from each command class
        for command_cls in COMMANDS.values():
            command_cls.add_arguments(subparsers)

        # help command (simple, no class needed)
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

        # Use RunCommand for target mode
        ctx = CommandContext(self.registry, self.args)
        RunCommand(ctx).execute()
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
        ctx = CommandContext(self.registry, self.args)

        # Look up command class in dispatch table
        command_cls = COMMANDS.get(command)
        if command_cls:
            command_cls(ctx).execute()
        elif command == "help":
            self.parser.print_help()
        else:
            # No command - try default task or show help
            default_target = self.registry.default_task()
            if default_target:
                # Create args with targets for RunCommand
                self.args.targets = [default_target]
                RunCommand(ctx).execute()
            else:
                self.parser.print_help()

        sys.exit(0)


def main(argv: list[str] | None = None) -> NoReturn:
    """Main entry point."""
    CLI(argv).run()


if __name__ == "__main__":
    main()
