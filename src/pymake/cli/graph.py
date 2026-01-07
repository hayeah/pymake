"""Graph command for pymake CLI."""

from __future__ import annotations

import argparse

from .context import CommandContext


class GraphCommand:
    """Generate DOT graph for a target."""

    def __init__(self, ctx: CommandContext) -> None:
        self.ctx = ctx

    @staticmethod
    def add_arguments(subparsers: argparse._SubParsersAction) -> None:
        """Add graph command arguments."""
        parser = subparsers.add_parser("graph", help="Generate DOT graph for a target")
        parser.add_argument("target", help="Target to graph")

    def execute(self) -> None:
        """Generate and print DOT graph."""
        found_task = self.ctx.find_target(self.ctx.args.target)
        dot = self.ctx.resolver.to_dot(found_task)
        print(dot)
